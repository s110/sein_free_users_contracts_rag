"""HybridStore.search: dedup, fusión RRF y reescritura de score.

Es lo que decide qué ve el LLM, y estaba al 0%.
"""

from __future__ import annotations

from rag.retrieval.store import HybridStore


class Hit:
    def __init__(self, chunk_id, text="texto", score=0.5, **payload):
        self.id = chunk_id
        self.score = score
        self.payload = {
            "doc_id": f"doc-{chunk_id}",
            "text": text,
            "source_file": "a.pdf",
            **payload,
        }


class FakeQdrant:
    def __init__(self, dense=(), lexical=(), lexical_raises=False, count=7, scroll_pages=None):
        self._dense = list(dense)
        self._lexical = list(lexical)
        self.lexical_raises = lexical_raises
        self._count = count
        self._scroll_pages = scroll_pages
        self.queries: list[dict] = []

    def query_points(self, collection_name, query, query_filter, limit, with_payload):  # noqa: ARG002
        self.queries.append({"filter": query_filter, "limit": limit})
        return type("R", (), {"points": self._dense})()

    def scroll(
        self,
        collection_name,
        scroll_filter=None,
        limit=None,
        with_payload=None,
        with_vectors=None,
        offset=None,
    ):  # noqa: ARG002
        if self._scroll_pages is not None:
            page = self._scroll_pages.pop(0) if self._scroll_pages else ([], None)
            return page
        if self.lexical_raises:
            raise RuntimeError("índice full-text ausente")
        return self._lexical, None

    def count(self, collection, exact=True):  # noqa: ARG002
        return type("C", (), {"count": self._count})()


class FakeEmbedder:
    def embed_one(self, text):  # noqa: ARG002
        return [0.1, 0.2, 0.3]


def store(**kw) -> HybridStore:
    return HybridStore(FakeQdrant(**kw), FakeEmbedder(), "c", dense_candidates=5, text_candidates=5)


class TestSearch:
    def test_fusiona_denso_y_lexico(self):
        s = store(dense=[Hit("a"), Hit("b")], lexical=[Hit("c")])
        results = s.search("potencia contratada", top_k=10)
        assert {r.chunk_id for r in results} == {"a", "b", "c"}

    def test_no_duplica_un_chunk_presente_en_ambos(self):
        s = store(dense=[Hit("a"), Hit("b")], lexical=[Hit("a")])
        results = s.search("q", top_k=10)
        assert len(results) == 2
        assert len({r.chunk_id for r in results}) == 2

    def test_lo_que_aparece_en_ambos_rankings_sube(self):
        """Es el punto de la fusión RRF: la coincidencia doble gana."""
        s = store(dense=[Hit("solo-denso"), Hit("ambos")], lexical=[Hit("ambos")])
        results = s.search("q", top_k=10)
        assert results[0].chunk_id == "ambos"

    def test_respeta_top_k(self):
        s = store(dense=[Hit(str(i)) for i in range(10)])
        assert len(s.search("q", top_k=3)) == 3

    def test_el_score_pasa_a_ser_el_de_la_fusion(self):
        s = store(dense=[Hit("a", score=0.99)])
        result = s.search("q", top_k=1)[0]
        assert result.score != 0.99
        assert 0 < result.score < 1

    def test_un_fallo_del_lexico_no_tumba_la_busqueda(self):
        """El índice full-text puede faltar en una colección vieja; la
        búsqueda densa tiene que seguir respondiendo."""
        s = store(dense=[Hit("a")], lexical_raises=True)
        assert [r.chunk_id for r in s.search("q", top_k=5)] == ["a"]

    def test_sin_resultados_devuelve_lista_vacia(self):
        assert store().search("q") == []

    def test_aplica_solo_los_filtros_de_la_allowlist(self):
        client = FakeQdrant(dense=[Hit("a")])
        s = HybridStore(client, FakeEmbedder(), "c")
        s.search("q", filters={"tipo": "contrato", "campo_inventado": "x"})
        keys = [c.key for c in client.queries[0]["filter"].must]
        assert keys == ["tipo"]

    def test_sin_filtros_no_manda_filtro(self):
        client = FakeQdrant(dense=[Hit("a")])
        HybridStore(client, FakeEmbedder(), "c").search("q")
        assert client.queries[0]["filter"] is None

    def test_mapea_la_metadata_al_chunk(self):
        s = store(dense=[Hit("a", ruc_usuario_libre="20467534026", tipo="contrato", page_start=3)])
        result = s.search("q", top_k=1)[0]
        assert result.ruc_usuario_libre == "20467534026"
        assert result.tipo == "contrato"
        assert result.page_start == 3

    def test_payload_incompleto_no_revienta(self):
        hit = Hit("a")
        hit.payload = {}
        s = store(dense=[hit])
        result = s.search("q", top_k=1)[0]
        assert result.doc_id == ""
        assert result.text == ""


class TestCount:
    def test_devuelve_el_conteo_exacto(self):
        assert store(count=123).count() == 123


class TestListDocuments:
    def test_deduplica_por_doc_id(self):
        page = (
            [Hit("1", doc_id="d1"), Hit("2", doc_id="d1"), Hit("3", doc_id="d2")],
            None,
        )
        s = HybridStore(FakeQdrant(scroll_pages=[page]), FakeEmbedder(), "c")
        docs = s.list_documents()
        assert len(docs) == 2

    def test_ordena_por_doc_id(self):
        page = ([Hit("1", doc_id="z"), Hit("2", doc_id="a")], None)
        s = HybridStore(FakeQdrant(scroll_pages=[page]), FakeEmbedder(), "c")
        assert [d["doc_id"] for d in s.list_documents()] == ["a", "z"]

    def test_pagina_hasta_agotar(self):
        pages = [
            ([Hit("1", doc_id="a")], "cursor"),
            ([Hit("2", doc_id="b")], None),
        ]
        s = HybridStore(FakeQdrant(scroll_pages=pages), FakeEmbedder(), "c")
        assert len(s.list_documents()) == 2

    def test_coleccion_vacia(self):
        s = HybridStore(FakeQdrant(scroll_pages=[([], None)]), FakeEmbedder(), "c")
        assert s.list_documents() == []
