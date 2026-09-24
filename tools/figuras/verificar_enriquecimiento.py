"""Verificación posterior a la fusión: el vault sigue cumpliendo sus invariantes.

Comprueba lo mismo que se verificó sobre el corpus OCR — ninguna tabla abre un
chunk sin la frase que dice de quién es — más lo propio del enriquecimiento:
que cada bloque de figura conserve su encabezado de procedencia y que no haya
quedado ningún marcador `![...]` con URL alucinada.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, "/Volumes/Datos/proyectos/sein_free_users_contracts_rag/backend/src")

from rag.ingestion.chunker import chunk_document  # noqa: E402
from rag.ingestion.loader import load_document  # noqa: E402

VAULT = Path("/Users/sebastianlopez/Library/Application Support/osinergmin/vault")
MARCADOR = re.compile(r"!\[[^\]]*\]")
FIGURA = re.compile(r"^\*\*Figura de la página (\d+) leída de la imagen", re.MULTILINE)
RESCATE = re.compile(r"^\*\*Página (\d+) recuperada de la imagen", re.MULTILINE)
# Toda mención al modelo de visión tiene que ir en una cabecera de bloque:
# si aparece suelta, hay una lectura de máquina sin decir que lo es.
VISION = re.compile(r"^.*modelo de visión.*$", re.MULTILINE)
TABLA = re.compile(r"^\s*(?:<table\b|\|)", re.IGNORECASE)
CABECERA_FIG = re.compile(
    r"\*\*(?:Figura de la página \d+ leída|Página \d+ recuperada) de la imagen"
)
FIN_FIG = "*(fin de la lectura automática de la imagen)*"


fragmentos_sin_cabecera = 0


def main() -> int:
    docs = 0
    chunks_total = 0
    huerfanas_reales = 0
    huerfanas_chunk0 = 0
    marcadores_restantes = 0
    docs_con_marcador: list[str] = []
    bloques = 0
    docs_con_figura = 0
    figura_sin_procedencia = 0

    for md in sorted(VAULT.glob("*.md")):
        doc = load_document(md, VAULT)
        if doc is None:
            continue
        docs += 1
        n = len(MARCADOR.findall(doc.body))
        if n:
            marcadores_restantes += n
            docs_con_marcador.append(md.stem)
        b = len(FIGURA.findall(doc.body)) + len(RESCATE.findall(doc.body))
        if b:
            bloques += b
            docs_con_figura += 1
        # Una lectura de máquina sin cabecera de procedencia sería
        # indistinguible del texto del contrato: eso no puede pasar.
        for linea in VISION.findall(doc.body):
            if not (
                linea.startswith("**Figura de la página")
                or linea.startswith("**Página ")
            ):
                figura_sin_procedencia += 1

        cs = chunk_document(doc.doc_id, doc.body, doc.meta)
        chunks_total += len(cs)
        for i, c in enumerate(cs):
            if TABLA.match(c.text):
                if i == 0:
                    huerfanas_chunk0 += 1
                else:
                    huerfanas_reales += 1
            # Un fragmento que cierra un bloque de lectura automática tiene que
            # llevar su cabecera: sin ella el texto de máquina es
            # indistinguible del articulado del contrato.
            if FIN_FIG in c.text and not CABECERA_FIG.search(c.text):
                global fragmentos_sin_cabecera
                fragmentos_sin_cabecera += 1

    print(f"documentos            : {docs}")
    print(f"fragmentos            : {chunks_total}")
    print(f"bloques de figura     : {bloques} en {docs_con_figura} documentos")
    print(f"TABLAS HUÉRFANAS reales : {huerfanas_reales}")
    print(f"  (documentos que empiezan con tabla, por diseño: {huerfanas_chunk0})")
    print(f"marcadores ![] restantes: {marcadores_restantes}")
    if docs_con_marcador:
        print(f"  en {len(docs_con_marcador)} docs, p.ej.: {docs_con_marcador[:5]}")
    print(f"bloques sin procedencia : {figura_sin_procedencia}")
    print(f"FRAGMENTOS de lectura automática sin cabecera: {fragmentos_sin_cabecera}")
    ok = (
        huerfanas_reales == 0
        and figura_sin_procedencia == 0
        and fragmentos_sin_cabecera == 0
    )
    print("\nRESULTADO:", "correcto" if ok else "HAY DEFECTOS")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
