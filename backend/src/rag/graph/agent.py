"""Grafo agéntico (LangGraph):

    analyze ─▶ retrieve ─▶ grade ─┬─▶ generate ─▶ verify ─▶ END
                   ▲              │
                   └── rewrite ◀──┤  (sin docs relevantes, hasta N reintentos)
                                  └─▶ no_context ─▶ END  (reintentos agotados)

Diseño para modelos locales chicos (4B):
- Los pasos de control (analyze/grade/rewrite/verify) usan format=json de
  Ollama + parser tolerante: si el modelo responde mal, hay fallback seguro
  (grade → relevante, verify → no verificado) en vez de romper el flujo.
- La generación cita fragmentos con [n]; el mapeo n→fuente viaja en el
  estado y la API lo expone como `sources`.
"""

from __future__ import annotations

import asyncio
import logging
import re

import orjson
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph

from ..config import Settings
from ..llm import build_llm
from ..retrieval.store import HybridStore
from ..schemas import RetrievedChunk
from ..textnorm import find_third_parties, html_tables_to_markdown
from . import prompts
from .state import AgentState

log = logging.getLogger("rag.agent")

MAX_HISTORY_MESSAGES = 8

# Techo de afirmaciones verificadas por respuesta: acota el costo del
# verificador (una llamada por fragmento citado) y el tamaño del informe.
MAX_CLAIMS = 25
# Un chunk son ~3200 caracteres; este techo solo protege de un chunk anómalo.
VERIFY_FRAGMENT_CHARS = 6000


