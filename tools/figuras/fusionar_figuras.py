"""Paso 3: incorporar al vault lo que el modelo de visión leyó en las figuras.

Cuatro reglas gobiernan este archivo:

1. Solo entra lo que aporta información nueva. Un logotipo, un sello o una firma
   manuscrita no añaden nada: el OCR ya transcribió el nombre y el cargo que hay
   impresos al lado.
2. El bloque insertado dice de dónde viene. Un dato leído de un gráfico no es
   texto del contrato, y ni el generador ni el verificador deben poder
   confundirlos — es la misma disciplina que impuso el caso Celepsa→Pluz.
3. Se descarta lo que solo repite el texto que ya está en la página. Si el
   modelo transcribió el cuerpo en vez de la figura, no se inserta.
4. Los marcadores `![image](https://i.imgur.com/…)` se borran. Esa URL es una
   alucinación del OCR: no apunta a nada y ensucia el índice y la interfaz.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import tarfile
import unicodedata
from datetime import datetime
from pathlib import Path

VAULT = Path("/Users/sebastianlopez/Library/Application Support/osinergmin/vault")
PAGE_RE = re.compile(r"^#{1,6}\s*P[áa]gina\s+(\d+)\s*$", re.IGNORECASE)
IMG_RE = re.compile(r"!\[([^\]]*)\]")
# Un bloque ya insertado. La fusión se corre por oleadas conforme la
# extracción avanza, y sin esto una página rescatada —que se salta la prueba
# de novedad por venir vacía— se insertaría de nuevo en cada pasada.
YA_INSERTADO_RE = re.compile(
    r"^\*\*(?:Figura de la página \d+ leída|Página \d+ recuperada) de la imagen",
    re.MULTILINE,
)

# Tipos que por definición no pueden aportar nada: un logotipo, una página sin
# gráfico y una figura ilegible no tienen contenido que recuperar. Todo lo demás
# —incluidas las firmas— se decide por el juicio del modelo (`aporta_informacion`)
# y por la prueba de novedad. El modelo distingue bien: marca `firmas` con
# aporta=false cuando solo hay rúbricas, y aporta=true cuando el recuadro trae
# una fecha manuscrita que no está en el texto corrido.
TIPOS_EXCLUIDOS = {"sello_logo", "sin_grafico", "ilegible"}
FIN_BLOQUE = "*(fin de la lectura automática de la imagen)*"
# Títulos que no titulan: el modelo a veces devuelve el pie de página o
# repite la consigna. En la cabecera del bloque solo estorban.
TITULO_VACIO_RE = re.compile(
    r"^(?:p[áa]gina\s+\d+(?:\s+(?:de|del)\s+.*)?|\d+\s*/\s*\d+|sin\s+t[íi]tulo)$",
    re.IGNORECASE,
)
MAX_CONTENIDO = 6000
UMBRAL_DUPLICADO = 0.85
MIN_TOKENS_NUEVOS = 4


def normalizar(texto: str) -> set[str]:
    """Tokens informativos: palabras de 4+ letras y cualquier cosa con un dígito.

    La cifra importa aunque sea corta: en estos contratos "600" o "10" son el
    dato, no ruido. Se quitan las tildes para que "energía" y "energia" cuenten
    como el mismo token.
    """
    plano = unicodedata.normalize("NFKD", texto.lower())
    plano = "".join(c for c in plano if not unicodedata.combining(c))
    crudo = re.findall(r"[a-z0-9]+(?:[.,/-][a-z0-9]+)*", plano)
    return {t for t in crudo if len(t) >= 4 or any(c.isdigit() for c in t)}


SELLO_RE = re.compile(r"^figuras_leidas:.*$\n?", re.MULTILINE)


def sellar_frontmatter(texto: str, n: int) -> str:
    """Deja constancia en el propio documento de que se enriqueció.

    Así el vault se explica solo: no hace falta el informe externo para saber
    qué documentos llevan lectura de figuras ni con qué modelo.
    """
    if not texto.startswith("---\n"):
        return texto
    fin = texto.find("\n---\n", 4)
    if fin == -1:
        return texto
    cabecera = SELLO_RE.sub("", texto[4:fin])
    cabecera = re.sub(r"^figuras_modelo:.*$\n?", "", cabecera, flags=re.MULTILINE)
    sello = f'figuras_leidas: {n}\nfiguras_modelo: "Qwen3.8-27B"'
    return f"---\n{cabecera.rstrip()}\n{sello}\n{texto[fin + 1:]}"


def bloque(res: dict) -> str:
    contenido = (res.get("contenido_markdown") or "").strip()[:MAX_CONTENIDO]
    # Red de seguridad: si el modelo escapó los saltos de línea dos veces, la
    # tabla llegaría como una sola línea con "\\n" literales y no se
    # renderizaría nunca.
    if "\\n" in contenido and "\n" not in contenido:
        contenido = contenido.replace("\\n", "\n")
    titulo = (res.get("titulo") or "").strip()
    if TITULO_VACIO_RE.match(titulo):
        titulo = ""
    coletilla = f" — {titulo}" if titulo else ""
    if res.get("rescate"):
        # Página que el OCR no pudo transcribir: aquí la lectura de la imagen no
        # duplica nada, es lo único que hay.
        encabezado = (
            f"**Página {res['pagina']} recuperada de la imagen{coletilla}.** "
            f"El OCR no pudo transcribirla; esta lectura la hizo un modelo de visión "
            f"({res.get('modelo_corto', 'Qwen3.8-27B')})."
        )
    else:
        encabezado = (
            f"**Figura de la página {res['pagina']} leída de la imagen{coletilla}.** "
            f"Tipo: {res['tipo']}. Lectura automática con modelo de visión "
            f"({res.get('modelo_corto', 'Qwen3.8-27B')}); no es texto transcrito del contrato."
        )
    notas = (res.get("notas") or "").strip()
    partes = [encabezado, contenido]
    if notas and res.get("confianza") != "alta":
        partes.append(f"*Nota de lectura: {notas}*")
    # Cierre explícito: el chunker lo usa para saber dónde acaba el bloque y
    # ponerle la cabecera a cada fragmento que lo continúe. Sin él, un bloque
    # más largo que un chunk deja texto de máquina sin identificar.
    partes.append(FIN_BLOQUE)
    return "\n\n".join(p for p in partes if p)


def util(res: dict, texto_pagina: str) -> tuple[bool, str]:
    """¿Se inserta? Devuelve (sí/no, motivo del descarte)."""
    if res.get("error"):
        return False, "error"
    if not res.get("aporta_informacion"):
        return False, "sin_informacion"
    if res.get("tipo") in TIPOS_EXCLUIDOS:
        return False, f"tipo_{res.get('tipo')}"
    contenido = (res.get("contenido_markdown") or "").strip()
    if len(contenido) < 40:
        return False, "contenido_vacio"
    if res.get("rescate"):
        # La página venía casi vacía: no hay nada con lo que pueda duplicarse.
        # El riesgo aquí no es la repetición sino la invención, y eso no lo
        # resuelve un contador de tokens — lo resuelve el prompt y la revisión.
        return True, ""
    nuevos = normalizar(contenido) - normalizar(texto_pagina)
    total = normalizar(contenido)
    if not total:
        return False, "sin_tokens"
    if len(nuevos) < MIN_TOKENS_NUEVOS:
        return False, "pocos_tokens_nuevos"
    if 1 - len(nuevos) / len(total) >= UMBRAL_DUPLICADO:
        return False, "duplica_el_texto_de_la_pagina"
    return True, ""


def paginas_del_documento(lineas: list[str]) -> list[int | None]:
    """Página a la que pertenece cada línea."""
    pagina: int | None = None
    fuera = []
    for linea in lineas:
        m = PAGE_RE.match(linea)
        if m:
            pagina = int(m.group(1))
        fuera.append(pagina)
    return fuera


def procesar_documento(md: Path, resultados: list[dict], aplicar: bool) -> dict:
    texto = md.read_text(encoding="utf-8")
    lineas = texto.splitlines()
    de_pagina = paginas_del_documento(lineas)

    texto_por_pagina: dict[int, list[str]] = {}
    tiene_marcador: dict[int, bool] = {}
    for linea, pg in zip(lineas, de_pagina, strict=True):
        if pg is None:
            continue
        texto_por_pagina.setdefault(pg, []).append(linea)
        if IMG_RE.search(linea):
            tiene_marcador[pg] = True

    por_pagina = {r["pagina"]: r for r in resultados}
    decision: dict[int, tuple[bool, str]] = {}
    for pg, res in por_pagina.items():
        texto_pg = "\n".join(texto_por_pagina.get(pg, []))
        if YA_INSERTADO_RE.search(texto_pg):
            decision[pg] = (False, "ya_insertado")
            continue
        decision[pg] = util(res, texto_pg)

    salida: list[str] = []
    insertadas = 0
    marcadores_borrados = 0
    ya_insertado: set[int] = set()
    pendiente: int | None = None  # página sin marcador cuyo bloque va al cerrarla

    def volcar_pendiente() -> None:
        """Una página sin marcador recibe su bloque al final de su sección."""
        nonlocal pendiente, insertadas
        if pendiente is None:
            return
        if salida and salida[-1].strip():
            salida.append("")
        salida.append(bloque(por_pagina[pendiente]))
        ya_insertado.add(pendiente)
        insertadas += 1
        pendiente = None

    anterior: int | None = None
    for linea, pg in zip(lineas, de_pagina, strict=True):
        if pg != anterior:
            volcar_pendiente()
            anterior = pg
            entra, _ = decision.get(pg, (False, "sin_resultado")) if pg is not None else (False, "")
            # Solo se agenda si la página no trae ningún marcador que reemplazar.
            if entra and pg not in ya_insertado and not tiene_marcador.get(pg):
                pendiente = pg
        if not IMG_RE.search(linea) or pg is None:
            salida.append(linea)
            continue
        marcadores_borrados += 1
        entra, _motivo = decision.get(pg, (False, "sin_resultado"))
        if entra and pg not in ya_insertado:
            salida.append(bloque(por_pagina[pg]))
            ya_insertado.add(pg)
            insertadas += 1
        # los demás marcadores de la página desaparecen: son la misma
        # alucinación repetida y no apuntan a nada
    volcar_pendiente()

    nuevo = "\n".join(salida) + ("\n" if texto.endswith("\n") else "")
    # Nunca dejar dos líneas en blanco de más donde había un marcador.
    nuevo = re.sub(r"\n{3,}", "\n\n", nuevo)
    if insertadas:
        nuevo = sellar_frontmatter(nuevo, insertadas)

    cambio = nuevo != texto
    if aplicar and cambio:
        tmp = md.with_suffix(".md.tmp")
        tmp.write_text(nuevo, encoding="utf-8")
        tmp.replace(md)

    return {
        "doc": md.stem,
        "insertadas": insertadas,
        "marcadores_borrados": marcadores_borrados,
        "cambio": cambio,
        "descartes": [
            {"pagina": pg, "motivo": m} for pg, (ok, m) in decision.items() if not ok
        ],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--figuras", type=Path, required=True)
    ap.add_argument("--vault", type=Path, default=VAULT)
    ap.add_argument("--aplicar", action="store_true", help="sin esto es un simulacro")
    ap.add_argument("--salida", type=Path, default=Path("/Volumes/Datos/osinergmin_data/charts"))
    args = ap.parse_args()

    resultados: dict[str, list[dict]] = {}
    tipos: dict[str, int] = {}
    for linea in args.figuras.read_text(encoding="utf-8").splitlines():
        if not linea.strip():
            continue
        r = json.loads(linea)
        resultados.setdefault(r["doc"], []).append(r)
        tipos[r.get("tipo") or f"error:{str(r.get('error'))[:20]}"] = (
            tipos.get(r.get("tipo") or f"error:{str(r.get('error'))[:20]}", 0) + 1
        )

    if args.aplicar:
        args.salida.mkdir(parents=True, exist_ok=True)
        # Un solo respaldo, el del vault virgen. La fusión es determinista a
        # partir de figuras.jsonl, así que desde ese estado se reconstruye
        # cualquier oleada; hacer un tar de 7.767 ficheros en cada pasada solo
        # cuesta minutos y disco.
        previos = sorted(args.salida.glob("vault_backup_*.tar.gz"))
        if previos:
            print(f"respaldo previo del vault virgen: {previos[0].name}", flush=True)
        else:
            marca = datetime.now().strftime("%Y%m%d_%H%M%S")
            copia = args.salida / f"vault_backup_{marca}.tar.gz"
            print(f"respaldando el vault virgen en {copia} …", flush=True)
            with tarfile.open(copia, "w:gz") as tar:
                for md in sorted(args.vault.glob("*.md")):
                    tar.add(md, arcname=md.name)
            print(f"respaldo listo ({copia.stat().st_size / 1e6:.0f} MB)", flush=True)

    informes = []
    for doc, res in sorted(resultados.items()):
        md = args.vault / f"{doc}.md"
        if not md.exists():
            informes.append({"doc": doc, "error": "no_esta_en_el_vault"})
            continue
        informes.append(procesar_documento(md, res, args.aplicar))

    con_cambio = [i for i in informes if i.get("cambio")]
    insertadas = sum(i.get("insertadas", 0) for i in informes)
    borrados = sum(i.get("marcadores_borrados", 0) for i in informes)
    motivos: dict[str, int] = {}
    for i in informes:
        for d in i.get("descartes", []):
            motivos[d["motivo"]] = motivos.get(d["motivo"], 0) + 1

    print(f"\n{'APLICADO' if args.aplicar else 'SIMULACRO'}")
    print(f"documentos con resultado : {len(resultados)}")
    print(f"documentos modificados   : {len(con_cambio)}")
    print(f"figuras insertadas       : {insertadas}")
    print(f"marcadores falsos borrados: {borrados}")
    print("\ntipos detectados por el modelo:")
    for k, v in sorted(tipos.items(), key=lambda x: -x[1]):
        print(f"  {v:6d}  {k}")
    print("\nmotivos de descarte:")
    for k, v in sorted(motivos.items(), key=lambda x: -x[1]):
        print(f"  {v:6d}  {k}")

    args.salida.mkdir(parents=True, exist_ok=True)
    (args.salida / "informe_fusion.json").write_text(
        json.dumps(informes, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (args.salida / "docs_a_reindexar.txt").write_text(
        "".join(f"{i['doc']}\n" for i in con_cambio), encoding="utf-8"
    )
    # Dos prioridades. Reindexar un documento cuesta ~8 s de embeddings en el
    # Mac, y la mayoría cambian solo porque se les quitó un marcador con URL
    # falsa — mejora real, pero sin información nueva. Los que ganaron una
    # figura entran primero.
    con_figura = [i for i in informes if i.get("insertadas")]
    (args.salida / "docs_con_figura.txt").write_text(
        "".join(f"{i['doc']}\n" for i in con_figura), encoding="utf-8"
    )
    print(f"\ninforme          : {args.salida / 'informe_fusion.json'}")
    print(f"reindex prioritario: {args.salida / 'docs_con_figura.txt'} "
          f"({len(con_figura)} docs con figura nueva)")
    print(f"reindex completo   : {args.salida / 'docs_a_reindexar.txt'} "
          f"({len(con_cambio)} docs, incluye los que solo pierden marcadores)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
