"""Regresión del error Celepsa→Pluz.

El contrato de LA ARENA S.A. con Pluz Energía transcribe en su cláusula
primera las tablas de potencia de los "Contratos Primigenios" con Orygen y
Celepsa. El asistente presentó la tabla de Celepsa como potencia contratada de
Pluz: las cifras eran correctas y la empresa equivocada. Estos tests fijan las
dos defensas que lo impiden — la advertencia de atribución en el contexto y la
conversión de las tablas HTML del OCR — con el texto literal del documento.
"""

from __future__ import annotations

from rag.graph.agent import attribution_warning, format_context
from rag.textnorm import find_third_parties, html_tables_to_markdown

from .conftest import make_retrieved

# Texto literal del vault (PZPE_20205467603_20250930_9762_00_f38c6fb05ede.md).
TABLA_CELEPSA_HTML = (
    "1.5 Con fecha 18 de julio de 2024, el **Cliente** suscribió un contrato de "
    "suministro de potencia y energía con la empresa Compañía Eléctrica El Platanal "
    "S.A. (“Celepsa”) para atender la demanda eléctrica del **Cliente** en la UM La "
    "Arena por encima de la potencia contratada suministrada por Orygen con vigencia "
    "hasta el 31 de diciembre de 2031. El citado contrato contempla la siguiente "
    "potencia contratada:\n\n"
    "<table><thead><tr><th>Año</th><th>Potencia contratada (MW)</th></tr></thead>"
    "<tbody><tr><td>2024</td><td>3.0</td></tr><tr><td>2025</td><td>3.5</td></tr>"
    "<tr><td>2026</td><td>4.5</td></tr></tbody>"
)


class TestTablasHtmlAMarkdown:
    def test_convierte_la_tabla_real_del_contrato(self):
        out = html_tables_to_markdown(TABLA_CELEPSA_HTML)
        assert "| Año | Potencia contratada (MW) |" in out
        assert "| --- | --- |" in out
        assert "| 2026 | 4.5 |" in out
        assert "<table>" not in out

    def test_tolera_la_tabla_sin_cerrar_del_salto_de_pagina(self):
        """El OCR corta la tabla cuando el documento cambia de página: la del
        contrato real termina en </tbody> y nunca cierra </table>."""
        assert "</table>" not in TABLA_CELEPSA_HTML
        assert "| 2024 | 3.0 |" in html_tables_to_markdown(TABLA_CELEPSA_HTML)

    def test_sin_tablas_devuelve_el_texto_intacto(self):
        texto = "Cláusula Segunda.- Marco legal, sin tabla alguna."
        assert html_tables_to_markdown(texto) == texto

    def test_escapa_los_pipes_para_no_romper_la_fila(self):
        out = html_tables_to_markdown("<table><tr><td>a|b</td><td>c</td></tr></table>")
        assert r"a\|b" in out

    def test_sin_th_no_inventa_nombres_de_columna(self):
        out = html_tables_to_markdown("<table><tr><td>2024</td><td>3.0</td></tr></table>")
        assert "| 2024 | 3.0 |" in out
        assert out.count("---") >= 1


class TestTerceros:
    def test_detecta_las_empresas_ajenas_al_documento(self):
        terceros = find_third_parties(TABLA_CELEPSA_HTML, ["Pluz Energía", "LA ARENA S.A."])
        assert any("Platanal" in t for t in terceros)

    def test_no_señala_a_las_partes_del_propio_documento(self):
        texto = "PLUZ ENERGÍA PERÚ S.A.A. y LA ARENA S.A. celebran el presente contrato."
        assert find_third_parties(texto, ["Pluz Energía", "LA ARENA S.A."]) == []

    def test_sin_empresas_no_hay_advertencia(self):
        assert find_third_parties("La potencia contratada es 5 MW.", ["Pluz Energía"]) == []


class TestAdvertenciaEnElContexto:
    def test_el_fragmento_con_terceros_lleva_advertencia(self):
        doc = make_retrieved(
            1, text=TABLA_CELEPSA_HTML, suministrador="Pluz Energía", usuario_libre="LA ARENA S.A."
        )
        aviso = attribution_warning(doc)
        assert "ADVERTENCIA DE ATRIBUCIÓN" in aviso
        assert "Platanal" in aviso
        assert "NO son de Pluz Energía" in aviso

    def test_el_fragmento_limpio_no_la_lleva(self):
        doc = make_retrieved(1, text="La potencia contratada es 5 MW.")
        assert attribution_warning(doc) == ""

    def test_el_contexto_entrega_la_advertencia_y_la_tabla_en_markdown(self):
        doc = make_retrieved(
            1, text=TABLA_CELEPSA_HTML, suministrador="Pluz Energía", usuario_libre="LA ARENA S.A."
        )
        ctx = format_context([doc])
        assert "ADVERTENCIA DE ATRIBUCIÓN" in ctx
        assert "| 2026 | 4.5 |" in ctx
        assert "<table>" not in ctx


