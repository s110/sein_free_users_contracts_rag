"""Embedder: batching, guarda de longitud y reintentos.

La guarda de longitud es lo único que impide que un chunk reciba el vector de
otro; si se relaja, el retrieval devuelve contratos equivocados con toda
confianza y sin error en ninguna parte.
"""

from __future__ import annotations

import httpx
import pytest

from rag.ingestion.embedder import OllamaEmbedder


def embedder(handler, *, batch_size=2, max_retries=2) -> tuple[OllamaEmbedder, list[float]]:
    slept: list[float] = []
    client = httpx.Client(transport=httpx.MockTransport(handler))
    emb = OllamaEmbedder(
        host="http://ollama:11434",
        model="bge-m3",
        batch_size=batch_size,
        max_retries=max_retries,
        client=client,
        sleep=slept.append,
    )
    return emb, slept


def ok(dim=3):
    def handler(request: httpx.Request) -> httpx.Response:
        import json

        n = len(json.loads(request.content)["input"])
        return httpx.Response(200, json={"embeddings": [[0.1] * dim for _ in range(n)]})

    return handler


class TestEmbed:
    def test_devuelve_un_vector_por_texto(self):
        emb, _ = embedder(ok())
        assert len(emb.embed(["a", "b", "c"])) == 3

    def test_respeta_el_tamano_de_batch(self):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return ok()(request)

        emb, _ = embedder(handler, batch_size=2)
        emb.embed(["a", "b", "c", "d", "e"])
        assert calls["n"] == 3  # 2 + 2 + 1

    def test_embed_one(self):
        emb, _ = embedder(ok())
        assert len(emb.embed_one("hola")) == 3

    def test_dimension_se_detecta(self):
        emb, _ = embedder(ok(dim=1024))
        assert emb.dimension() == 1024

    def test_lista_vacia_no_llama(self):
        def handler(request):  # pragma: no cover
            raise AssertionError("no debería llamarse")

        emb, _ = embedder(handler)
        assert emb.embed([]) == []


class TestGuardaDeLongitud:
    def test_menos_embeddings_que_textos_es_un_error(self):
        """Si esto se relaja y alguien quita el `strict=True` del zip aguas
        abajo, los chunks reciben vectores desalineados."""

        def handler(request):
            return httpx.Response(200, json={"embeddings": [[0.1, 0.2, 0.3]]})

        emb, _ = embedder(handler, batch_size=2, max_retries=0)
        with pytest.raises(RuntimeError):
            emb.embed(["a", "b"])

    def test_respuesta_sin_embeddings_es_un_error(self):
        def handler(request):
            return httpx.Response(200, json={})

        emb, _ = embedder(handler, max_retries=0)
        with pytest.raises(RuntimeError):
            emb.embed(["a"])


class TestReintentos:
    def test_reintenta_y_acaba_bien(self):
        state = {"n": 0}

        def handler(request):
            state["n"] += 1
            if state["n"] < 3:
                return httpx.Response(503)
            return ok()(request)

        emb, slept = embedder(handler, max_retries=3)
        assert len(emb.embed(["a"])) == 1
        assert slept == [1.0, 2.0]  # backoff exponencial

    def test_agota_reintentos_y_lanza(self):
        emb, _ = embedder(lambda r: httpx.Response(503), max_retries=2)
        with pytest.raises(RuntimeError, match="3 intentos"):
            emb.embed(["a"])

    def test_no_duerme_tras_el_ultimo_intento(self):
        """Dormía también tras el intento final: 8 segundos muertos en cada
        fallo definitivo, dentro del event loop de la API."""
        emb, slept = embedder(lambda r: httpx.Response(503), max_retries=2)
        with pytest.raises(RuntimeError):
            emb.embed(["a"])
        assert len(slept) == 2  # 3 intentos, 2 esperas

    def test_conserva_la_causa_original(self):
        emb, _ = embedder(lambda r: httpx.Response(503), max_retries=0)
        with pytest.raises(RuntimeError) as exc:
            emb.embed(["a"])
        assert exc.value.__cause__ is not None
