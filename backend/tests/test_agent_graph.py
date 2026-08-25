"""Nodos del grafo agéntico.

Ninguno estaba testeado porque `ContractsAgent.__init__` construía los
ChatOllama directamente. Ahora son inyectables.
"""

from __future__ import annotations

import pytest

from rag.config import Settings
from rag.graph.agent import ContractsAgent, truncate_context

from .conftest import make_retrieved


class FakeLLM:
    """Responde por guion; registra los prompts que recibe."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts: list[str] = []

    async def ainvoke(self, messages):
        self.prompts.append(messages[-1].content)
        item = self.replies.pop(0) if self.replies else ""
        if isinstance(item, Exception):
            raise item
        return type("R", (), {"content": item})()


class FakeStore:
    def __init__(self, results):
        # results: lista de listas, una por llamada consecutiva
        self.results = list(results)
        self.calls: list[dict] = []

    def search(self, query, top_k=6, filters=None):
        self.calls.append({"query": query, "top_k": top_k, "filters": filters})
        return self.results.pop(0) if self.results else []

    def search_diverse(self, query, *, n_docs, per_doc=2, filters=None):
        self.calls.append(
            {
                "query": query,
                "n_docs": n_docs,
                "per_doc": per_doc,
                "filters": filters,
                "diverse": True,
            }
        )
        return self.results.pop(0) if self.results else []

    def count_distinct_docs(self, filters=None):  # noqa: ARG002
        return getattr(self, "distinct_total", 0)


def agent(*, json_replies=(), gen_replies=(), store_results=(), **overrides) -> ContractsAgent:
    settings = Settings(api_key="k", **overrides)
    return ContractsAgent(
        settings,
        FakeStore(store_results),
        llm_json=FakeLLM(json_replies),
        llm_generate=FakeLLM(gen_replies),
    )


class TestAnalyze:
    async def test_extrae_query_y_filtros(self):
        a = agent(
            json_replies=[
                '{"search_query": "potencia contratada ACME", "filters": {"tipo": "contrato"}}'
            ]
        )
        out = await a.analyze({"question": "¿Potencia de ACME?", "history": []})
        assert out["search_query"] == "potencia contratada ACME"
        assert out["filters"] == {"tipo": "contrato"}

    async def test_respuesta_ilegible_cae_a_la_pregunta_original(self):
        a = agent(json_replies=["el modelo se puso a divagar"])
        out = await a.analyze({"question": "¿Potencia?", "history": []})
        assert out["search_query"] == "¿Potencia?"

    async def test_los_filtros_del_usuario_ganan_sobre_los_extraidos(self):
        a = agent(json_replies=['{"search_query": "q", "filters": {"tipo": "adenda"}}'])
        out = await a.analyze(
            {"question": "q", "history": [], "user_filters": {"tipo": "contrato"}}
        )
        assert out["filters"]["tipo"] == "contrato"

    async def test_descarta_filtros_vacios(self):
        a = agent(json_replies=['{"search_query": "q", "filters": {"tipo": "", "x": null}}'])
        out = await a.analyze({"question": "q", "history": []})
        assert out["filters"] == {}

    async def test_filtros_no_dict_no_revientan(self):
        a = agent(json_replies=['{"search_query": "q", "filters": "contrato"}'])
        out = await a.analyze({"question": "q", "history": []})
        assert out["filters"] == {}


class TestRetrieve:
    async def test_devuelve_los_documentos(self):
        docs = [make_retrieved(1)]
        a = agent(store_results=[docs])
        out = await a.retrieve({"search_query": "q", "filters": None})
        assert out["documents"] == docs

    async def test_reintenta_sin_filtros_cuando_el_filtro_automatico_no_da_nada(self):
        """Un RUC mal extraído por el modelo no debe dejar al usuario sin
        respuesta."""
        docs = [make_retrieved(1)]
        a = agent(store_results=[[], docs])
        out = await a.retrieve({"search_query": "q", "filters": {"ruc_usuario_libre": "999"}})
        assert out["documents"] == docs
        assert a.store.calls[1]["filters"] is None

    async def test_no_reintenta_si_el_filtro_lo_puso_el_usuario(self):
        """Si el usuario filtró explícitamente, 0 resultados es la respuesta
        correcta, no un error a corregir."""
        a = agent(store_results=[[], [make_retrieved(1)]])
        out = await a.retrieve(
            {
                "search_query": "q",
                "filters": {"tipo": "adenda"},
                "user_filters": {"tipo": "adenda"},
            }
        )
        assert out["documents"] == []
        assert len(a.store.calls) == 1


class TestGrade:
    async def test_conserva_los_relevantes_y_descarta_el_resto(self):
        a = agent(json_replies=['{"relevant": true}', '{"relevant": false}'])
        out = await a.grade({"question": "q", "documents": [make_retrieved(1), make_retrieved(2)]})
        assert [d.chunk_id for d in out["relevant_documents"]] == ["c1"]

    async def test_ante_json_ilegible_es_permisivo(self):
        """Fallback deliberado: si el grader de 4B no devuelve JSON, decide el
        generador. Sin este test, invertir el default convierte `rewrite` y
        `no_context` en código muerto sin que nada lo detecte."""
        a = agent(json_replies=["no es json", "tampoco"])
        out = await a.grade({"question": "q", "documents": [make_retrieved(1), make_retrieved(2)]})
        assert len(out["relevant_documents"]) == 2

    async def test_sin_documentos_no_llama_al_modelo(self):
        a = agent(json_replies=[])
        out = await a.grade({"question": "q", "documents": []})
        assert out["relevant_documents"] == []
        assert a.llm_json.prompts == []


class TestAfterGrade:
    def test_con_relevantes_va_a_generar(self):
        a = agent()
        assert a.after_grade({"relevant_documents": [make_retrieved(1)]}) == "generate"

    def test_sin_relevantes_reformula(self):
        a = agent()
        assert a.after_grade({"relevant_documents": [], "rewrites": 0}) == "rewrite"

    def test_agotados_los_reintentos_admite_no_saber(self):
        a = agent(max_query_rewrites=2)
        assert a.after_grade({"relevant_documents": [], "rewrites": 2}) == "no_context"

    def test_el_limite_de_reformulaciones_es_configurable(self):
        a = agent(max_query_rewrites=0)
        assert a.after_grade({"relevant_documents": [], "rewrites": 0}) == "no_context"


class TestRewrite:
    async def test_produce_una_query_nueva_e_incrementa_el_contador(self):
        a = agent(json_replies=['{"search_query": "potencia MW contrato ACME"}'])
        out = await a.rewrite({"search_query": "q", "question": "p", "rewrites": 0})
        assert out["search_query"] == "potencia MW contrato ACME"
        assert out["rewrites"] == 1

    async def test_respuesta_vacia_conserva_la_query_anterior(self):
        a = agent(json_replies=['{"search_query": ""}'])
        out = await a.rewrite({"search_query": "original", "question": "p", "rewrites": 1})
        assert out["search_query"] == "original"
        assert out["rewrites"] == 2


class TestGenerate:
    async def test_construye_el_contexto_con_las_fuentes_numeradas(self):
        a = agent(gen_replies=["La potencia es 10 MW [1]."])
        out = await a.generate(
            {
                "question": "¿Potencia?",
                "relevant_documents": [make_retrieved(1), make_retrieved(2)],
                "history": [],
            }
        )
        assert out["answer"] == "La potencia es 10 MW [1]."
        prompt = a.llm_generate.prompts[0]
        assert "[1]" in prompt and "[2]" in prompt

    async def test_incluye_el_historial_como_turnos(self):
        a = agent(gen_replies=["ok"])
        history = [{"role": "user", "content": "antes"}, {"role": "assistant", "content": "sí"}]
        await a.generate(
            {"question": "q", "relevant_documents": [make_retrieved(1)], "history": history}
        )
        assert a.llm_generate.prompts  # no revienta con historial presente


EXTRACCION_UNA = '{"afirmaciones": [{"texto": "la potencia es 5 MW", "citas": [1]}]}'


class TestVerify:
    async def test_todas_sustentadas_marca_fundamentada(self):
        a = agent(
            json_replies=[EXTRACCION_UNA, '{"veredictos": [{"i": 1, "estado": "sustentada"}]}']
        )
        out = await a.verify({"relevant_documents": [make_retrieved(1)], "answer": "5 MW [1]"})
        assert out["grounded"] is True
        assert (out["claims_ok"], out["claims_total"]) == (1, 1)
        assert out["claim_issues"] == []

    async def test_una_refutada_marca_no_fundamentada_y_la_reporta(self):
        a = agent(
            json_replies=[
                EXTRACCION_UNA,
                '{"veredictos": [{"i": 1, "estado": "refutada",'
                ' "motivo": "la tabla es de Celepsa"}]}',
            ]
        )
        out = await a.verify({"relevant_documents": [make_retrieved(1)], "answer": "5 MW [1]"})
        assert out["grounded"] is False
        assert out["claims_ok"] == 0
        assert out["claim_issues"][0]["estado"] == "refutada"
        assert "Celepsa" in out["claim_issues"][0]["motivo"]

    async def test_ausente_no_concluye_pero_no_certifica(self):
        """Ni verde ni rojo: el dato no está en el fragmento que la respuesta
        citó, y certificarlo sería peor que admitir que no se pudo comprobar."""
        a = agent(json_replies=[EXTRACCION_UNA, '{"veredictos": [{"i": 1, "estado": "ausente"}]}'])
        out = await a.verify({"relevant_documents": [make_retrieved(1)], "answer": "5 MW [1]"})
        assert out["grounded"] is None
        assert out["claim_issues"][0]["estado"] == "ausente"

    async def test_afirmacion_sin_cita_queda_sin_verificar(self):
        a = agent(json_replies=['{"afirmaciones": [{"texto": "algo", "citas": []}]}'])
        out = await a.verify({"relevant_documents": [make_retrieved(1)], "answer": "algo"})
        assert out["claim_issues"][0]["estado"] == "sin_cita"
        assert out["grounded"] is None
        # Sin cita no hay a qué fragmento preguntar: una sola llamada al modelo.
        assert len(a.llm_json.prompts) == 1

    async def test_cita_fuera_de_rango_no_se_verifica_contra_nada(self):
        a = agent(json_replies=['{"afirmaciones": [{"texto": "algo", "citas": [7]}]}'])
        out = await a.verify({"relevant_documents": [make_retrieved(1)], "answer": "algo [7]"})
        assert out["claim_issues"][0]["estado"] == "sin_cita"

    async def test_json_ilegible_deja_el_veredicto_en_none(self):
        a = agent(json_replies=["vaya usted a saber"])
        out = await a.verify({"relevant_documents": [make_retrieved(1)], "answer": "x"})
        assert out["grounded"] is None
        assert out["claims_total"] == 0

    async def test_un_fallo_del_modelo_no_tumba_la_respuesta(self):
        a = agent(json_replies=[RuntimeError("Ollama caído")])
        out = await a.verify({"relevant_documents": [make_retrieved(1)], "answer": "x"})
        assert out["grounded"] is None

    async def test_cada_afirmacion_se_contrasta_SOLO_con_el_fragmento_que_cita(self):
        """La propiedad que impide el error Celepsa→Pluz: con el contexto
        entero delante bastaba que la cifra apareciera en CUALQUIER documento.
        El refutador solo puede ver el fragmento citado."""
        docs = [make_retrieved(n, text=f"contenido {n}") for n in range(1, 4)]
        a = agent(
            json_replies=[
                '{"afirmaciones": [{"texto": "dato del tercero", "citas": [3]}]}',
                '{"veredictos": [{"i": 1, "estado": "sustentada"}]}',
            ]
        )
        await a.verify({"relevant_documents": docs, "answer": "dato [3]"})
        prompt_refutacion = a.llm_json.prompts[1]
        assert "contenido 3" in prompt_refutacion
        assert "contenido 1" not in prompt_refutacion
        assert "contenido 2" not in prompt_refutacion

    async def test_basta_que_uno_de_los_fragmentos_citados_la_sustente(self):
        docs = [make_retrieved(1), make_retrieved(2)]
        a = agent(
            json_replies=[
                '{"afirmaciones": [{"texto": "x", "citas": [1, 2]}]}',
                '{"veredictos": [{"i": 1, "estado": "ausente"}]}',
                '{"veredictos": [{"i": 1, "estado": "sustentada"}]}',
            ]
        )
        out = await a.verify({"relevant_documents": docs, "answer": "x [1][2]"})
        assert out["grounded"] is True


class TestNoContext:
    async def test_responde_honestamente_y_sin_fuentes(self):
        a = agent()
        out = await a.no_context({})
        assert out["no_context"] is True
        assert out["relevant_documents"] == []
        assert out["answer"]


class TestTruncateContext:
    def test_reparte_el_presupuesto_entre_las_fuentes(self):
        docs = [make_retrieved(n, text="y" * 5000) for n in range(1, 5)]
        out = truncate_context(docs, 4000)
        # las cuatro fuentes aparecen, ninguna se pierde entera
        for n in range(1, 5):
            assert f"[{n}]" in out

    def test_sin_documentos_devuelve_vacio(self):
        assert truncate_context([], 1000) == ""

    def test_no_recorta_lo_que_ya_cabe(self):
        docs = [make_retrieved(1, text="corto")]
        assert "corto" in truncate_context(docs, 10000)


@pytest.fixture(autouse=True)
def _no_red(monkeypatch):
    """Ningún test de este módulo puede abrir una conexión a Ollama."""

    def boom(*a, **kw):
        raise AssertionError("un test intentó construir un ChatOllama real")

    monkeypatch.setattr("rag.llm.ChatOllama", boom)
