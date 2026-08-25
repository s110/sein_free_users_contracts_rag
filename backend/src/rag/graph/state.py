"""Estado del grafo agéntico."""

from __future__ import annotations

from typing import TypedDict

from ..schemas import RetrievedChunk


class AgentState(TypedDict, total=False):
    # Entrada
    question: str
    history: list[dict]  # [{role, content}]
    user_filters: dict | None  # filtros explícitos del frontend

    # Análisis
    search_query: str  # pregunta reescrita como query autónoma
    filters: dict  # filtros efectivos (usuario + extraídos)
    scope: str  # "contratos" | "fuera_de_tema" | "extraccion_masiva"
    num_docs: int | None  # cuántos documentos DISTINTOS pidió el usuario (>=2 activa diversidad)
    resolved_doc: dict | None  # doc elegido por el selector temporal (más reciente/antiguo)
    selector_note: str | None  # nota al generador sobre cómo se resolvió el superlativo
    multi_doc_note: str | None  # nota al generador: docs pedidos vs. en contexto vs. en índice

    # Retrieval
    documents: list[RetrievedChunk]
    relevant_documents: list[RetrievedChunk]
    rewrites: int  # cuántas veces se reformuló la query

    # Generación
    answer: str
    grounded: bool | None
    no_context: bool  # no se encontró nada relevante → respuesta honesta
