"""Segunda pasada: contrasta cada lectura de dibujo contra su propia imagen.

El verificador adversario del RAG protege de que el generador atribuya mal una
cifra, pero no puede protegerse de un error cometido antes, al leer la imagen:
si la lectura dice "Max 30'" y el dibujo dice "Mapa SAR", el fragmento contiene
la invención y cualquier comprobación posterior la confirma.

Este paso cierra ese hueco donde está el riesgo — las cifras y rótulos sueltos
dentro de un dibujo — devolviendo la imagen al modelo junto con lo que escribió
y pidiéndole que señale lo que NO aparece literalmente en ella.

Salida: `verificacion.jsonl`, una línea por lectura revisada con la lista de
afirmaciones dudosas. `fusionar_figuras.py` las marca en el bloque insertado.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import sys
import time
from pathlib import Path

import httpx
from PIL import Image

BASE = Path.home() / "osinergmin" / "figuras"
IMGS = BASE / "imgs"
MAX_PIXELES = 12_000_000

# Solo los tipos donde el modelo lee rótulos pequeños dentro de un dibujo. Una
# tabla dibujada o una página transcrita se comprueban solas con la prueba de
# novedad; un flujograma no.
TIPOS_A_REVISAR = {"grafico", "diagrama", "flujograma", "esquema_unifilar", "mapa", "plano"}

SISTEMA = """Eres un revisor adversario de lecturas automáticas de imágenes.

Recibes una página escaneada y el texto que OTRO modelo dijo haber leído en ella.
Tu trabajo NO es aprobar ese texto: es encontrar lo que se inventó.

Un caso real de lo que buscas: en un flujograma que decía "Tiempo de llegada /
Mapa SAR", la lectura escribió "Tiempo de llegada: Max 30'". La estructura era
correcta y el número no existía. Ese es exactamente el error a cazar.

REGLAS
1. Recorre el texto afirmación por afirmación y compruébala contra la imagen.
2. Marca como dudosa toda cifra, unidad, fecha, porcentaje o nombre propio que
   no puedas localizar literalmente en el dibujo.
3. Marca también lo que esté claramente mal transcrito (una palabra por otra).
4. NO marques lo que sí está, aunque esté reformulado: juzgas el contenido, no
   la redacción.
5. Si no encuentras nada dudoso, devuelve la lista vacía. No inventes reparos.

Devuelve exclusivamente un objeto JSON con este esquema."""

ESQUEMA = {
    "type": "object",
    "properties": {
        "dudosas": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "fragmento": {"type": "string"},
                    "motivo": {"type": "string"},
                },
                "required": ["fragmento", "motivo"],
                "additionalProperties": False,
            },
        },
        "veredicto": {"type": "string", "enum": ["fiel", "con_reparos", "no_verificable"]},
    },
    "required": ["dudosas", "veredicto"],
    "additionalProperties": False,
}


def b64(path: Path) -> str:
    with Image.open(path) as im:
        if im.width * im.height <= MAX_PIXELES:
            return base64.b64encode(path.read_bytes()).decode("ascii")
        f = (MAX_PIXELES / (im.width * im.height)) ** 0.5
        chico = im.convert("RGB").resize(
            (max(1, int(im.width * f)), max(1, int(im.height * f))), Image.LANCZOS
        )
        buf = io.BytesIO()
        chico.save(buf, "JPEG", quality=90, optimize=True)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def hechos(salida: Path) -> set[str]:
    if not salida.exists():
        return set()
    out = set()
    for linea in salida.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            fila = json.loads(linea)
        except Exception:  # noqa: BLE001
            continue
        if not fila.get("error"):
            out.add(fila["clave"])
    return out


async def revisar(
    cliente: httpx.AsyncClient, modelo: str, fila: dict, sem: asyncio.Semaphore
) -> dict:
    img = IMGS / f"{fila['clave']}.jpg"
    if not img.exists():
        return {"clave": fila["clave"], "error": "sin_imagen"}
    async with sem:
        cuerpo = {
            "model": modelo,
            "messages": [
                {"role": "system", "content": SISTEMA},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{b64(img)}"},
                        },
                        {
                            "type": "text",
                            "text": (
                                "Texto que otro modelo dijo haber leído en esta página:\n\n"
                                f"{fila['contenido_markdown']}\n\n"
                                "Señala lo que no aparece en la imagen."
                            ),
                        },
                    ],
                },
            ],
            "max_tokens": 1600,
            "temperature": 0.0,
            "chat_template_kwargs": {"enable_thinking": False},
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "revision", "schema": ESQUEMA, "strict": True},
            },
        }
        for intento in range(3):
            try:
                r = await cliente.post("/v1/chat/completions", json=cuerpo, timeout=420)
                r.raise_for_status()
                txt = r.json()["choices"][0]["message"]["content"] or ""
                try:
                    datos = json.loads(txt)
                except json.JSONDecodeError:
                    i, j = txt.find("{"), txt.rfind("}")
                    if i == -1 or j <= i:
                        raise
                    datos = json.loads(txt[i : j + 1])
                datos.update({"clave": fila["clave"], "tipo": fila["tipo"]})
                return datos
            except Exception as exc:  # noqa: BLE001
                if intento == 2:
                    return {"clave": fila["clave"], "error": f"{type(exc).__name__}: {exc}"[:200]}
                await asyncio.sleep(2 * (intento + 1))
    return {"clave": fila["clave"], "error": "sin_resultado"}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--modelo", required=True)
    ap.add_argument("--concurrencia", type=int, default=12)
    ap.add_argument("--figuras", type=Path, default=BASE / "out" / "figuras.jsonl")
    ap.add_argument("--salida", type=Path, default=BASE / "out" / "verificacion.jsonl")
    args = ap.parse_args()

    candidatas = []
    for linea in args.figuras.read_text(encoding="utf-8").splitlines():
        if not linea.strip():
            continue
        d = json.loads(linea)
        if d.get("error") or not d.get("aporta_informacion"):
            continue
        if d.get("tipo") not in TIPOS_A_REVISAR:
            continue
        if len(d.get("contenido_markdown") or "") < 40:
            continue
        candidatas.append(d)

    ya = hechos(args.salida)
    pendientes = [c for c in candidatas if c["clave"] not in ya]
    print(f"candidatas={len(candidatas)} hechas={len(ya)} pendientes={len(pendientes)}", flush=True)
    if not pendientes:
        return 0

    args.salida.parent.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(args.concurrencia)
    t0 = time.monotonic()
    n = reparos = 0
    with args.salida.open("a", encoding="utf-8") as fh:
        async with httpx.AsyncClient(base_url=args.url) as cliente:
            tareas = [asyncio.create_task(revisar(cliente, args.modelo, c, sem)) for c in pendientes]
            for done in asyncio.as_completed(tareas):
                res = await done
                fh.write(json.dumps(res, ensure_ascii=False) + "\n")
                fh.flush()
                n += 1
                reparos += 1 if res.get("dudosas") else 0
                if n % 25 == 0:
                    dt = time.monotonic() - t0
                    print(f"  {n}/{len(pendientes)} con reparos={reparos} "
                          f"{n / dt:.2f} pág/s", flush=True)
    print(f"revisadas={n} con reparos={reparos}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
