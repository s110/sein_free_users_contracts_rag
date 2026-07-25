from __future__ import annotations

import pytest

from rag.config import ConfigError, Settings, get_settings


class TestValidateRuntime:
    def test_con_api_key_arranca(self):
        Settings(api_key="secreta").validate_runtime()

    def test_sin_api_key_no_arranca(self):
        """El default vacío desactivaba la autenticación en silencio: una
        exposición pública sin RAG_API_KEY publicaba /api/documents —con RUC
        de usuarios libres— a todo internet."""
        with pytest.raises(ConfigError, match="RAG_API_KEY"):
            Settings().validate_runtime()

    def test_anonimo_explicito_si_arranca(self):
        Settings(allow_anonymous=True).validate_runtime()

    def test_overlap_mayor_que_chunk_se_rechaza(self):
        """`overlap >= chunk_size` degeneraba en un chunk por carácter."""
        with pytest.raises(ConfigError, match="OVERLAP"):
            Settings(api_key="k", chunk_size_chars=400, chunk_overlap_chars=400).validate_runtime()

    def test_overlap_menor_es_valido(self):
        Settings(api_key="k", chunk_size_chars=400, chunk_overlap_chars=100).validate_runtime()

    def test_avisa_de_cors_wildcard_con_clave(self, caplog):
        Settings(api_key="k", cors_origins="*").validate_runtime()
        assert "cualquier página" in caplog.text


class TestCors:
    def test_por_defecto_no_hay_origenes(self):
        """El default era `*`: cualquier página podía leer /api/documents
        desde el navegador de la víctima."""
        assert Settings().cors_origin_list == []

    def test_parte_y_limpia_la_lista(self):
        s = Settings(cors_origins=" https://a.com , https://b.com ,, ")
        assert s.cors_origin_list == ["https://a.com", "https://b.com"]


class TestSettings:
    def test_lee_del_entorno_con_prefijo(self, monkeypatch):
        monkeypatch.setenv("RAG_API_KEY", "desde-entorno")
        monkeypatch.setenv("RAG_TOP_K", "12")
        s = Settings()
        assert s.api_key == "desde-entorno"
        assert s.top_k == 12

    def test_get_settings_cachea(self):
        assert get_settings() is get_settings()

    def test_presupuesto_del_verificador_cubre_el_contexto_completo(self):
        s = Settings()
        # Con top_k fuentes de chunk_size caracteres, el verificador debe
        # poder ver una parte representativa de todas, no solo del principio.
        assert s.verify_context_chars >= s.top_k * 1000
