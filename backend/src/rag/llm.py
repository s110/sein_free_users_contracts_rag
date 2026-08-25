"""Factorías de modelos: Ollama local (default) o DeepSeek por API.

Único punto de construcción de LLMs del proyecto: antes este módulo existía
pero nadie lo importaba, y `ContractsAgent` duplicaba su cuerpo, de modo que
un cambio de configuración había que hacerlo en dos sitios.

Los embeddings NO tienen proveedor de nube: el índice entero está vectorizado
con el modelo local y mezclar embedders corrompería la búsqueda densa.
"""

from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_ollama import ChatOllama

from .config import Settings


def build_llm(
    settings: Settings, json_mode: bool = False, temperature: float | None = None
) -> BaseChatModel:
    temp = settings.llm_temperature if temperature is None else temperature
    if settings.llm_provider == "deepseek":
        # Import diferido: langchain-openai solo hace falta en este modo.
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            base_url=settings.deepseek_base_url,
            # SecretStr → ChatOpenAI también lo trata como secreto.
            api_key=settings.deepseek_api_key,
            model=settings.deepseek_model,
            temperature=temp,
            timeout=settings.request_timeout,
            model_kwargs=(
                {"response_format": {"type": "json_object"}} if json_mode else {}
            ),
        )
    return ChatOllama(
        base_url=settings.ollama_host,
        model=settings.llm_model,
        temperature=temp,
        num_ctx=settings.llm_num_ctx,
        format="json" if json_mode else None,
        # `request_timeout` estaba declarado en Settings y no se usaba en
        # ninguna parte: una llamada colgada a Ollama no tenía deadline y
        # nginx sostenía la conexión los 600s completos.
        client_kwargs={"timeout": settings.request_timeout},
        # qwen3 es un modelo "thinking": sin esto, langchain-ollama deja los
        # bloques <think> dentro del contenido y se streamean al usuario como
        # si fueran la respuesta (y el verificador los evalúa).
        reasoning=False,
    )
