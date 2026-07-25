"""Esquemas compartidos entre ingesta, retrieval, grafo y API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class DocumentMeta(BaseModel):
    """Frontmatter YAML que emite ocr_pdf_markdown (campos relevantes al RAG)."""

    source_file: str
    source_hash: str
    created: str | None = None
    pages: int | None = None
    tipo: str | None = None  # contrato | adenda
    suministrador: str | None = None
    suministrador_code: str | None = None
    usuario_libre: str | None = None
    ruc_usuario_libre: str | None = None
    fecha_suscripcion: str | None = None
    source_url: str | None = None
    tags: list[str] = Field(default_factory=list)


class Chunk(BaseModel):
    """Unidad indexable: fragmento de texto + metadata heredada del documento."""

    chunk_id: str  # uuid5 determinístico: reingesta idéntica = mismos IDs
    doc_id: str  # relativo al vault, estable entre corridas
    chunk_index: int
    text: str
    section: str | None = None  # último header markdown visto
    page_start: int | None = None
    page_end: int | None = None
    meta: DocumentMeta


class RetrievedChunk(BaseModel):
    chunk_id: str
    doc_id: str
    text: str
    score: float
    section: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    source_file: str
    tipo: str | None = None
    suministrador: str | None = None
    usuario_libre: str | None = None
    ruc_usuario_libre: str | None = None
    fecha_suscripcion: str | None = None
    source_url: str | None = None


class HistoryMessage(BaseModel):
    """Turno previo de la conversación. Acotado para que validar el cuerpo no
    sea un vector de DoS: el cliente reenvía el historial completo en cada
    pregunta y `list[dict]` sin límite permitía POSTear megabytes que pydantic
    materializaba entero antes de que nadie lo truncara."""

    role: Literal["user", "assistant"]
    content: str = Field(max_length=8000)


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    history: list[HistoryMessage] = Field(default_factory=list, max_length=50)
    filters: dict[str, str] | None = Field(default=None)  # {"tipo": "contrato", ...}


class SourceRef(BaseModel):
    """Cita mostrada al usuario: [n] → documento/página."""

    n: int
    source_file: str
    doc_id: str
    section: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    usuario_libre: str | None = None
    suministrador: str | None = None
    fecha_suscripcion: str | None = None
    tipo: str | None = None
    source_url: str | None = None
    snippet: str = ""


class ChatEvent(BaseModel):
    """Evento SSE hacia el frontend."""

    type: Literal["status", "sources", "token", "end", "error"]
    data: dict
