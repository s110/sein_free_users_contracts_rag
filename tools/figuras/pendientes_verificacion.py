"""Cuántas lecturas de dibujo quedan por contrastar contra su imagen.

Lo usa `figuras_job.slurm` para decidir si el job todavía tiene trabajo: la
segunda pasada es parte del pipeline, no un extra opcional.
"""

from __future__ import annotations

import json
from pathlib import Path

BASE = Path.home() / "osinergmin" / "figuras"
TIPOS = {"grafico", "diagrama", "flujograma", "esquema_unifilar", "mapa", "plano"}


def _claves(path: Path, filtro) -> set[str]:
    if not path.exists():
        return set()
    out = set()
    for linea in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            d = json.loads(linea)
        except Exception:  # noqa: BLE001
            continue
        if filtro(d):
            out.add(d["clave"])
    return out


def main() -> None:
    candidatas = _claves(
        BASE / "out" / "figuras.jsonl",
        lambda d: (
            not d.get("error")
            and d.get("aporta_informacion")
            and d.get("tipo") in TIPOS
            and len(d.get("contenido_markdown") or "") >= 40
        ),
    )
    hechas = _claves(BASE / "out" / "verificacion.jsonl", lambda d: not d.get("error"))
    print(len(candidatas - hechas))


if __name__ == "__main__":
    main()
