"""Proveedor DeepSeek: selección de factoría y que la clave no se filtre."""

from __future__ import annotations

import pytest

from rag.config import ConfigError, Settings
from rag.llm import build_llm


def _settings(**kw):
    return Settings(api_key="k", _env_file=None, **kw)


class TestSeleccionDeProveedor:
    def test_default_es_ollama(self):
        from langchain_ollama import ChatOllama

        assert isinstance(build_llm(_settings()), ChatOllama)

    def test_deepseek_construye_chatopenai_contra_su_base_url(self):
        from langchain_openai import ChatOpenAI

        llm = build_llm(_settings(llm_provider="deepseek", deepseek_api_key="sk-secreta-123"))
        assert isinstance(llm, ChatOpenAI)
        assert "api.deepseek.com" in str(llm.openai_api_base or llm.client._client.base_url)

    def test_proveedor_invalido_no_arranca(self):
        with pytest.raises(ConfigError, match="RAG_LLM_PROVIDER"):
            _settings(llm_provider="openai").validate_runtime()

    def test_deepseek_sin_clave_no_arranca(self):
        with pytest.raises(ConfigError, match="RAG_DEEPSEEK_API_KEY"):
            _settings(llm_provider="deepseek").validate_runtime()


class TestLaClaveNoSeFiltra:
    def test_repr_de_settings_enmascara_la_clave(self):
        s = _settings(llm_provider="deepseek", deepseek_api_key="sk-secreta-123")
        assert "sk-secreta-123" not in repr(s)
        assert "sk-secreta-123" not in str(s)

    def test_model_dump_enmascara_la_clave(self):
        s = _settings(llm_provider="deepseek", deepseek_api_key="sk-secreta-123")
        assert "sk-secreta-123" not in str(s.model_dump())

    def test_meta_y_health_no_exponen_la_clave(self, monkeypatch):
        from .test_api import build, ollama  # noqa: F401

        s = _settings(
            llm_provider="deepseek",
            deepseek_api_key="sk-secreta-123",
            public_chat=False,
        )
        client = build(s)
        client.app.state.quota = None
        meta = client.get("/api/meta", headers={"X-API-Key": "k"})
        assert meta.status_code == 200
        assert "sk-secreta-123" not in meta.text
        # El nombre del modelo de nube sí se publica; la clave jamás.
        health = client.get("/api/health")
        assert "sk-secreta-123" not in health.text


class TestModeloDeepSeek:
    def test_default_es_v41_flash_por_su_nombre_de_api(self):
        assert _settings().deepseek_model == "deepseek-flash"

    def test_razonamiento_apagado_por_defecto(self):
        s = _settings(llm_provider="deepseek", deepseek_api_key="sk-x")
        llm = build_llm(s)
        assert llm.extra_body == {"thinking": {"type": "disabled"}}

    def test_razonamiento_se_enciende_por_config(self):
        s = _settings(llm_provider="deepseek", deepseek_api_key="sk-x", deepseek_thinking=True)
        assert build_llm(s).extra_body == {"thinking": {"type": "enabled"}}

    def test_json_mode_conserva_el_control_de_razonamiento(self):
        s = _settings(llm_provider="deepseek", deepseek_api_key="sk-x")
        llm = build_llm(s, json_mode=True)
        assert llm.model_kwargs["response_format"] == {"type": "json_object"}
        assert llm.extra_body == {"thinking": {"type": "disabled"}}
