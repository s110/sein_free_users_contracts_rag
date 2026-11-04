"""Renderiza las páginas del worklist que aún no tienen imagen.

A diferencia de prep_figuras.py, no reconstruye el worklist: lo lee tal cual.
"""
from __future__ import annotations

import json
import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import subprocess

BASE = Path.home() / "osinergmin" / "figuras"
IMGS = BASE / "imgs"
PDFS = Path.home() / "osinergmin" / "data" / "pdfs"
DPI = int(os.environ.get("FIG_DPI", "200"))


def render(fila: dict) -> str:
    destino = IMGS / fila["clave"]
    if destino.with_suffix(".jpg").exists():
        return "ya"
    pdf = PDFS / fila["pdf"]
    if not pdf.exists():
        return "sin_pdf"
    # Mismo renombrado final que prep_figuras.render: nunca un .jpg truncado.
    tmp = IMGS / f".{fila['clave']}.tmp"
    cmd = ["pdftoppm", "-f", str(fila["pagina"]), "-l", str(fila["pagina"]), "-r", str(DPI),
           "-jpeg", "-jpegopt", "quality=88", "-singlefile", str(pdf), str(tmp)]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=180)
    except Exception as exc:  # noqa: BLE001
        tmp.with_suffix(".tmp.jpg").unlink(missing_ok=True)
        return f"error:{type(exc).__name__}"
    if not tmp.with_suffix(".tmp.jpg").exists():
        return "vacio"
    tmp.with_suffix(".tmp.jpg").replace(destino.with_suffix(".jpg"))
    return "ok"


if __name__ == "__main__":
    filas = [json.loads(l) for l in (BASE / "worklist.jsonl").read_text().splitlines() if l.strip()]
    IMGS.mkdir(parents=True, exist_ok=True)
    estados: dict[str, int] = {}
    with ProcessPoolExecutor(max_workers=int(os.environ.get("FIG_WORKERS", "8"))) as ex:
        for e in ex.map(render, filas, chunksize=8):
            estados[e.split(":")[0]] = estados.get(e.split(":")[0], 0) + 1
    print("render:", estados)
    print("imágenes en disco:", len(list(IMGS.glob("*.jpg"))))
