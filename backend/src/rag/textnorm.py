"""Normalización del texto OCR antes de que lo lea un modelo o un humano.

Dos problemas concretos del corpus de Osinergmin, ambos causa directa de
respuestas mal atribuidas:

1. El OCR emite las tablas como HTML crudo (`<table><tr><td>...`). El modelo
   las lee peor que un Markdown, y el panel de fuentes del frontend las
   descarta enteras (react-markdown sin rehype-raw ignora los nodos HTML),
   así que el usuario no podía comprobar con sus ojos la cifra citada.
2. Un contrato TRANSCRIBE cláusulas y tablas de contratos de terceros (los
   "Contratos Primigenios" de otros suministradores). La cabecera del
   fragmento dice "contrato de LA ARENA con Pluz", y el modelo aplicaba esa
   atribución a TODAS las cifras del fragmento, incluida la tabla que el
   propio texto asigna a Celepsa.
"""

from __future__ import annotations

import html
import re

from .schemas import normalize_text_filter

# El cierre es opcional a propósito: el OCR corta tablas a mitad cuando el
# documento salta de página, y una tabla sin </table> se quedaba sin convertir.
_TABLE_RE = re.compile(r"<table\b[^>]*>(.*?)(?:</table>|\Z)", re.DOTALL | re.IGNORECASE)
_ROW_RE = re.compile(r"<tr\b[^>]*>(.*?)(?:</tr>|\Z)", re.DOTALL | re.IGNORECASE)
_CELL_RE = re.compile(r"<(t[hd])\b[^>]*>(.*?)(?:</\1>|\Z)", re.DOTALL | re.IGNORECASE)
_IS_HEADER_CELL = re.compile(r"<th\b", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")


def _cell_text(raw: str) -> str:
    """Texto plano de una celda: sin etiquetas, sin entidades, sin romper la tabla."""
    text = html.unescape(_TAG_RE.sub(" ", raw))
    # El pipe dentro de una celda partiría la fila en Markdown.
    return " ".join(text.split()).replace("|", "\\|")


def html_tables_to_markdown(text: str) -> str:
    """Convierte cada <table> del texto en una tabla Markdown GFM.

    Idempotente sobre texto sin tablas HTML: devuelve la entrada tal cual.
    """
    if "<table" not in text.lower():
        return text

    def _convert(m: re.Match[str]) -> str:
        body = m.group(1)
        rows: list[list[str]] = []
        header_seen = False
        for row_m in _ROW_RE.finditer(body):
            raw_row = row_m.group(1)
            cells = [_cell_text(c.group(2)) for c in _CELL_RE.finditer(raw_row)]
            if not cells:
                continue
            if not rows and _IS_HEADER_CELL.search(raw_row):
                header_seen = True
            rows.append(cells)
        if not rows:
            return ""
        width = max(len(r) for r in rows)
        rows = [r + [""] * (width - len(r)) for r in rows]
        if not header_seen:
            # Sin <th> no hay cabecera real, pero GFM exige una fila de
            # separación: se emite vacía para no inventar nombres de columna.
            rows.insert(0, [""] * width)
        out = ["| " + " | ".join(rows[0]) + " |", "| " + " | ".join(["---"] * width) + " |"]
        out += ["| " + " | ".join(r) + " |" for r in rows[1:]]
        return "\n" + "\n".join(out) + "\n"

    return _TABLE_RE.sub(_convert, text)


# Razones sociales peruanas: siempre terminan en S.A., S.A.C. o S.A.A.
_COMPANY_RE = re.compile(
    r"\b((?:[A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÑáéíóúñ]*|de|del|la|el|y)"
    r"(?:\s+(?:[A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÑáéíóúñ]*|de|del|la|el|y)){0,5}"
    r"\s+S\.\s?A\.(?:\s?[AC]\.)?)"
)

MAX_THIRD_PARTIES = 4


def find_third_parties(text: str, own_names: list[str | None]) -> list[str]:
    """Empresas nombradas en el texto que NO son parte del propio documento.

    `own_names` son el suministrador y el usuario libre del fragmento. La
    comparación es por contención sobre el nombre normalizado: el frontmatter
    trae "Pluz Energía" y el cuerpo "PLUZ ENERGÍA PERÚ S.A.A.".
    """
    own = [normalize_text_filter(n) for n in own_names if n]
    found: dict[str, str] = {}
    for m in _COMPANY_RE.finditer(text):
        name = " ".join(m.group(1).split())
        norm = normalize_text_filter(name)
        if any(o in norm or norm in o for o in own):
            continue
        # Clave normalizada: "Orygen Perú S.A.A." y "ORYGEN PERU S.A.A." son una.
        found.setdefault(norm, name)
        if len(found) >= MAX_THIRD_PARTIES:
            break
    return list(found.values())
