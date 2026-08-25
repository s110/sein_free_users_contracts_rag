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
from . import prompts
from .state import AgentState

log = logging.getLogger("rag.agent")

MAX_HISTORY_MESSAGES = 8


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


def format_context(docs: list[RetrievedChunk]) -> str:
    """Cada fragmento entra con su metadata en la cabecera.

    Sin esto el modelo respondía "no existe información sobre las fechas de
    suscripción" con las fechas a un campo de distancia: estaban en el payload
    y en el panel de fuentes, pero jamás llegaban al prompt.
    """
    parts = []
    for i, d in enumerate(docs, start=1):
        page = ""
        if d.page_start:
            page = f", pág. {d.page_start}" + (
                f"-{d.page_end}" if d.page_end and d.page_end != d.page_start else ""
            )
        desc = d.tipo or "documento"
        if d.usuario_libre:
            desc += f" de {d.usuario_libre}"
        if d.suministrador:
            desc += f" con {d.suministrador}"
        if d.fecha_suscripcion:
            desc += f", suscrito el {d.fecha_suscripcion}"
        header = f"[{i}] {desc} ({d.source_file}{page})"
        if d.section:
            header += f" — {d.section}"
        parts.append(f"{header}\n{d.text}")
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
            resolved_doc = await asyncio.to_thread(
                self.store.find_extreme_doc, filters or None, latest=orden == "mas_reciente"
            )
            if resolved_doc and resolved_doc.get("doc_id"):
                filters = dict(filters)
                filters["doc_id"] = resolved_doc["doc_id"]
                adjetivo = "reciente" if orden == "mas_reciente" else "antiguo"
                selector_note = (
                    f"\nNOTA DEL SISTEMA: el documento más {adjetivo} del índice que "
                    f"coincide con la pregunta es un(a) {resolved_doc.get('tipo') or 'documento'} "
                    f"de {resolved_doc.get('usuario_libre') or '¿?'} suscrito el "
                    f"{resolved_doc.get('fecha_suscripcion') or '¿?'}. Preséntalo con esa "
                    "naturaleza exacta; si es una adenda, aclara que modifica un contrato "
                    "anterior y no la llames 'el contrato'.\n"
                )
        # Ni la pregunta ni los RUC van al log: `docker logs` acababa siendo
        # un registro consultable de qué analista preguntó por qué empresa.
        log.info(
            "analyze: alcance=%s, %d filtros, orden=%s", scope, len(filters), orden or "-"
        )
        return {
            "search_query": search_query,
            "filters": filters,
            "scope": scope,
            "resolved_doc": resolved_doc,
            "selector_note": selector_note,
            "rewrites": state.get("rewrites", 0),
        }

    async def retrieve(self, state: AgentState) -> dict:
        # `HybridStore.search` es síncrono (httpx.Client + qdrant_client) y se
        # llamaba directamente desde este `async def`: una llamada lenta a
        # Ollama bloqueaba el event loop entero, congelando los SSE de todos
        # los demás usuarios y el propio /api/health.
        docs = await asyncio.to_thread(
            self.store.search,
            state["search_query"],
            top_k=self.settings.top_k,
            filters=state.get("filters") or None,
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
        relevant: list[RetrievedChunk] = []
        for doc in state.get("documents", []):
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
            if data.get("relevant", True):
                relevant.append(doc)
        log.info("grade: %d/%d relevantes", len(relevant), len(state.get("documents", [])))
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
                    selector_note=state.get("selector_note") or "",
                )
            ),
        ]
        reply = await self.llm_generate.ainvoke(messages)
        return {"answer": strip_ghost_citations(str(reply.content), len(docs))}

    async def verify(self, state: AgentState) -> dict:
        try:
            reply = await self.llm_json.ainvoke(
                [
                    HumanMessage(
                        content=prompts.GROUNDEDNESS_PROMPT.format(
                            context=truncate_context(
                                state["relevant_documents"], self.settings.verify_context_chars
                            ),
                            answer=state["answer"],
                        )
                    )
                ]
            )
            data = parse_json_reply(str(reply.content))
            grounded = data.get("grounded")
            grounded = bool(grounded) if isinstance(grounded, bool) else None
        except Exception as e:  # noqa: BLE001 — verificación es best-effort
            log.warning("verify falló: %s", e)
            grounded = None
        return {"grounded": grounded}

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