class TestLaTablaNuncaPierdeSuDueño:
    """El chunk 1 del contrato real empezaba en `<table>`: la tabla de Celepsa
    viajaba sin la frase que dice de quién es, y ninguna regla de prompt puede
    recuperar un dato que no está en el fragmento."""

    def _documento(self) -> str:
        relleno = "Cláusula de relleno con texto suficiente. " * 60  # ~2.5k
        intro_celepsa = (
            "1.5 Con fecha 18 de julio de 2024, el Cliente suscribió un contrato de "
            "suministro de potencia y energía con la empresa Compañía Eléctrica El "
            "Platanal S.A. (“Celepsa”) para atender la demanda eléctrica del Cliente en "
            "la UM La Arena por encima de la potencia contratada suministrada por Orygen "
            "con vigencia hasta el 31 de diciembre de 2031 (en adelante, el “Contrato con "
            "Celepsa”). El citado contrato contempla la siguiente potencia contratada:"
        )
        tabla = (
            "<table><thead><tr><th>Año</th><th>Potencia contratada (MW)</th></tr></thead>"
            "<tbody><tr><td>2026</td><td>4.5</td></tr></tbody></table>"
        )
        return f"{relleno}\n\n{intro_celepsa}\n\n{tabla}\n"

    def _chunks(self):
        from rag.ingestion.chunker import chunk_document
        from rag.schemas import DocumentMeta

        meta = DocumentMeta(source_file="c.pdf", source_hash="h", suministrador="Pluz Energía")
        return chunk_document("d", self._documento(), meta, max_chars=3200, overlap_chars=400)

    def test_el_chunk_de_la_tabla_conserva_la_frase_que_la_presenta(self):
        con_tabla = [c for c in self._chunks() if "4.5" in c.text]
        assert con_tabla, "la tabla debe estar en algún chunk"
        for c in con_tabla:
            assert "Celepsa" in c.text, "la tabla viaja sin decir de quién es"

    def test_ningun_chunk_empieza_con_una_tabla_huerfana(self):
        for c in self._chunks():
            assert not c.text.lstrip().startswith("<table"), (
                "un chunk que abre con <table> pierde la atribución de sus cifras"
            )

    def test_el_overlap_arrastra_algo_aunque_el_bloque_previo_lo_exceda(self):
        """El bucle rompía en la primera iteración cuando el párrafo anterior
        medía más que overlap_chars, y el chunk siguiente nacía sin contexto."""
        from rag.ingestion.chunker import chunk_document
        from rag.schemas import DocumentMeta

        cuerpo = "A" * 3000 + "\n\n" + "B" * 600 + "\n\n" + "C" * 3000
        chunks = chunk_document(
            "d",
            cuerpo,
            DocumentMeta(source_file="c.pdf", source_hash="h"),
            max_chars=3200,
            overlap_chars=400,
        )
        assert len(chunks) > 1
        assert any("B" in c.text for c in chunks[1:]), "el bloque intermedio se perdió"


class TestTablasGrandesYTrasEncabezado:
    """Las dos rutas que el arrastre del empaquetador no cubría: una tabla
    mayor que max_chars (troceado duro) y una tabla justo después de un
    encabezado markdown (corte semántico)."""

    def _chunks(self, cuerpo: str, max_chars: int = 3200):
        from rag.ingestion.chunker import chunk_document
        from rag.schemas import DocumentMeta

        return chunk_document(
            "d",
            cuerpo,
            DocumentMeta(source_file="c.pdf", source_hash="h"),
            max_chars=max_chars,
            overlap_chars=400,
        )

    def test_tabla_gigante_conserva_la_frase_que_la_introduce(self):
        intro = "El contrato con Celepsa contempla la siguiente potencia contratada:"
        filas = "".join(f"<tr><td>{a}</td><td>3.5</td></tr>" for a in range(2024, 2400))
        cuerpo = f"{'relleno. ' * 100}\n\n{intro}\n\n<table>{filas}</table>\n"
        chunks = self._chunks(cuerpo)
        con_tabla = [c for c in chunks if "<tr>" in c.text or "3.5" in c.text]
        assert con_tabla
        assert "Celepsa" in con_tabla[0].text, "la tabla gigante perdió su leyenda"

    def test_tabla_tras_encabezado_conserva_contexto_anterior(self):
        cuerpo = (
            "La potencia contratada con Celepsa es la que sigue:\n\n"
            "## Anexo 1\n\n"
            "<table><tr><td>2026</td><td>4.5</td></tr></table>\n"
        )
        chunks = self._chunks(cuerpo)
        con_tabla = [c for c in chunks if "4.5" in c.text]
        assert con_tabla
        assert "Celepsa" in con_tabla[0].text

    def test_ningun_chunk_abre_con_tabla_salvo_que_el_documento_empiece_asi(self):
        cuerpo = (
            "Introducción con su frase.\n\n## Sección\n\n"
            "<table><tr><td>a</td></tr></table>\n\n"
            + "texto. "
            * 600
            + "\n\nOtra frase que presenta la tabla:\n\n"
            "<table><tr><td>b</td></tr></table>\n"
        )
        chunks = self._chunks(cuerpo)
        for c in chunks[1:]:
            assert not c.text.lstrip().startswith("<table")
