"""Filtro por razón social (espejo normalizado) y saneo de citas fantasma."""

from __future__ import annotations

from qdrant_client import models

from rag.graph.agent import strip_ghost_citations
from rag.retrieval.store import _build_filter
from rag.schemas import normalize_text_filter


class TestNormalizacion:
    def test_quita_tildes_mayusculas_y_espacios(self):
        assert (
            normalize_text_filter("  LAVANDERÍA   INDUSTRIAL  LANDEO S.A.C. ")
            == "lavanderia industrial landeo s.a.c."
        )


class TestFiltroRazonSocial:
    def test_usuario_libre_va_como_matchtext_normalizado(self):
        f = _build_filter({"usuario_libre": "Lavandería Landeo"})
        assert f is not None and len(f.must) == 1
        cond = f.must[0]
        assert cond.key == "usuario_libre_norm"
        assert isinstance(cond.match, models.MatchText)
        assert cond.match.text == "lavanderia landeo"

    def test_los_demas_campos_siguen_siendo_match_exacto(self):
        f = _build_filter({"ruc_usuario_libre": "20348133889", "tipo": "adenda"})
        assert {c.key for c in f.must} == {"ruc_usuario_libre", "tipo"}
        assert all(isinstance(c.match, models.MatchValue) for c in f.must)


class TestCitasFantasma:
    def test_borra_marcadores_sin_fuente(self):
        answer = "La potencia es 500 kW [2], según la Tercera Adenda [3] y el PPA [1]."
        assert (
            strip_ghost_citations(answer, 1)
            == "La potencia es 500 kW , según la Tercera Adenda  y el PPA [1]."
        )

    def test_conserva_todas_cuando_existen(self):
        answer = "Cláusula séptima [1] y anexo B [2]."
        assert strip_ghost_citations(answer, 2) == answer

    def test_sin_fuentes_borra_todo_marcador(self):
        assert strip_ghost_citations("No hay contexto [1].", 0) == "No hay contexto ."
