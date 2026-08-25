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

from ..schemas import Chunk, normalize_text_filter
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


# Si una corrida borraría más de esta fracción del índice, se aborta la purga.
MAX_PURGE_RATIO = 0.5


def doc_id_for(path: Path, vault_root: Path) -> str:
    """doc_id de un fichero sin necesidad de poder leerlo."""
    return str(path.relative_to(vault_root).with_suffix(""))


@dataclass
class IngestStats:
    scanned: int = 0
    indexed: int = 0
    skipped: int = 0
    deleted_stale: int = 0
    failed: int = 0
    chunks_upserted: int = 0
    purge_skipped: int = 0  # stale detectados pero NO borrados por las guardas

    @property
    def ok(self) -> bool:
        """Una corrida es buena solo si nada falló y ninguna purga se abortó."""
        return self.failed == 0 and self.purge_skipped == 0


def ensure_collection(client: QdrantClient, name: str, dim: int) -> None:
    """Crea la colección si falta y **siempre** reconcilia los índices.

    Antes esto retornaba temprano cuando la colección ya existía, así que una
    colección creada por una versión anterior (o a mano) se quedaba sin los
    índices keyword ni el full-text. `HybridStore._text_search` fallaba,
    `store.py` se comía la excepción con un WARNING, y el retrieval híbrido
    degradaba en silencio a solo-denso para siempre.
    """
    if not client.collection_exists(name):
        client.create_collection(
            collection_name=name,
            vectors_config=models.VectorParams(size=dim, distance=models.Distance.COSINE),
        )
        log.info("Colección '%s' creada (dim=%d, cosine)", name, dim)

    ensure_payload_indexes(client, name)


def ensure_payload_indexes(client: QdrantClient, name: str) -> None:
    """Crea los índices de payload. Idempotente: recrear uno existente es no-op."""
    for f in KEYWORD_FIELDS:
        _create_index(client, name, f, models.PayloadSchemaType.KEYWORD)
    # Índice full-text sobre el texto: habilita búsqueda léxica (híbrida con densa)
    _create_index(
        client,
        name,
        "text",
        models.TextIndexParams(
            type=models.TextIndexType.TEXT,
            tokenizer=models.TokenizerType.MULTILINGUAL,
            min_token_len=2,
            lowercase=True,
        ),
    )
    # Índices full-text de los espejos normalizados: los filtros
    # "usuario_libre" y "suministrador" buscan por palabras ("orygen",
    # "lavanderia landeo"), no por igualdad exacta con tildes y "S.A.C.".
    for norm_field in ("usuario_libre_norm", "suministrador_norm"):
        _create_index(
            client,
            name,
            norm_field,
            models.TextIndexParams(
                type=models.TextIndexType.TEXT,
                tokenizer=models.TokenizerType.WORD,
                min_token_len=2,
                lowercase=True,
            ),
        )


def _create_index(client: QdrantClient, collection: str, field: str, schema) -> None:
    try:
        client.create_payload_index(
            collection_name=collection, field_name=field, field_schema=schema
        )
    except Exception as e:  # noqa: BLE001 — ya existente es el caso normal
        log.debug("Índice '%s' ya presente o no creable: %s", field, e)


def backfill_norm_fields(client: QdrantClient, collection: str) -> int:
    """Rellena los espejos *_norm en puntos indexados antes de que existieran.

    Solo payload (sin re-embeber): agrupa los ids por valor normalizado y hace
    un set_payload por grupo — hay ~30 suministradores distintos, no ~13k
    llamadas. Idempotente: los puntos que ya tienen el espejo se saltan.
    """
    groups: dict[tuple[str | None, str | None], list] = {}
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=collection,
            with_payload=["suministrador", "suministrador_norm",
                          "usuario_libre", "usuario_libre_norm"],
            with_vectors=False,
            limit=1024,
            offset=offset,
        )
        for p in points:
            pl = p.payload or {}
            sum_norm = (
                normalize_text_filter(pl["suministrador"])
                if pl.get("suministrador") and not pl.get("suministrador_norm")
                else None
            )
            ul_norm = (
                normalize_text_filter(pl["usuario_libre"])
                if pl.get("usuario_libre") and not pl.get("usuario_libre_norm")
                else None
            )
            if sum_norm or ul_norm:
                groups.setdefault((sum_norm, ul_norm), []).append(p.id)
        if offset is None:
            break
    updated = 0
    for (sum_norm, ul_norm), ids in groups.items():
        payload = {}
        if sum_norm:
            payload["suministrador_norm"] = sum_norm
        if ul_norm:
            payload["usuario_libre_norm"] = ul_norm
        client.set_payload(collection_name=collection, payload=payload, points=ids, wait=True)
        updated += len(ids)
    if updated:
        log.info("Backfill *_norm: %d chunks actualizados en %d grupos", updated, len(groups))
    return updated


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
        # Minúsculas SIEMPRE: el parser del filename emite "adenda" y el state
        # del scraper "Adenda"; el índice mezclado rompía el filtro exacto.
        "tipo": m.tipo.lower() if m.tipo else None,
        "suministrador": m.suministrador,
        "suministrador_norm": normalize_text_filter(m.suministrador) if m.suministrador else None,
        "suministrador_code": m.suministrador_code,
        "usuario_libre": m.usuario_libre,
        # Espejo normalizado para el filtro por razón social (full-text sin
        # tildes ni mayúsculas; ver schemas.normalize_text_filter).
        "usuario_libre_norm": normalize_text_filter(m.usuario_libre) if m.usuario_libre else None,
        "ruc_usuario_libre": m.ruc_usuario_libre,
        "fecha_suscripcion": m.fecha_suscripcion,
        "source_url": m.source_url,
        "tags": m.tags,
    }


