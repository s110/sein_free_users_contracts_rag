"""Compara las dos lecturas de las MISMAS páginas: pesos FP8 oficiales vs AWQ INT4.

Cambiar de cuantización para ganar velocidad solo vale si la lectura no se
degrada. Esto lo mira página por página en vez de fiarse del benchmark.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def cargar(p: Path) -> dict[str, dict]:
    return {
        json.loads(l)["clave"]: json.loads(l)
        for l in p.read_text(encoding="utf-8").splitlines()
        if l.strip()
    }


def main() -> int:
    fp8 = cargar(Path(sys.argv[1]))
    awq = cargar(Path(sys.argv[2]))
    comunes = sorted(set(fp8) & set(awq))
    print(f"páginas comparables: {len(comunes)}\n")

    igual_tipo = igual_aporta = 0
    for k in comunes:
        a, b = fp8[k], awq[k]
        igual_tipo += a.get("tipo") == b.get("tipo")
        igual_aporta += a.get("aporta_informacion") == b.get("aporta_informacion")
        print("=" * 78)
        print(k)
        for etiqueta, d in (("FP8", a), ("AWQ", b)):
            c = (d.get("contenido_markdown") or "").strip()
            print(
                f"  [{etiqueta}] tipo={d.get('tipo')} aporta={d.get('aporta_informacion')} "
                f"conf={d.get('confianza')} chars={len(c)} notas={len(d.get('notas') or '')}"
            )
            for linea in c.splitlines()[:8]:
                print(f"        {linea}")
        print()

    print("=" * 78)
    print(f"mismo tipo            : {igual_tipo}/{len(comunes)}")
    print(f"misma decisión aporta : {igual_aporta}/{len(comunes)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
