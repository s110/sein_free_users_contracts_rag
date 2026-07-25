"""Chunking markdown-aware para contratos OCR'd.

Estrategia:
1. Se parte el cuerpo por headers markdown (`#`..`####`), preservando la
   jerarquía en `section` (contexto para el LLM y para las citas).
2. Los headers `## Página N` del pipeline OCR no cortan chunks (son límites
   artificiales de página): se consumen como marcadores y se registra el
   rango de páginas de cada chunk para poder citar "pág. 3-4".
3. Dentro de cada sección se empaquetan párrafos hasta `chunk_size_chars`
   con solapamiento de `overlap_chars` para no perder contexto en los bordes.

IDs determinísticos: uuid5(doc_id, source_hash, chunk_index) → reingestar el
mismo contenido produce exactamente los mismos puntos (upsert idempotente).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field

from ..schemas import Chunk, DocumentMeta

_NAMESPACE = uuid.UUID("7c9e6a2e-53c1-45f7-9d3a-1b2f0e8a4c5d")

_PAGE_HEADER_RE = re.compile(r"^#{1,6}\s*P[áa]gina\s+(\d+)\s*$", re.IGNORECASE)
_HEADER_RE = re.compile(r"^(#{1,6})\s+(.*)$")


def chunk_id_for(doc_id: str, source_hash: str, index: int) -> str:
    return str(uuid.uuid5(_NAMESPACE, f"{doc_id}|{source_hash}|{index}"))


@dataclass
class _Block:
    text: str
    section: str | None
    page: int | None


@dataclass
class _Packer:
    """Empaqueta bloques consecutivos en chunks con overlap."""

    max_chars: int
    overlap_chars: int
    chunks: list[dict] = field(default_factory=list)
    _buf: list[_Block] = field(default_factory=list)
    _len: int = 0

    def add(self, block: _Block) -> None:
        # Un bloque solo puede ser gigante (tabla OCR'd): se parte duro
        if len(block.text) > self.max_chars:
            self.flush()
            for piece in _hard_split(block.text, self.max_chars, self.overlap_chars):
                self.chunks.append(_to_dict([_Block(piece, block.section, block.page)]))
            return
        if self._len + len(block.text) > self.max_chars and self._buf:
            self.flush(carry_overlap=True)
        self._buf.append(block)
        self._len += len(block.text) + 2

    def flush(self, carry_overlap: bool = False) -> None:
        if not self._buf:
            return
        self.chunks.append(_to_dict(self._buf))
        if carry_overlap and self.overlap_chars > 0:
            # Los últimos bloques (hasta overlap_chars) reabren el buffer
            carried: list[_Block] = []
            size = 0
            for b in reversed(self._buf):
                if size + len(b.text) > self.overlap_chars:
                    break
                carried.insert(0, b)
                size += len(b.text)
            self._buf = carried
            self._len = size
        else:
            self._buf = []
            self._len = 0


def _to_dict(blocks: list[_Block]) -> dict:
    pages = [b.page for b in blocks if b.page is not None]
    return {
        "text": "\n\n".join(b.text for b in blocks),
        "section": next((b.section for b in blocks if b.section), None),
        "page_start": min(pages) if pages else None,
        "page_end": max(pages) if pages else None,
    }


def _hard_split(text: str, max_chars: int, overlap: int) -> list[str]:
    step = max(max_chars - overlap, 1)
    return [
        text[i : i + max_chars]
        for i in range(0, len(text), step)
        if text[i : i + max_chars].strip()
    ]


class ChunkingError(ValueError):
    """Parámetros de chunking incoherentes."""


def _validate(max_chars: int, overlap_chars: int) -> None:
    """El overlap tiene que ser estrictamente menor que el tamaño de chunk.

    Con `overlap >= max_chars`, `_hard_split` calcula `step = max(max-ov, 1)`
    = 1 y trocea el texto carácter a carácter: un contrato de 200KB con una
    tabla grande producía 200.000 chunks y 12.500 llamadas a /api/embed.
    """
    if max_chars < 1:
        raise ChunkingError(f"chunk_size_chars debe ser >= 1, no {max_chars}")
    if overlap_chars < 0:
        raise ChunkingError(f"chunk_overlap_chars no puede ser negativo ({overlap_chars})")
    if overlap_chars >= max_chars:
        raise ChunkingError(
            f"chunk_overlap_chars ({overlap_chars}) debe ser menor que "
            f"chunk_size_chars ({max_chars})"
        )


def chunk_document(
    doc_id: str,
    body: str,
    meta: DocumentMeta,
    max_chars: int = 3200,
    overlap_chars: int = 400,
) -> list[Chunk]:
    _validate(max_chars, overlap_chars)
    packer = _Packer(max_chars=max_chars, overlap_chars=overlap_chars)
    section: str | None = None
    page: int | None = None
    paragraph: list[str] = []

    def close_paragraph() -> None:
        nonlocal paragraph
        text = "\n".join(paragraph).strip()
        paragraph = []
        if text:
            packer.add(_Block(text=text, section=section, page=page))

    for line in body.splitlines():
        page_m = _PAGE_HEADER_RE.match(line)
        if page_m:
            close_paragraph()
            page = int(page_m.group(1))
            continue
        header_m = _HEADER_RE.match(line)
        if header_m:
            close_paragraph()
            packer.flush()  # headers reales sí son límites semánticos
            section = header_m.group(2).strip()
            continue
        if not line.strip():
            close_paragraph()
            continue
        paragraph.append(line)

    close_paragraph()
    packer.flush()

    return [
        Chunk(
            chunk_id=chunk_id_for(doc_id, meta.source_hash, i),
            doc_id=doc_id,
            chunk_index=i,
            text=c["text"],
            section=c["section"],
            page_start=c["page_start"],
            page_end=c["page_end"],
            meta=meta,
        )
        for i, c in enumerate(packer.chunks)
    ]
