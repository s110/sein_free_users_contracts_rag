"""CLI de ingesta: `sein-rag-ingest [--force] [--vault DIR]`.

Corre como servicio one-shot en docker compose (profile `ingest`) o nativo.
Es seguro correrla en cron/launchd: es idempotente por source_hash.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from qdrant_client import QdrantClient

from ..config import get_settings
from ..logging_setup import setup_logging
from .embedder import OllamaEmbedder
from .indexer import MAX_PURGE_RATIO, ingest_vault

log = logging.getLogger("rag.ingest")


def main() -> int:
    settings = get_settings()
    setup_logging(settings.log_level)

    parser = argparse.ArgumentParser(description="Ingesta incremental del vault a Qdrant")
    parser.add_argument("--vault", type=Path, default=Path(settings.vault_dir))
    parser.add_argument("--force", action="store_true", help="Reindexa todo ignorando hashes")
    parser.add_argument(
        "--allow-purge",
        action="store_true",
        help="Permite borrar del índice más de la mitad de los documentos",
    )
    args = parser.parse_args()

    if not args.vault.is_dir():
        log.error("El vault no existe: %s", args.vault)
        return 2

    client = QdrantClient(url=settings.qdrant_url, timeout=60)
    embedder = OllamaEmbedder(
        host=settings.ollama_host,
        model=settings.embedding_model,
        batch_size=settings.embed_batch_size,
    )
    try:
        stats = ingest_vault(
            vault_dir=args.vault,
            client=client,
            embedder=embedder,
            collection=settings.collection,
            manifest_path=Path(settings.manifest_path),
            chunk_size=settings.chunk_size_chars,
            chunk_overlap=settings.chunk_overlap_chars,
            force=args.force,
            max_purge_ratio=1.0 if args.allow_purge else MAX_PURGE_RATIO,
        )
    finally:
        embedder.close()

    log.info(
        "Ingesta terminada: %d escaneados, %d indexados (%d chunks), "
        "%d sin cambios, %d purgados, %d fallidos, %d purgas abortadas",
        stats.scanned,
        stats.indexed,
        stats.chunks_upserted,
        stats.skipped,
        stats.deleted_stale,
        stats.failed,
        stats.purge_skipped,
    )
    # Antes: `1 if failed and not indexed and not skipped else 0`, de modo que
    # una corrida con 1 indexado y 500 fallidos salía con éxito y el cron no
    # se enteraba de que faltaban 500 contratos en el índice.
    return 0 if stats.ok else 1


if __name__ == "__main__":
    sys.exit(main())
