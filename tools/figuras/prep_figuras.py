"""Paso 1: inventario de figuras + render de las páginas que las contienen.

El OCR (GLM-OCR 0.9B) escribe un placeholder `![...](...)` cuando encuentra una
figura que no sabe leer — la URL que pone es alucinada y el contenido visual se
pierde. Ese marcador es la señal de "aquí había información que no se recuperó".

Salida:
  worklist.jsonl  un registro por (documento, página) a procesar
  imgs/<clave>.jpg  la página renderizada a 200 DPI
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

BASE = Path(os.environ.get("FIG_BASE", Path.home() / "osinergmin" / "figuras"))
VAULT = Path(os.environ.get("FIG_VAULT", Path.home() / "osinergmin" / "vault"))
PDFS = Path(os.environ.get("FIG_PDFS", Path.home() / "osinergmin" / "data" / "pdfs"))
IMGS = BASE / "imgs"
DPI = int(os.environ.get("FIG_DPI", "200"))

PAGE_RE = re.compile(r"^#{1,6}\s*P[áa]gina\s+(\d+)\s*$", re.IGNORECASE)
# Marcador de figura. La URL es opcional a propósito: el OCR a veces corta el
# paréntesis en el salto de línea, y esa página tiene figura igual.
IMG_RE = re.compile(r"!\[([^\]]*)\]")
SRC_RE = re.compile(r'^source_file:\s*"?([^"\n]+)"?\s*$')


def inventario() -> list[dict]:
    filas: list[dict] = []
    for md in sorted(VAULT.glob("*.md")):
        texto = md.read_text(encoding="utf-8", errors="replace")
        src = None
        pagina = None
        porpag: dict[int, list[str]] = {}
        for linea in texto.splitlines():
            if src is None:
                m = SRC_RE.match(linea)
                if m:
                    src = m.group(1).strip()
                    continue
            m = PAGE_RE.match(linea)
            if m:
                pagina = int(m.group(1))
                continue
            for alt in IMG_RE.findall(linea):
                if pagina is None:
                    continue
                porpag.setdefault(pagina, []).append(alt.strip().lower())
        if not porpag or not src:
            continue
        for pg, alts in sorted(porpag.items()):
            filas.append(
                {
                    "clave": f"{md.stem}__p{pg}",
                    "doc": md.stem,
                    "pdf": src,
                    "pagina": pg,
                    "marcadores": len(alts),
                    "alts": sorted(set(alts)),
                }
            )
    return filas


def render(fila: dict) -> tuple[str, str]:
    pdf = PDFS / fila["pdf"]
    if not pdf.exists():
        return fila["clave"], "sin_pdf"
    destino = IMGS / fila["clave"]
    if destino.with_suffix(".jpg").exists():
        return fila["clave"], "ya"
    cmd = [
        "pdftoppm", "-f", str(fila["pagina"]), "-l", str(fila["pagina"]),
        "-r", str(DPI), "-jpeg", "-jpegopt", "quality=88", "-singlefile",
        str(pdf), str(destino),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=180)
    except Exception as exc:  # noqa: BLE001
        return fila["clave"], f"error:{type(exc).__name__}"
    return fila["clave"], "ok" if destino.with_suffix(".jpg").exists() else "vacio"


def main() -> int:
    IMGS.mkdir(parents=True, exist_ok=True)
    filas = inventario()
    (BASE / "worklist.jsonl").write_text(
        "".join(json.dumps(f, ensure_ascii=False) + "\n" for f in filas), encoding="utf-8"
    )
    print(f"páginas a procesar : {len(filas)}", flush=True)
    print(f"documentos         : {len({f['doc'] for f in filas})}", flush=True)

    estados: dict[str, int] = {}
    with ProcessPoolExecutor(max_workers=int(os.environ.get("FIG_WORKERS", "12"))) as ex:
        for i, (_clave, estado) in enumerate(ex.map(render, filas, chunksize=8), 1):
            estados[estado.split(":")[0]] = estados.get(estado.split(":")[0], 0) + 1
            if i % 250 == 0:
                print(f"  {i}/{len(filas)} {estados}", flush=True)
    print("render:", estados, flush=True)
    return 0 if estados.get("ok", 0) + estados.get("ya", 0) > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
