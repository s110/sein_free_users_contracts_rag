"""Genera un golden set real a partir del vault OCR.

El golden.jsonl del repo nació como plantilla de 3 filas. Este script lo
reemplaza con preguntas verificables: muestrea documentos del vault (uno por
razón social, para que `expected_source_file` no sea ambiguo), ancla cada
pregunta en una cláusula que el documento de verdad contiene, e incluye la
razón social y la fecha en la pregunta para que el retrieval tenga señal.

Determinista a propósito (sin LLM): el golden debe ser estable entre corridas
para que dos modelos se comparen contra el mismo listón.

Uso:
    uv run python eval/build_golden.py --vault ~/osinergmin_vault --n 20
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)

# Anclas en orden de preferencia: (patrón en el cuerpo, plantilla de pregunta).
ANCHORS = [
    (
        re.compile(r"potencia\s+contratada", re.IGNORECASE),
        "¿Qué potencia contratada establece el {tipo} de {usuario} suscrito el {fecha}?",
    ),
    (
        re.compile(
            r"plazo\s+de\s+(vigencia|suministro)|duraci[oó]n\s+del\s+contrato", re.IGNORECASE
        ),
        "¿Cuál es el plazo de vigencia del {tipo} de {usuario} suscrito el {fecha}?",
    ),
    (
        re.compile(r"factura", re.IGNORECASE),
        "¿Qué condiciones de facturación y pago fija el {tipo} de {usuario} suscrito el {fecha}?",
    ),
    (
        re.compile(r"punto\s+de\s+(suministro|entrega)", re.IGNORECASE),
        "¿Cuál es el punto de suministro pactado en el {tipo} de {usuario} suscrito el {fecha}?",
    ),
]


def parse_frontmatter(text: str) -> dict:
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}
    meta: dict = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.startswith(" "):
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip().strip('"')
    return meta


def build(vault: Path, n: int) -> list[dict]:
    seen_users: set[str] = set()
    user_doc_count: dict[str, int] = {}
    docs = []
    for path in sorted(vault.glob("*.md")):
        text = path.read_text(errors="replace")
        meta = parse_frontmatter(text)
        user = meta.get("usuario_libre", "")
        if user:
            user_doc_count[user] = user_doc_count.get(user, 0) + 1
        docs.append((path, meta, text))

    cases = []
    for path, meta, text in docs:
        user = meta.get("usuario_libre", "")
        fecha = meta.get("fecha_suscripcion", "")
        tipo = (meta.get("tipo") or "contrato").lower()
        if not user or not fecha:
            continue
        if meta.get("ocr_status") not in ("ok", None):
            continue
        # Una sola empresa-documento: con dos documentos de la misma razón
        # social, "expected_source_file" castigaría respuestas legítimas
        # sacadas del otro documento.
        if user_doc_count.get(user, 0) != 1 or user in seen_users:
            continue
        body = text[FRONTMATTER_RE.match(text).end():] if FRONTMATTER_RE.match(text) else text
        for pattern, template in ANCHORS:
            if pattern.search(body):
                seen_users.add(user)
                cases.append(
                    {
                        "question": template.format(tipo=tipo, usuario=user, fecha=fecha),
                        "expected_source_file": meta.get("source_file", path.stem + ".pdf"),
                        "notes": f"ancla: {pattern.pattern[:40]}",
                    }
                )
                break
        if len(cases) >= n:
            break
    return cases


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault", type=Path, required=True)
    parser.add_argument("--n", type=int, default=20)
    parser.add_argument(
        "--out", type=Path, default=Path(__file__).parent / "golden.jsonl"
    )
    args = parser.parse_args()

    cases = build(args.vault.expanduser(), args.n)
    if len(cases) < max(10, args.n // 2):
        print(f"Solo {len(cases)} casos utilizables; no reemplazo el golden.", file=sys.stderr)
        return 1
    args.out.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases))
    print(f"golden.jsonl: {len(cases)} casos escritos en {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
