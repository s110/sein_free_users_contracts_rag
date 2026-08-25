"""Configuración 12-factor: todo por variables de entorno con prefijo RAG_.

Misma convención que ocr_pdf_markdown: Ollama corre nativo en el host en
macOS (Metal no es accesible desde Docker); los contenedores se conectan
vía host.docker.internal.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from pydantic import SecretStr
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
    # Reevaluados en ago 2026: qwen3.5:4b sucede a qwen3:4b (201 idiomas,
    # 256K ctx, mismo footprint) y qwen3-embedding:0.6b rinde mejor que
    # bge-m3 en MTEB multilingüe con la mitad de RAM. La dimensión del
    # índice se autodetecta, pero cambiar de embedder exige reindexar.
    llm_model: str = "qwen3.5:4b"
    embedding_model: str = "qwen3-embedding:0.6b"

    # --- Proveedor del LLM: "ollama" (local, default) o "deepseek" (nube) ---
    # Los embeddings SIEMPRE son locales (el índice depende de ellos); solo la
    # generación puede ir a la nube. La clave es SecretStr: cualquier repr,
    # log o volcado de Settings la muestra como '**********'. Nunca aparece
    # en /api/meta, /api/health ni en mensajes de error.
    llm_provider: str = "ollama"
    deepseek_api_key: SecretStr = SecretStr("")
    # Vision Exp y no el Flash de texto: mismo precio publicado, y los
    # contratos traen diagramas de carga y mapas que en el futuro entrarán
    # como imagen. Para solo-texto se comporta igual.
    deepseek_model: str = "deepseek-v4-flash-vision-exp"
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    llm_temperature: float = 0.1
    llm_num_ctx: int = 8192

    # --- Colección / ingesta ---
    collection: str = "sein_contracts"
    vault_dir: str = "/data/vault"
    manifest_path: str = "/data/index/ingest_manifest.jsonl"
    chunk_size_chars: int = 3200  # ~800 tokens; holgado para embedders de 32K
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

    # --- Chat público con cuota diaria por IP ---
    # Con public_chat=true, /api/chat acepta peticiones SIN clave aunque
    # RAG_API_KEY esté definida, sujetas a una cuota diaria por IP (el LLM es
    # el recurso caro: un 4B local atiende en serie). Una API key válida
    # salta la cuota — uso propio ilimitado. /api/documents y /api/meta
    # siguen exigiendo la clave siempre: publican RUC de usuarios libres.
    public_chat: bool = False
    chat_daily_limit: int = 5
    quota_db_path: str = "/data/quota/quota.sqlite3"
    # Cabecera de la que se toma la IP del cliente. nginx la reconstruye con
    # real_ip desde CF-Connecting-IP y la reenvía como X-Real-IP; el backend
    # solo es alcanzable desde la red interna de compose, así que confiar en
    # ella no abre spoofing desde fuera.
    trusted_ip_header: str = "x-real-ip"

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
        if self.public_chat and self.chat_daily_limit < 1:
            raise ConfigError(
                f"RAG_CHAT_DAILY_LIMIT ({self.chat_daily_limit}) debe ser >= 1 "
                "con RAG_PUBLIC_CHAT=true"
            )
        if self.llm_provider not in ("ollama", "deepseek"):
            raise ConfigError(
                f"RAG_LLM_PROVIDER inválido: {self.llm_provider!r} (ollama | deepseek)"
            )
        if self.llm_provider == "deepseek" and not self.deepseek_api_key.get_secret_value():
            raise ConfigError(
                "RAG_LLM_PROVIDER=deepseek exige RAG_DEEPSEEK_API_KEY. "
                "Ponla solo en .env (600, gitignoreado), nunca en compose ni en el código."
            )
        if self.public_chat and not self.api_key:
            log.warning(
                "RAG_PUBLIC_CHAT=true sin RAG_API_KEY: nadie (ni tú) puede "
                "saltarse la cuota diaria, y /api/documents queda inaccesible."
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()
