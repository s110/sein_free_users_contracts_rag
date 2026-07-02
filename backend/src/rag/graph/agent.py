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

import logging

import orjson
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_ollama import ChatOllama
from langgraph.graph import END, StateGraph

from ..config import Settings
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


def format_context(docs: list[RetrievedChunk]) -> str:
    parts = []
    for i, d in enumerate(docs, start=1):
        page = ""
        if d.page_start:
            page = f", pág. {d.page_start}" + (
                f"-{d.page_end}" if d.page_end and d.page_end != d.page_start else ""
            )
        header = f"[{i}] {d.source_file}{page}"
        if d.section:
            header += f" — {d.section}"
        parts.append(f"{header}\n{d.text}")
    return "\n\n---\n\n".join(parts)


def format_history(history: list[dict]) -> str:
    recent = history[-MAX_HISTORY_MESSAGES:]
    if not recent:
        return "(sin historial)"
    return "\n".join(f"{m.get('role', '?')}: {m.get('content', '')[:500]}" for m in recent)


class ContractsAgent:
    def __init__(self, settings: Settings, store: HybridStore) -> None:
        self.settings = settings
        self.store = store
        self.llm_json = ChatOllama(
            base_url=settings.ollama_host,
            model=settings.llm_model,
            temperature=0.0,
            num_ctx=settings.llm_num_ctx,
            format="json",
        )
        self.llm_generate = ChatOllama(
            base_url=settings.ollama_host,
            model=settings.llm_model,
            temperature=settings.llm_temperature,
            num_ctx=settings.llm_num_ctx,
        )
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
        search_query = str(data.get("search_query") or "").strip() or question
        extracted = data.get("filters") if isinstance(data.get("filters"), dict) else {}
        filters = {k: v for k, v in extracted.items() if v}
        # Los filtros explícitos del usuario (UI) mandan sobre los extraídos
        filters.update(state.get("user_filters") or {})
        log.info("analyze: query='%s' filtros=%s", search_query[:100], filters)
        return {
            "search_query": search_query,
            "filters": filters,
            "rewrites": state.get("rewrites", 0),
        }

    async def retrieve(self, state: AgentState) -> dict:
        docs = self.store.search(
            state["search_query"],
            top_k=self.settings.top_k,
            filters=state.get("filters") or None,
        )
        # Si el filtro extraído automáticamente dejó 0 resultados, reintenta sin él
        # (un RUC mal extraído no debe dejar al usuario sin respuesta)
        if not docs and state.get("filters") and not state.get("user_filters"):
            log.info("retrieve: 0 docs con filtros %s, reintento sin filtros", state["filters"])
            docs = self.store.search(state["search_query"], top_k=self.settings.top_k)
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
        log.info("rewrite #%d: '%s'", state.get("rewrites", 0) + 1, new_query[:100])
        return {"search_query": new_query, "rewrites": state.get("rewrites", 0) + 1}

    async def generate(self, state: AgentState) -> dict:
        docs = state["relevant_documents"]
        messages = [
            SystemMessage(content=prompts.GENERATE_SYSTEM),
            *self._history_messages(state.get("history", [])),
            HumanMessage(
                content=prompts.GENERATE_USER.format(
                    context=format_context(docs), question=state["question"]
                )
            ),
        ]
        reply = await self.llm_generate.ainvoke(messages)
        return {"answer": str(reply.content)}

    async def verify(self, state: AgentState) -> dict:
        try:
            reply = await self.llm_json.ainvoke(
                [
                    HumanMessage(
                        content=prompts.GROUNDEDNESS_PROMPT.format(
                            context=format_context(state["relevant_documents"])[:12000],
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

    def _build(self):
        g = StateGraph(AgentState)
        g.add_node("analyze", self.analyze)
        g.add_node("retrieve", self.retrieve)
        g.add_node("grade", self.grade)
        g.add_node("rewrite", self.rewrite)
        g.add_node("generate", self.generate)
        g.add_node("verify", self.verify)
        g.add_node("no_context", self.no_context)

        g.set_entry_point("analyze")
        g.add_edge("analyze", "retrieve")
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
