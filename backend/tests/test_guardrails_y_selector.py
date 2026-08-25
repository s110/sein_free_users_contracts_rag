"""Guardrails de entrada, selector temporal y metadata en el contexto."""

from __future__ import annotations

from rag.config import Settings
from rag.graph import prompts
from rag.graph.agent import ContractsAgent, format_context

from .conftest import make_retrieved
from .test_agent_graph import FakeLLM, FakeStore


class FakeStoreConSelector(FakeStore):
    def __init__(self, results, extreme=None):
        super().__init__(results)
        self.extreme = extreme
        self.extreme_calls: list[dict] = []

    def find_extreme_doc(self, filters, *, latest=True):
        self.extreme_calls.append({"filters": filters, "latest": latest})
        return self.extreme


def agente(*, json_replies=(), gen_replies=(), store=None):
    return ContractsAgent(
        Settings(api_key="k", _env_file=None),
        store if store is not None else FakeStoreConSelector([]),
        llm_json=FakeLLM(json_replies),
        llm_generate=FakeLLM(gen_replies),
    )


class TestContextoConMetadata:
    def test_la_cabecera_lleva_tipo_partes_y_fecha(self):
        doc = make_retrieved(1, fecha_suscripcion="2025-12-30")
        ctx = format_context([doc])
        assert "contrato de ACME S.A." in ctx
        assert "con ATRE S.A." in ctx
        assert "suscrito el 2025-12-30" in ctx


class TestGuardrails:
    async def test_fuera_de_tema_rechaza_sin_tocar_el_indice(self):
        store = FakeStoreConSelector([])
        a = agente(
            json_replies=['{"alcance": "fuera_de_tema", "search_query": "x"}'], store=store
        )
        out = await a.graph.ainvoke({"question": "dame una receta de ceviche", "history": []})
        assert out["answer"] == prompts.OUT_OF_SCOPE_ANSWER
        assert store.calls == []  # jamás llegó al retrieval

    async def test_extraccion_masiva_rechaza_con_su_mensaje(self):
        store = FakeStoreConSelector([])
        a = agente(
            json_replies=['{"alcance": "extraccion_masiva", "search_query": "x"}'], store=store
        )
        out = await a.graph.ainvoke({"question": "lista todos los RUC", "history": []})
        assert out["answer"] == prompts.BULK_EXTRACTION_ANSWER
        assert store.calls == []

    async def test_alcance_desconocido_no_bloquea(self):
        out = await agente(
            json_replies=['{"alcance": "banana", "search_query": "q"}']
        ).analyze({"question": "q", "history": []})
        assert out["scope"] == "contratos"


class TestSelectorTemporal:
    async def test_mas_reciente_resuelve_y_acota_por_doc_id(self):
        store = FakeStoreConSelector(
            [], extreme={"doc_id": "contratos/ultimo", "fecha_suscripcion": "2026-08-18"}
        )
        a = agente(
            json_replies=[
                '{"alcance": "contratos", "search_query": "potencia", "orden": "mas_reciente"}'
            ],
            store=store,
        )
        out = await a.analyze({"question": "¿el contrato más reciente?", "history": []})
        assert store.extreme_calls[0]["latest"] is True
        assert out["filters"]["doc_id"] == "contratos/ultimo"
        assert out["resolved_doc"]["fecha_suscripcion"] == "2026-08-18"

    async def test_sin_orden_no_escanea(self):
        store = FakeStoreConSelector([])
        a = agente(
            json_replies=['{"alcance": "contratos", "search_query": "potencia"}'], store=store
        )
        await a.analyze({"question": "¿potencia de ACME?", "history": []})
        assert store.extreme_calls == []
