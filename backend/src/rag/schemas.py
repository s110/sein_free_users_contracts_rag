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


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    history: list[dict] = Field(default_factory=list)  # [{role, content}]
    filters: dict | None = None  # {"tipo": "contrato", "ruc_usuario_libre": "..."}


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
