"""Configuración 12-factor: todo por variables de entorno con prefijo RAG_.

Misma convención que ocr_pdf_markdown: Ollama corre nativo en el host en
macOS (Metal no es accesible desde Docker); los contenedores se conectan
vía host.docker.internal.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger("rag.config")


class ConfigError(RuntimeError):
    """Configuración que no permite arrancar de forma segura."""


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
    # Presupuesto de contexto para el verificador de fidelidad. Se reparte
    # entre las fuentes en vez de cortar el final.
    verify_context_chars: int = 16000

    # --- API ---
    api_key: str = ""
    # Arrancar sin API key tiene que ser una decisión explícita: antes el
    # default vacío desactivaba la autenticación en silencio, y una
    # exposición pública sin haber puesto RAG_API_KEY publicaba
    # /api/documents (con RUC de usuarios libres) a todo internet.
    allow_anonymous: bool = False
    # Sin wildcard por defecto: con `*` cualquier página podía leer la
    # respuesta de /api/documents desde el navegador de la víctima.
    cors_origins: str = ""
    request_timeout: int = 300

    # --- Observabilidad ---
    log_level: str = "INFO"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def validate_runtime(self) -> None:
        """Comprueba invariantes que deben impedir el arranque."""
        if not self.api_key and not self.allow_anonymous:
            raise ConfigError(
                "No hay RAG_API_KEY. Define una, o pon RAG_ALLOW_ANONYMOUS=true "
                "si de verdad quieres exponer la API sin autenticación."
            )
        if self.chunk_overlap_chars >= self.chunk_size_chars:
            raise ConfigError(
                f"RAG_CHUNK_OVERLAP_CHARS ({self.chunk_overlap_chars}) debe ser menor "
                f"que RAG_CHUNK_SIZE_CHARS ({self.chunk_size_chars})"
            )
        if "*" in self.cors_origin_list and self.api_key:
            log.warning(
                "RAG_CORS_ORIGINS='*' con API key definida: cualquier página puede "
                "usar la clave de un usuario autenticado desde su navegador."
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()
