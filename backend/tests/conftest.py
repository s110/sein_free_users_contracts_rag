"""Fixtures compartidas.

El backend no tenía conftest: `get_settings` está cacheada con `lru_cache` y
`api/main.py` la llamaba en import time, así que ningún test podía influir en
la configuración. Aquí se limpia la caché en cada test.
"""

from __future__ import annotations

import os

import pytest

from rag.config import Settings, get_settings
from rag.schemas import Chunk, DocumentMeta, RetrievedChunk


@pytest.fixture(autouse=True)
def _clean_settings(monkeypatch):
    for key in list(os.environ):
        if key.startswith("RAG_"):
            monkeypatch.delenv(key, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def settings() -> Settings:
    return Settings(api_key="clave-de-prueba", collection="test_contracts")


def make_meta(**kw) -> DocumentMeta:
    base = {
        "source_file": "ATRE_20467534026_20250514_8789_00.pdf",
        "source_hash": "a3f9b2c1d4e5f6a7",
        "tipo": "contrato",
        "suministrador": "ATRE S.A.",
        "suministrador_code": "ATRE",
        "usuario_libre": "ACME S.A.",
        "ruc_usuario_libre": "20467534026",
        "fecha_suscripcion": "2025-05-14",
        "source_url": "https://www.osinergmin.gob.pe/c.pdf",
        "tags": ["osinergmin", "contrato"],
    }
    return DocumentMeta(**{**base, **kw})


def make_chunk(index: int = 0, text: str = "texto", **kw) -> Chunk:
    return Chunk(
        chunk_id=f"chunk-{index}",
        doc_id=kw.pop("doc_id", "contratos/a"),
        chunk_index=index,
        text=text,
        meta=kw.pop("meta", make_meta()),
        **kw,
    )


def make_retrieved(n: int = 1, text: str = "contenido del contrato", **kw) -> RetrievedChunk:
    base = {
        "chunk_id": f"c{n}",
        "doc_id": f"contratos/doc{n}",
        "text": text,
        "score": 0.9,
        "source_file": f"doc{n}.pdf",
        "page_start": n,
        "page_end": n,
        "section": None,
        "tipo": "contrato",
        "suministrador": "ATRE S.A.",
        "usuario_libre": "ACME S.A.",
        "ruc_usuario_libre": "20467534026",
        "fecha_suscripcion": "2025-05-14",
        "source_url": None,
    }
    return RetrievedChunk(**{**base, **kw})
