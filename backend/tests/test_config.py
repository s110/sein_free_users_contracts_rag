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

    def test_avisa_de_que_documents_queda_abierto_sin_clave(self, caplog):
        """El aviso decía que `/api/documents` "queda inaccesible", y era falso
        en el 100% de los casos en que se emitía: sin clave el endpoint no se
        cierra, se abre — y publica el RUC de cada usuario libre."""
        Settings(api_key="", allow_anonymous=True, public_chat=True).validate_runtime()
        assert "ABIERTOS" in caplog.text
        assert "inaccesible" not in caplog.text


class TestTrustedProxies:
    def test_por_defecto_confia_en_redes_privadas_y_loopback(self):
        s = Settings()
        assert s.is_trusted_proxy("127.0.0.1")
        assert s.is_trusted_proxy("172.18.0.5")
        assert s.is_trusted_proxy("10.1.2.3")
        assert s.is_trusted_proxy("192.168.1.9")
        assert s.is_trusted_proxy("::1")

    def test_no_confia_en_una_ip_publica(self):
        assert not Settings().is_trusted_proxy("203.0.113.7")

    def test_lo_que_no_es_una_ip_nunca_es_de_confianza(self):
        s = Settings()
        assert not s.is_trusted_proxy("testclient")
        assert not s.is_trusted_proxy("")

    def test_se_puede_estrechar_la_lista(self):
        s = Settings(trusted_proxies="172.18.0.0/16")
        assert s.is_trusted_proxy("172.18.0.5")
        assert not s.is_trusted_proxy("127.0.0.1")

    def test_un_cidr_ilegible_se_ignora_sin_tumbar_el_arranque(self, caplog):
        """Un typo debe estrechar la confianza, nunca abrirla ni dejar el
        servicio abajo."""
        s = Settings(trusted_proxies="no-es-un-cidr, 127.0.0.0/8")
        assert s.is_trusted_proxy("127.0.0.1")
        assert not s.is_trusted_proxy("10.0.0.1")
        assert "no es un CIDR válido" in caplog.text


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