def parse_json_reply(text: str) -> dict:
    """Parser tolerante para respuestas JSON de modelos chicos."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.removeprefix("json").strip()
    try:
        data = orjson.loads(text)
        return data if isinstance(data, dict) else {}
    except orjson.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if 0 <= start < end:
            try:
                data = orjson.loads(text[start : end + 1])
                return data if isinstance(data, dict) else {}
            except orjson.JSONDecodeError:
                pass
    return {}


_CITATION_MARKER_RE = re.compile(r"\[(\d{1,2})\]")


def strip_ghost_citations(answer: str, n_sources: int) -> str:
    """Borra marcadores [n] que no correspondan a ninguna fuente entregada.

    Los modelos pequeños a veces copian el patrón del ejemplo del prompt y
    citan [2] o [3] con una sola fuente en el contexto; el panel mostraba
    "Fuentes: 1" y la respuesta prometía tres. La afirmación queda intacta,
    solo cae el marcador falso.
    """

    def _keep_or_drop(m: re.Match[str]) -> str:
        n = int(m.group(1))
        return m.group(0) if 1 <= n <= n_sources else ""

    return _CITATION_MARKER_RE.sub(_keep_or_drop, answer)


def describe_chunk(d: RetrievedChunk) -> str:
    """Identificación legible de un fragmento: tipo, partes y fecha."""
    desc = d.tipo or "documento"
    if d.usuario_libre:
        desc += f" de {d.usuario_libre}"
    if d.suministrador:
        desc += f" con {d.suministrador}"
    if d.fecha_suscripcion:
        desc += f", suscrito el {d.fecha_suscripcion}"
    return desc


def attribution_warning(d: RetrievedChunk) -> str:
    """Advertencia cuando el fragmento nombra empresas ajenas al documento.

    El caso real que motivó esto: el contrato de LA ARENA con Pluz transcribe
    en su cláusula primera las tablas de potencia de los "Contratos
    Primigenios" con Orygen y Celepsa. La cabecera del fragmento dice "con
    Pluz Energía", y el modelo presentaba la tabla de Celepsa como potencia
    contratada de Pluz — cifras correctas, empresa equivocada, que es la peor
    clase de error que puede cometer este producto.
    """
    terceros = find_third_parties(d.text, [d.suministrador, d.usuario_libre])
    if not terceros:
        return ""
    propio = d.suministrador or "el suministrador de este documento"
    return (
        f"\n⚠ ADVERTENCIA DE ATRIBUCIÓN: este fragmento MENCIONA a terceros "
        f"({'; '.join(terceros)}). Las cifras, tablas y cláusulas que el texto "
        f"asigne a ellos NO son de {propio}. Localiza en el texto la frase que "
        f"introduce cada dato antes de atribuirlo."
    )


def format_context(docs: list[RetrievedChunk]) -> str:
    """Cada fragmento entra con su metadata en la cabecera.

    Sin esto el modelo respondía "no existe información sobre las fechas de
    suscripción" con las fechas a un campo de distancia: estaban en el payload
    y en el panel de fuentes, pero jamás llegaban al prompt.

    Las tablas llegan como HTML del OCR y se convierten a Markdown: el modelo
    las lee mejor y son las mismas que ve el usuario en el panel de fuentes.
    """
    parts = []
    for i, d in enumerate(docs, start=1):
        page = ""
        if d.page_start:
            page = f", pág. {d.page_start}" + (
                f"-{d.page_end}" if d.page_end and d.page_end != d.page_start else ""
            )
        header = f"[{i}] {describe_chunk(d)} ({d.source_file}{page})"
        if d.section:
            header += f" — {d.section}"
        parts.append(f"{header}{attribution_warning(d)}\n{html_tables_to_markdown(d.text)}")
    return "\n\n---\n\n".join(parts)


def truncate_context(docs: list[RetrievedChunk], budget: int) -> str:
    """Contexto para el verificador, recortando *cada* fuente por igual.

    Antes se hacía `format_context(docs)[:12000]`, un corte plano al final:
    con top_k=6 y chunks de 3200 caracteres, las fuentes [5] y [6] quedaban
    fuera enteras. El verificador veía una respuesta que citaba hechos
    ausentes de su contexto y devolvía `grounded: false`, así que la insignia
    "⚠ Verificación no concluyente" aparecía justamente en las respuestas
    bien citadas de varias fuentes.
    """
    if not docs:
        return ""
    per_doc = max(budget // len(docs), 200)
    trimmed = [
        d.model_copy(update={"text": d.text[:per_doc]}) if len(d.text) > per_doc else d
        for d in docs
    ]
    return format_context(trimmed)


def format_history(history: list[dict]) -> str:
    recent = history[-MAX_HISTORY_MESSAGES:]
    if not recent:
        return "(sin historial)"
    return "\n".join(f"{m.get('role', '?')}: {m.get('content', '')[:500]}" for m in recent)


class ContractsAgent:
    def __init__(
        self,
        settings: Settings,
        store: HybridStore,
        *,
        llm_json=None,
        llm_generate=None,
    ) -> None:
        """Los modelos son inyectables: sin esa costura, ningún nodo del grafo
        podía testearse sin un Ollama vivo (y ninguno lo estaba)."""
        self.settings = settings
        self.store = store
        self.llm_json = (
            llm_json
            if llm_json is not None
            else build_llm(settings, json_mode=True, temperature=0.0)
        )
        self.llm_generate = llm_generate if llm_generate is not None else build_llm(settings)
        self.graph = self._build()

    # --- Nodos ---

    async def analyze(self, state: AgentState) -> dict:
        question = state["question"]
        reply = await self.llm_json.ainvoke(
            [
                HumanMessage(
                    content=prompts.ANALYZE_PROMPT.format(
                        history=format_history(state.get("history", [])),
                        question=question,
                    )
                )
            ]
        )
        data = parse_json_reply(str(reply.content))
        scope = str(data.get("alcance") or "contratos")
        if scope not in ("contratos", "fuera_de_tema", "extraccion_masiva"):
            scope = "contratos"
        search_query = str(data.get("search_query") or "").strip() or question
        extracted = data.get("filters") if isinstance(data.get("filters"), dict) else {}
        filters = {k: v for k, v in extracted.items() if v}
        # Los filtros explícitos del usuario (UI) mandan sobre los extraídos
        filters.update(state.get("user_filters") or {})

        # ¿Piden varios documentos distintos? ("5 contratos de Orygen",
        # "compara dos contratos"). Activa el retrieval con diversidad por
        # doc_id; acotado a 8 para no reventar el contexto del generador.
        num_docs = None
        try:
            n = int(data.get("num_docs") or 0)
            if n >= 2:
                num_docs = min(n, 8)
        except (TypeError, ValueError):
            pass

        # Superlativo temporal: "el contrato más reciente" no se resuelve por
        # similitud — se busca el máximo de fecha_suscripcion en la metadata
        # (respetando los filtros) y se acota el retrieval a ese documento.
        resolved_doc = None
        selector_note = None
        orden = data.get("orden")
        if orden in ("mas_reciente", "mas_antiguo") and "tipo" not in filters:
            # Cinturón determinista sobre la guía del prompt: "el CONTRATO más
            # reciente" debe resolver un contrato, no la adenda más nueva.
            # \bcontrato\b no matchea "contratada" (palabra distinta).
            q = question.lower()
            pide_contrato = re.search(r"\bcontratos?\b", q)
            pide_adenda = re.search(r"\badendas?\b", q)
            if pide_contrato and not pide_adenda:
                filters["tipo"] = "contrato"
            elif pide_adenda and not pide_contrato:
                filters["tipo"] = "adenda"
        if scope == "contratos" and orden in ("mas_reciente", "mas_antiguo"):
            extreme_docs = await asyncio.to_thread(
                lambda: self.store.find_extreme_docs(
                    filters or None, latest=orden == "mas_reciente", n=num_docs or 1
                )
            )
            adjetivo = "reciente(s)" if orden == "mas_reciente" else "antiguo(s)"
            if len(extreme_docs) == 1:
                resolved_doc = extreme_docs[0]
                filters = dict(filters)
                filters["doc_id"] = resolved_doc["doc_id"]
                selector_note = (
                    f"\nNOTA DEL SISTEMA: el documento más {adjetivo} del índice que "
                    f"coincide con la pregunta es un(a) {resolved_doc.get('tipo') or 'documento'} "
                    f"de {resolved_doc.get('usuario_libre') or '¿?'} suscrito el "
                    f"{resolved_doc.get('fecha_suscripcion') or '¿?'}. Preséntalo con esa "
                    "naturaleza exacta; si es una adenda, aclara que modifica un contrato "
                    "anterior y no la llames 'el contrato'.\n"
                )
            elif extreme_docs:
                # "Los N contratos más recientes": el retrieval se acota a esos
                # N doc_id exactos y el generador recibe la lista resuelta.
                filters = dict(filters)
                filters["doc_id"] = [d["doc_id"] for d in extreme_docs]
                listado = "; ".join(
                    f"{d.get('tipo') or 'documento'} de {d.get('usuario_libre') or '¿?'} "
                    f"suscrito el {d.get('fecha_suscripcion') or '¿?'}"
                    for d in extreme_docs
                )
                selector_note = (
                    f"\nNOTA DEL SISTEMA: los {len(extreme_docs)} documentos más "
                    f"{adjetivo} del índice que coinciden con la pregunta son: {listado}. "
                    "Presenta cada uno con su naturaleza exacta (contrato o adenda).\n"
                )
        # Ni la pregunta ni los RUC van al log: `docker logs` acababa siendo
        # un registro consultable de qué analista preguntó por qué empresa.
        log.info(
            "analyze: alcance=%s, %d filtros, orden=%s, num_docs=%s",
            scope,
            len(filters),
            orden or "-",
            num_docs or "-",
        )
        return {
            "search_query": search_query,
            "filters": filters,
            "scope": scope,
            "num_docs": num_docs,
            "resolved_doc": resolved_doc,
            "selector_note": selector_note,
            "rewrites": state.get("rewrites", 0),
        }

    async def retrieve(self, state: AgentState) -> dict:
        # `HybridStore.search` es síncrono (httpx.Client + qdrant_client) y se
        # llamaba directamente desde este `async def`: una llamada lenta a
        # Ollama bloqueaba el event loop entero, congelando los SSE de todos
        # los demás usuarios y el propio /api/health.
        num_docs = state.get("num_docs") or 0
        filters = state.get("filters") or None
        if num_docs >= 2:
            docs = await asyncio.to_thread(
                lambda: self.store.search_diverse(
                    state["search_query"], n_docs=num_docs, per_doc=2, filters=filters
                )
            )
            if not docs and filters and not state.get("user_filters"):
                # Mismo fallback que abajo: un filtro mal extraído no debe
                # dejar al usuario sin respuesta.
                log.info("retrieve diverso: 0 docs con filtros, reintento sin filtros")
                filters = None
                docs = await asyncio.to_thread(
                    lambda: self.store.search_diverse(
                        state["search_query"], n_docs=num_docs, per_doc=2, filters=None
                    )
                )
            distinct = len({d.doc_id for d in docs})
            total = await asyncio.to_thread(self.store.count_distinct_docs, filters)
            note = prompts.MULTI_DOC_NOTE.format(
                pedidos=num_docs, en_contexto=distinct, en_indice=total
            )
            log.info("retrieve diverso: %d docs distintos de %d en índice", distinct, total)
            return {"documents": docs, "multi_doc_note": note}
        docs = await asyncio.to_thread(
            self.store.search,
            state["search_query"],
            top_k=self.settings.top_k,
            filters=filters,
        )
        # Si el filtro extraído automáticamente dejó 0 resultados, reintenta sin él
        # (un RUC mal extraído no debe dejar al usuario sin respuesta)
        if (
            not docs
            and state.get("filters")
            and not state.get("user_filters")
            and not state.get("resolved_doc")
        ):
            log.info("retrieve: 0 docs con filtros, reintento sin filtros")
            docs = await asyncio.to_thread(
                self.store.search, state["search_query"], top_k=self.settings.top_k
            )
        return {"documents": docs}

    async def grade(self, state: AgentState) -> dict:
        docs = state.get("documents", [])
        if (state.get("num_docs") or 0) >= 2 and docs:
            # En modo multi-documento el grading por fragmento es
            # contraproducente: "dame 5 contratos de Atria" hacía que el
            # evaluador rechazara CADA portada por no responder sola la
            # pregunta completa (0/8 relevantes → rewrite → misma poda).
            # Los filtros de metadata ya seleccionaron los documentos.
            log.info("grade: omitido en modo multi-documento (%d fragmentos)", len(docs))
            return {"relevant_documents": docs}

        async def _grade_one(doc: RetrievedChunk) -> bool:
            reply = await self.llm_json.ainvoke(
                [
                    HumanMessage(
                        content=prompts.GRADE_PROMPT.format(
                            question=state["question"],
                            source_file=doc.source_file,
                            document=doc.text[:2500],
                        )
                    )
                ]
            )
            data = parse_json_reply(str(reply.content))
            # Fallback permisivo: ante duda del grader, el generador decide
            return bool(data.get("relevant", True))

        # En paralelo: en serie, 12 fragmentos del modo multi-documento eran
        # 12 viajes al LLM uno detrás de otro (~20 s solo de grading).
        verdicts = await asyncio.gather(*(_grade_one(d) for d in docs))
        relevant = [d for d, ok in zip(docs, verdicts, strict=True) if ok]
        log.info("grade: %d/%d relevantes", len(relevant), len(docs))
        return {"relevant_documents": relevant}

    async def rewrite(self, state: AgentState) -> dict:
        reply = await self.llm_json.ainvoke(
            [
                HumanMessage(
                    content=prompts.REWRITE_PROMPT.format(
                        search_query=state["search_query"],
                        question=state["question"],
                    )
                )
            ]
        )
        data = parse_json_reply(str(reply.content))
        new_query = str(data.get("search_query") or "").strip() or state["search_query"]
        log.info("rewrite #%d", state.get("rewrites", 0) + 1)
        return {"search_query": new_query, "rewrites": state.get("rewrites", 0) + 1}

    async def generate(self, state: AgentState) -> dict:
        docs = state["relevant_documents"]
        messages = [
            SystemMessage(content=prompts.GENERATE_SYSTEM),
            *self._history_messages(state.get("history", [])),
            HumanMessage(
                content=prompts.GENERATE_USER.format(
                    context=format_context(docs),
                    question=state["question"],
                    selector_note=(state.get("selector_note") or "")
                    + (state.get("multi_doc_note") or ""),
                )
            ),
        ]
        reply = await self.llm_generate.ainvoke(messages)
        return {"answer": strip_ghost_citations(str(reply.content), len(docs))}

    async def _extract_claims(self, answer: str, n_docs: int) -> list[dict]:
        """Descompone la respuesta en afirmaciones atómicas con su cita."""
        reply = await self.llm_json.ainvoke(
            [HumanMessage(content=prompts.EXTRACT_CLAIMS_PROMPT.format(answer=answer))]
        )
        data = parse_json_reply(str(reply.content))
        raw = data.get("afirmaciones")
        if not isinstance(raw, list):
            return []
        claims: list[dict] = []
        for item in raw[:MAX_CLAIMS]:
            if not isinstance(item, dict):
                continue
            texto = str(item.get("texto") or "").strip()
            if not texto:
                continue
            citas = []
            for c in item.get("citas") or []:
                try:
                    n = int(c)
                except (TypeError, ValueError):
                    continue
                # Una cita fuera de rango es una cita inventada: se descarta,
                # y la afirmación queda como "sin_cita".
                if 1 <= n <= n_docs and n not in citas:
                    citas.append(n)
            claims.append({"texto": texto, "citas": citas})
        return claims

    async def _refute(self, n: int, doc: RetrievedChunk, group: list[tuple[int, dict]]) -> dict:
        """Somete a refutación las afirmaciones que citan el fragmento `n`.

        El verificador ve ÚNICAMENTE ese fragmento. Con el contexto completo
        delante bastaba que la cifra apareciera en cualquier documento para
        darla por buena, que es exactamente como una tabla de Celepsa acabó
        certificada como potencia contratada de Pluz.
        """
        listado = "\n".join(f"{i}. {c['texto']}" for i, (_, c) in enumerate(group, start=1))
        reply = await self.llm_json.ainvoke(
            [
                HumanMessage(
                    content=prompts.REFUTE_PROMPT.format(
                        n=n,
                        desc=describe_chunk(doc),
                        fragment=html_tables_to_markdown(doc.text)[:VERIFY_FRAGMENT_CHARS],
                        claims=listado,
                    )
                )
            ]
        )
        data = parse_json_reply(str(reply.content))
        out: dict[int, tuple[str, str]] = {}
        for v in data.get("veredictos") or []:
            if not isinstance(v, dict):
                continue
            try:
                local = int(v.get("i"))
            except (TypeError, ValueError):
                continue
            estado = str(v.get("estado") or "").lower().strip()
            if estado not in prompts.CLAIM_STATES or not 1 <= local <= len(group):
                continue
            claim_idx = group[local - 1][0]
            out[claim_idx] = (estado, str(v.get("motivo") or "")[:120])
        return out

    async def verify(self, state: AgentState) -> dict:
        """Verificación adversaria por afirmación.

        El verificador anterior era una sola pregunta binaria sobre la
        respuesta entera ("¿está todo sustentado?"), con el contexto completo
        a la vista y formulada para decir que sí. Una respuesta de veinte datos
        con un solo dato mal atribuido devolvía `grounded: true`, y la insignia
        verde acababa CERTIFICANDO el error. Ahora cada afirmación se refuta
        por separado contra el fragmento que ella misma cita.
        """
        docs = state.get("relevant_documents") or []
        answer = str(state.get("answer") or "")
        empty = {"grounded": None, "claims_total": 0, "claims_ok": 0, "claim_issues": []}
        if not docs or not answer.strip():
            return empty
        try:
            claims = await self._extract_claims(answer, len(docs))
        except Exception as e:  # noqa: BLE001 — la verificación nunca tumba la respuesta
            log.warning("verify: extracción de afirmaciones falló: %s", e)
            return empty
        if not claims:
            return empty

        por_fragmento: dict[int, list[tuple[int, dict]]] = {}
        for idx, c in enumerate(claims):
            for n in c["citas"]:
                por_fragmento.setdefault(n, []).append((idx, c))

        veredictos: dict[int, list[tuple[str, str]]] = {}
        if por_fragmento:
            resultados = await asyncio.gather(
                *(self._refute(n, docs[n - 1], grupo) for n, grupo in por_fragmento.items()),
                return_exceptions=True,
            )
            for r in resultados:
                if isinstance(r, BaseException):
                    log.warning("verify: refutación de un fragmento falló: %s", r)
                    continue
                for claim_idx, verdict in r.items():
                    veredictos.setdefault(claim_idx, []).append(verdict)

        issues: list[dict] = []
        ok = 0
        for idx, c in enumerate(claims):
            estados = veredictos.get(idx, [])
            if not c["citas"]:
                # Afirmación fáctica sin [n]: la regla 2 la prohíbe y no hay
                # nada contra lo que contrastarla.
                issues.append({"texto": c["texto"], "estado": "sin_cita", "motivo": ""})
                continue
            if not estados:
                issues.append({"texto": c["texto"], "estado": "ausente", "motivo": "sin veredicto"})
                continue
            # Basta que UNO de los fragmentos citados la sustente.
            sustentada = next((m for e, m in estados if e == "sustentada"), None)
            if sustentada is not None:
                ok += 1
                continue
            refutada = next(((e, m) for e, m in estados if e == "refutada"), None)
            estado, motivo = refutada if refutada else estados[0]
            issues.append({"texto": c["texto"], "estado": estado, "motivo": motivo})

        if ok == len(claims):
            grounded: bool | None = True
        elif any(i["estado"] == "refutada" for i in issues):
            grounded = False
        else:
            # Ni contradichas ni sustentadas: verificación no concluyente.
            grounded = None
        log.info(
            "verify: %d/%d afirmaciones sustentadas, %d con reparo",
            ok,
            len(claims),
            len(issues),
        )
        return {
            "grounded": grounded,
            "claims_total": len(claims),
            "claims_ok": ok,
            "claim_issues": issues[:MAX_CLAIMS],
        }

    async def no_context(self, state: AgentState) -> dict:
        return {
            "answer": prompts.NO_CONTEXT_ANSWER,
            "no_context": True,
            "relevant_documents": [],
            "grounded": True,
        }

    # --- Aristas condicionales ---

    def after_grade(self, state: AgentState) -> str:
        if state.get("relevant_documents"):
            return "generate"
        if state.get("rewrites", 0) < self.settings.max_query_rewrites:
            return "rewrite"
        return "no_context"

    # --- Construcción ---

    def _history_messages(self, history: list[dict]) -> list:
        msgs = []
        for m in history[-MAX_HISTORY_MESSAGES:]:
            content = str(m.get("content", ""))[:2000]
            if m.get("role") == "user":
                msgs.append(HumanMessage(content=content))
            elif m.get("role") == "assistant":
                msgs.append(AIMessage(content=content))
        return msgs

    async def refuse(self, state: AgentState) -> dict:
        """Corta ANTES del retrieval: ni búsqueda ni generación para consultas
        fuera de alcance o de extracción masiva. También ahorra tokens del
        proveedor de nube cuando está activo."""
        answer = (
            prompts.BULK_EXTRACTION_ANSWER
            if state.get("scope") == "extraccion_masiva"
            else prompts.OUT_OF_SCOPE_ANSWER
        )
        return {"answer": answer, "grounded": None, "no_context": False}

    def _build(self):
        g = StateGraph(AgentState)
        g.add_node("analyze", self.analyze)
        g.add_node("retrieve", self.retrieve)
        g.add_node("grade", self.grade)
        g.add_node("rewrite", self.rewrite)
        g.add_node("generate", self.generate)
        g.add_node("verify", self.verify)
        g.add_node("no_context", self.no_context)
        g.add_node("refuse", self.refuse)

        g.set_entry_point("analyze")
        g.add_conditional_edges(
            "analyze",
            lambda st: "retrieve" if st.get("scope", "contratos") == "contratos" else "refuse",
            {"retrieve": "retrieve", "refuse": "refuse"},
        )
        g.add_edge("refuse", END)
        g.add_edge("retrieve", "grade")
        g.add_conditional_edges(
            "grade",
            self.after_grade,
            {"generate": "generate", "rewrite": "rewrite", "no_context": "no_context"},
        )
        g.add_edge("rewrite", "retrieve")
        g.add_edge("generate", "verify")
        g.add_edge("verify", END)
        g.add_edge("no_context", END)
        return g.compile()
