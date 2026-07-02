"""Indexado incremental en Qdrant, idempotente por source_hash.

Contrato de confiabilidad (espejo del pipeline OCR río arriba):
- Un documento solo se (re)indexa si su `source_hash` difiere del que está
  almacenado en Qdrant → reingestar el vault completo cuesta ~0 si nada cambió.
- Si el hash cambió, primero se borran los chunks viejos del doc (no quedan
  chunks huérfanos de versiones anteriores) y luego se upsertan los nuevos.
- IDs determinísticos (uuid5): un crash a mitad de upsert se corrige solo en
  la siguiente corrida (upsert = overwrite, no duplica).
- Cada corrida deja una línea auditable en un manifest JSONL.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import orjson
from qdrant_client import QdrantClient, models

from ..schemas import Chunk
from .chunker import chunk_document
from .embedder import OllamaEmbedder
from .loader import iter_vault, load_document

log = logging.getLogger("rag.indexer")

# Campos del payload con índice keyword (filtros exactos en retrieval)
KEYWORD_FIELDS = [
    "doc_id",
    "source_file",
    "source_hash",
    "tipo",
    "suministrador",
    "suministrador_code",
    "usuario_libre",
    "ruc_usuario_libre",
    "fecha_suscripcion",
]


@dataclass
class IngestStats:
    scanned: int = 0
    indexed: int = 0
    skipped: int = 0
    deleted_stale: int = 0
    failed: int = 0
    chunks_upserted: int = 0


def ensure_collection(client: QdrantClient, name: str, dim: int) -> None:
    if client.collection_exists(name):
        return
    client.create_collection(
        collection_name=name,
        vectors_config=models.VectorParams(size=dim, distance=models.Distance.COSINE),
    )
    for f in KEYWORD_FIELDS:
        client.create_payload_index(
            collection_name=name,
            field_name=f,
            field_schema=models.PayloadSchemaType.KEYWORD,
        )
    # Índice full-text sobre el texto: habilita búsqueda léxica (híbrida con densa)
    client.create_payload_index(
        collection_name=name,
        field_name="text",
        field_schema=models.TextIndexParams(
            type=models.TextIndexType.TEXT,
            tokenizer=models.TokenizerType.MULTILINGUAL,
            min_token_len=2,
            lowercase=True,
        ),
    )
    log.info("Colección '%s' creada (dim=%d, cosine)", name, dim)


def stored_hashes(client: QdrantClient, collection: str) -> dict[str, str]:
    """Mapa doc_id → source_hash de lo que ya está indexado."""
    hashes: dict[str, str] = {}
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=collection,
            with_payload=["doc_id", "source_hash"],
            with_vectors=False,
            limit=512,
            offset=offset,
        )
        for p in points:
            payload = p.payload or {}
            doc_id, h = payload.get("doc_id"), payload.get("source_hash")
            if doc_id and h:
                hashes[doc_id] = h
        if offset is None:
            return hashes


def delete_document(client: QdrantClient, collection: str, doc_id: str) -> None:
    client.delete(
        collection_name=collection,
        points_selector=models.FilterSelector(
            filter=models.Filter(
                must=[models.FieldCondition(key="doc_id", match=models.MatchValue(value=doc_id))]
            )
        ),
        wait=True,
    )


def chunk_payload(chunk: Chunk) -> dict:
    m = chunk.meta
    return {
        "doc_id": chunk.doc_id,
        "chunk_index": chunk.chunk_index,
        "text": chunk.text,
        "section": chunk.section,
        "page_start": chunk.page_start,
        "page_end": chunk.page_end,
        "source_file": m.source_file,
        "source_hash": m.source_hash,
        "tipo": m.tipo,
        "suministrador": m.suministrador,
        "suministrador_code": m.suministrador_code,
        "usuario_libre": m.usuario_libre,
        "ruc_usuario_libre": m.ruc_usuario_libre,
        "fecha_suscripcion": m.fecha_suscripcion,
        "source_url": m.source_url,
        "tags": m.tags,
    }


def upsert_chunks(
    client: QdrantClient,
    collection: str,
    chunks: list[Chunk],
    embedder: OllamaEmbedder,
) -> None:
    vectors = embedder.embed([c.text for c in chunks])
    points = [
        models.PointStruct(id=c.chunk_id, vector=v, payload=chunk_payload(c))
        for c, v in zip(chunks, vectors, strict=True)
    ]
    client.upsert(collection_name=collection, points=points, wait=True)


def write_manifest_line(manifest_path: Path, record: dict) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with manifest_path.open("ab") as f:
        f.write(orjson.dumps(record) + b"\n")


def ingest_vault(
    vault_dir: Path,
    client: QdrantClient,
    embedder: OllamaEmbedder,
    collection: str,
    manifest_path: Path,
    chunk_size: int = 3200,
    chunk_overlap: int = 400,
    force: bool = False,
) -> IngestStats:
    stats = IngestStats()
    dim = embedder.dimension()
    ensure_collection(client, collection, dim)
    existing = {} if force else stored_hashes(client, collection)

    files = iter_vault(vault_dir)
    stats.scanned = len(files)
    log.info("Vault %s: %d documentos, %d ya indexados", vault_dir, len(files), len(existing))
    seen_doc_ids: set[str] = set()

    for path in files:
        started = datetime.now(UTC)
        doc = load_document(path, vault_dir)
        if doc is None:
            stats.failed += 1
            continue
        seen_doc_ids.add(doc.doc_id)

        if not force and existing.get(doc.doc_id) == doc.meta.source_hash:
            stats.skipped += 1
            continue

        try:
            if doc.doc_id in existing:
                delete_document(client, collection, doc.doc_id)
            chunks = chunk_document(
                doc.doc_id, doc.body, doc.meta, max_chars=chunk_size, overlap_chars=chunk_overlap
            )
            if not chunks:
                log.warning("Sin chunks tras el chunking, se salta: %s", doc.doc_id)
                stats.failed += 1
                continue
            upsert_chunks(client, collection, chunks, embedder)
            stats.indexed += 1
            stats.chunks_upserted += len(chunks)
            write_manifest_line(
                manifest_path,
                {
                    "ts": started.isoformat(timespec="seconds"),
                    "doc_id": doc.doc_id,
                    "source_hash": doc.meta.source_hash,
                    "chunks": len(chunks),
                    "status": "indexed",
                    "seconds": (datetime.now(UTC) - started).total_seconds(),
                },
            )
            log.info("Indexado %s (%d chunks)", doc.doc_id, len(chunks))
        except Exception as e:  # noqa: BLE001 — un doc malo no aborta la corrida
            stats.failed += 1
            log.error("Falló indexado de %s: %s", doc.doc_id, e)
            write_manifest_line(
                manifest_path,
                {
                    "ts": started.isoformat(timespec="seconds"),
                    "doc_id": doc.doc_id,
                    "status": "failed",
                    "error": str(e),
                },
            )

    # Documentos borrados del vault → fuera del índice (el índice refleja el vault)
    for stale_doc_id in set(existing) - seen_doc_ids:
        delete_document(client, collection, stale_doc_id)
        stats.deleted_stale += 1
        log.info("Documento removido del vault, purgado del índice: %s", stale_doc_id)

    return stats
