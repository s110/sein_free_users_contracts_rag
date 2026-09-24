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
        # tipo pasó a MatchAny (grafías mezcladas en el índice); el resto
        # sigue siendo igualdad exacta.
        f = _build_filter({"ruc_usuario_libre": "20348133889", "fecha_suscripcion": "2026-01-01"})
        assert {c.key for c in f.must} == {"ruc_usuario_libre", "fecha_suscripcion"}
        assert all(isinstance(c.match, models.MatchValue) for c in f.must)


class TestCitasFantasma:
    def test_borra_marcadores_sin_fuente(self):
        answer = "La potencia es 500 kW [2], según la Tercera Adenda [3] y el PPA [1]."
        out = strip_ghost_citations(answer, 1)
        assert "[2]" not in out and "[3]" not in out
        assert "[1]" in out
        # La afirmación queda intacta: solo cae el marcador falso.
        assert "500 kW" in out and "Tercera Adenda" in out

    def test_conserva_todas_cuando_existen(self):
        answer = "Cláusula séptima [1] y anexo B [2]."
        assert strip_ghost_citations(answer, 2) == answer

    def test_sin_fuentes_borra_todo_marcador(self):
        out = strip_ghost_citations("No hay contexto [1].", 0)
        assert "[1]" not in out
        assert "No hay contexto" in out


class TestFiltroTipoInsensibleAMayusculas:
    def test_tipo_matchea_ambas_grafias(self):
        f = _build_filter({"tipo": "Contrato"})
        cond = f.must[0]
        assert isinstance(cond.match, models.MatchAny)
        assert set(cond.match.any) == {"contrato", "Contrato"}
