"""Factorías de modelos locales vía Ollama (langchain-ollama)."""

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
    )
