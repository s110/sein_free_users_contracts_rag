"""Configuración 12-factor: todo por variables de entorno con prefijo RAG_.

Misma convención que ocr_pdf_markdown: Ollama corre nativo en el host en
macOS (Metal no es accesible desde Docker); los contenedores se conectan
vía host.docker.internal.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RAG_", env_file=".env", extra="ignore")

    # --- Servicios ---
    ollama_host: str = "http://localhost:11434"
    qdrant_url: str = "http://localhost:6333"

    # --- Modelos (Mac Mini M4 16GB: LLM 4B + embeddings 0.6B caben juntos) ---
    llm_model: str = "qwen3:4b"
    embedding_model: str = "bge-m3"
    llm_temperature: float = 0.1
    llm_num_ctx: int = 8192

    # --- Colección / ingesta ---
    collection: str = "sein_contracts"
    vault_dir: str = "/data/vault"
    manifest_path: str = "/data/index/ingest_manifest.jsonl"
    chunk_size_chars: int = 3200  # ~800 tokens para bge-m3
    chunk_overlap_chars: int = 400
    embed_batch_size: int = 16

    # --- Retrieval ---
    top_k: int = 6
    dense_candidates: int = 20
    text_candidates: int = 20
    max_query_rewrites: int = 2

    # --- API ---
    api_key: str = ""  # vacío = sin auth (solo para uso en LAN)
    cors_origins: str = "*"
    request_timeout: int = 300

    # --- Observabilidad ---
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
