"""Consultas multi-documento: num_docs en analyze, retrieval con diversidad
por doc_id y la nota de sistema que impide el "solo existe un contrato".
"""

from __future__ import annotations

from qdrant_client import models

from rag.retrieval.store import HybridStore, _build_filter

from .conftest import make_retrieved
from .test_agent_graph import agent
from .test_store import FakeEmbedder, FakeQdrant, Hit


class TestAnalyzeNumDocs:
    async def test_extrae_y_activa_num_docs(self):
        a = agent(json_replies=['{"search_query": "q", "num_docs": 5}'])
        out = await a.analyze({"question": "dame 5 contratos de Orygen", "history": []})
        assert out["num_docs"] == 5

    async def test_clampa_a_ocho(self):
        a = agent(json_replies=['{"search_query": "q", "num_docs": 50}'])
        out = await a.analyze({"question": "dame 50 contratos", "history": []})
        assert out["num_docs"] == 8

    async def test_uno_o_basura_no_activan_el_modo(self):
        a = agent(json_replies=['{"search_query": "q", "num_docs": 1}'])
        assert (await a.analyze({"question": "q", "history": []}))["num_docs"] is None
        a = agent(json_replies=['{"search_query": "q", "num_docs": "varios"}'])
        assert (await a.analyze({"question": "q", "history": []}))["num_docs"] is None


class TestRetrieveDiverso:
    async def test_usa_search_diverse_y_reporta_cifras(self):
        docs = [make_retrieved(1), make_retrieved(2), make_retrieved(3)]
        a = agent(store_results=[docs])
        a.store.distinct_total = 12
        out = await a.retrieve(
            {"search_query": "contratos orygen", "num_docs": 5,
             "filters": {"suministrador": "orygen"}}
        )
        assert a.store.calls[0]["diverse"] is True
        assert a.store.calls[0]["n_docs"] == 5
        # 3 doc_id distintos en contexto, 12 en el índice, 5 pedidos
        assert "3 documento(s) distinto(s)" in out["multi_doc_note"]
        assert "12 documento(s)" in out["multi_doc_note"]
        assert "pidió 5 documentos" in out["multi_doc_note"]

    async def test_cero_docs_con_filtros_reintenta_sin_filtros(self):
        docs = [make_retrieved(1)]
        a = agent(store_results=[[], docs])
        out = await a.retrieve(
            {"search_query": "q", "num_docs": 2, "filters": {"suministrador": "orygen"}}
        )
        assert len(a.store.calls) == 2
        assert a.store.calls[1]["filters"] is None
        assert out["documents"] == docs


class TestSearchDiverseStore:
    def _store(self, dense):
        client = FakeQdrant(dense=dense, lexical=[])
        return HybridStore(client, FakeEmbedder(), "c")

    def test_maximo_per_doc_fragmentos_por_documento(self):
        # 4 chunks del mismo doc dominan el ranking; solo deben pasar 2 y
        # abrirse espacio para el segundo documento.
        dense = [
            Hit("a1", score=0.9, doc_id="docA"),
            Hit("a2", score=0.8, doc_id="docA"),
            Hit("a3", score=0.7, doc_id="docA"),
            Hit("a4", score=0.6, doc_id="docA"),
            Hit("b1", score=0.5, doc_id="docB"),
            Hit("b2", score=0.4, doc_id="docB"),
        ]
        results = self._store(dense).search_diverse("q", n_docs=2, per_doc=2)
        por_doc: dict[str, int] = {}
        for r in results:
            por_doc[r.doc_id] = por_doc.get(r.doc_id, 0) + 1
        assert por_doc == {"docA": 2, "docB": 2}

    def test_no_abre_mas_documentos_que_los_pedidos(self):
        dense = [Hit(f"c{i}", score=1 - i / 10, doc_id=f"doc{i}") for i in range(6)]
        results = self._store(dense).search_diverse("q", n_docs=3, per_doc=1)
        assert len({r.doc_id for r in results}) == 3


class TestFiltrosNuevos:
    def test_suministrador_va_al_espejo_normalizado(self):
        f = _build_filter({"suministrador": "Orygen Perú"})
        cond = f.must[0]
        assert cond.key == "suministrador_norm"
        assert isinstance(cond.match, models.MatchText)
        assert cond.match.text == "orygen peru"

    def test_doc_id_en_lista_usa_match_any(self):
        f = _build_filter({"doc_id": ["contratos/a", "contratos/b"]})
        cond = f.must[0]
        assert isinstance(cond.match, models.MatchAny)
        assert cond.match.any == ["contratos/a", "contratos/b"]
