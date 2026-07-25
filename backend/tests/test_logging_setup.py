"""El formatter JSON corre en cada línea de log de producción y estaba al 0%."""

from __future__ import annotations

import logging

import orjson

from rag.logging_setup import JsonFormatter, setup_logging


def record(msg="hola", level=logging.INFO, args=(), exc_info=None, **extra):
    r = logging.LogRecord("rag.test", level, "f.py", 10, msg, args, exc_info)
    for k, v in extra.items():
        setattr(r, k, v)
    return r


class TestJsonFormatter:
    def test_produce_json_valido(self):
        out = orjson.loads(JsonFormatter().format(record()))
        assert out["msg"] == "hola"
        assert out["level"] == "INFO"
        assert out["logger"] == "rag.test"
        assert "ts" in out

    def test_interpola_los_argumentos(self):
        out = orjson.loads(JsonFormatter().format(record("indexados %d de %d", args=(3, 10))))
        assert out["msg"] == "indexados 3 de 10"

    def test_incluye_el_traceback(self):
        try:
            raise ValueError("boom")
        except ValueError:
            import sys

            out = orjson.loads(JsonFormatter().format(record(exc_info=sys.exc_info())))
        assert "ValueError: boom" in out["exc"]

    def test_agrega_campos_extra(self):
        out = orjson.loads(JsonFormatter().format(record(extra_fields={"doc_id": "contratos/a"})))
        assert out["doc_id"] == "contratos/a"

    def test_extra_no_dict_se_ignora(self):
        out = orjson.loads(JsonFormatter().format(record(extra_fields="no soy dict")))
        assert out["msg"] == "hola"

    def test_un_payload_no_serializable_no_tumba_el_logging(self):
        """`orjson.dumps` lanza ante un objeto arbitrario: si eso escapa del
        formatter, revienta en producción en cada línea de log."""
        out = JsonFormatter().format(record(extra_fields={"obj": object()}))
        assert isinstance(out, str)
        assert "hola" in out

    def test_mensaje_con_acentos(self):
        out = orjson.loads(JsonFormatter().format(record("energía eléctrica")))
        assert out["msg"] == "energía eléctrica"


class TestSetupLogging:
    def test_instala_un_unico_handler_json(self):
        setup_logging("INFO")
        root = logging.getLogger()
        assert len(root.handlers) == 1
        assert isinstance(root.handlers[0].formatter, JsonFormatter)

    def test_respeta_el_nivel(self):
        setup_logging("DEBUG")
        assert logging.getLogger().level == logging.DEBUG

    def test_silencia_el_ruido_de_uvicorn_y_httpx(self):
        setup_logging("INFO")
        assert logging.getLogger("uvicorn.access").level == logging.WARNING
        assert logging.getLogger("httpx").level == logging.WARNING

    def test_es_idempotente(self):
        setup_logging("INFO")
        setup_logging("INFO")
        assert len(logging.getLogger().handlers) == 1