def upsert_chunks(
    client: QdrantClient,
    collection: str,
    chunks: list[Chunk],
    vectors: list[list[float]],
) -> None:
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
    max_purge_ratio: float = MAX_PURGE_RATIO,
) -> IngestStats:
    stats = IngestStats()
    dim = embedder.dimension()
    ensure_collection(client, collection, dim)
    # `existing` se consulta SIEMPRE, también con --force: sin él, los chunks
    # de una versión anterior del documento quedaban huérfanos en la colección
    # (los ids son uuid5 del source_hash, así que los nuevos no los pisan) y el
    # retrieval devolvía dos generaciones del mismo contrato como si fueran
    # documentos distintos en contradicción.
    existing = stored_hashes(client, collection)

    files = iter_vault(vault_dir)
    stats.scanned = len(files)
    log.info("Vault %s: %d documentos, %d ya indexados", vault_dir, len(files), len(existing))
    seen_doc_ids: set[str] = set()

    for path in files:
        started = datetime.now(UTC)
        doc = load_document(path, vault_dir)
        if doc is None:
            stats.failed += 1
            # El documento existe en el vault aunque no se haya podido leer:
            # marcarlo como visto evita que la purga de stale lo borre del
            # índice por un EIO transitorio en un montaje de red.
            seen_doc_ids.add(doc_id_for(path, vault_dir))
            continue
        seen_doc_ids.add(doc.doc_id)

        if not force and existing.get(doc.doc_id) == doc.meta.source_hash:
            stats.skipped += 1
            continue

        try:
            chunks = chunk_document(
                doc.doc_id, doc.body, doc.meta, max_chars=chunk_size, overlap_chars=chunk_overlap
            )
            if not chunks:
                log.warning("Sin chunks tras el chunking, se salta: %s", doc.doc_id)
                stats.failed += 1
                continue
            # Embeber ANTES de borrar: el borrado y el upsert no son atómicos,
            # y si el embedder fallaba en medio (Ollama sin memoria) el
            # documento desaparecía del índice en su versión vieja y en la
            # nueva a la vez.
            vectors = embedder.embed([c.text for c in chunks])
            if doc.doc_id in existing:
                delete_document(client, collection, doc.doc_id)
            upsert_chunks(client, collection, chunks, vectors)
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

    stale = set(existing) - seen_doc_ids
    _purge_stale(client, collection, stale, existing, stats, files, max_purge_ratio)
    return stats


def _purge_stale(
    client: QdrantClient,
    collection: str,
    stale: set[str],
    existing: dict[str, str],
    stats: IngestStats,
    files: list[Path],
    max_purge_ratio: float,
) -> None:
    """Purga documentos que ya no están en el vault, con dos frenos de mano.

    Sin ellos, un `VAULT_DIR` mal montado (compose crea el directorio vacío,
    así que `--vault` pasaba la validación) hacía `iter_vault -> []`, y el
    bucle borraba **toda** la colección reportando éxito con código 0.
    """
    if not stale:
        return

    if not files:
        stats.purge_skipped = len(stale)
        log.error(
            "El vault no tiene documentos pero el índice tiene %d: "
            "se aborta la purga (¿VAULT_DIR mal montado?)",
            len(stale),
        )
        return

    ratio = len(stale) / max(len(existing), 1)
    if ratio > max_purge_ratio:
        stats.purge_skipped = len(stale)
        log.error(
            "La purga eliminaría %d de %d documentos (%.0f%% > %.0f%% permitido): se aborta. "
            "Reingesta con --allow-purge si el borrado es intencional.",
            len(stale),
            len(existing),
            ratio * 100,
            max_purge_ratio * 100,
        )
        return

    for stale_doc_id in sorted(stale):
        delete_document(client, collection, stale_doc_id)
        stats.deleted_stale += 1
        log.info("Documento removido del vault, purgado del índice: %s", stale_doc_id)
