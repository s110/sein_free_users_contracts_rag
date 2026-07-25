"""Factorías de modelos locales vía Ollama (langchain-ollama).

Único punto de construcción de LLMs del proyecto: antes este módulo existía
pero nadie lo importaba, y `ContractsAgent` duplicaba su cuerpo, de modo que
un cambio de configuración había que hacerlo en dos sitios.
"""

from __future__ import annotations

from langchain_ollama import ChatOllama

from .config import Settings


def build_llm(
    settings: Settings, json_mode: bool = False, temperature: float | None = None
) -> ChatOllama:
    return ChatOllama(
        base_url=settings.ollama_host,
        model=settings.llm_model,
        temperature=settings.llm_temperature if temperature is None else temperature,
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
