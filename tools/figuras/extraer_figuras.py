"""Paso 2: lectura de las figuras con un modelo de visión servido por vLLM.

Regla que gobierna todo el archivo: el modelo describe lo que ve y nada más.
Un contrato mal leído es peor que un contrato no leído, así que cualquier valor
que no se distinga se marca `[ilegible]` en vez de completarse por contexto.

Reanudable: cada línea de salida lleva su clave; una segunda corrida salta las
claves ya escritas. Con 8 h de wall time en la QoS eso no es un lujo.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import os
import sys
import time
from pathlib import Path

import httpx
from PIL import Image

BASE = Path(os.environ.get("FIG_BASE", Path.home() / "osinergmin" / "figuras"))
IMGS = BASE / "imgs"
SALIDA = BASE / "out" / "figuras.jsonl"  # se puede sobrescribir con --salida

SISTEMA = """Eres un extractor de información visual de documentos contractuales del \
sector eléctrico peruano (contratos de suministro entre generadoras/distribuidoras y \
usuarios libres, en español).

Recibes UNA página escaneada. El texto corrido de esa página YA fue transcrito por otro \
sistema. Tu único trabajo son los ELEMENTOS GRÁFICOS: diagramas, flujogramas, gráficos \
estadísticos, esquemas unifilares, mapas, planos, tablas que están dibujadas como imagen, \
fotografías, sellos y firmas.

REGLAS INVIOLABLES
1. Describe SOLO lo que se ve. No infieras, no completes, no deduzcas por contexto.
2. Un número que no se lee con certeza se escribe `[ilegible]`. Nunca lo aproximes.
3. No transcribas el cuerpo de texto de la página: ya está capturado. Limítate al gráfico.
4. Si en la página no hay ningún elemento gráfico con información — solo logotipos, \
membretes, sellos decorativos o firmas manuscritas — responde aporta_informacion=false.
5. Escribe en español.

QUÉ DEVOLVER SEGÚN EL TIPO
- grafico (barras, líneas, torta, dispersión): reconstruye la tabla de datos subyacente en \
Markdown, con los rótulos de ejes y las unidades tal como aparecen. Si las series no están \
etiquetadas con valores, di explícitamente que los valores son lecturas aproximadas del eje.
- diagrama / flujograma / esquema unifilar: enumera los nodos con su texto literal y las \
conexiones entre ellos; respeta los carriles (swimlanes) y las condiciones de las decisiones.
- tabla_imagen: reprodúcela como tabla Markdown, celda por celda.
- mapa / plano: describe qué representa y los rótulos legibles.
- foto: describe el contenido de forma factual y breve.
- firmas / sello_logo: aporta_informacion=false, salvo que el sello contenga datos que no \
sean el nombre de la empresa (por ejemplo una fecha o un número de registro).

El campo `notas` es SOLO para advertir de lo que no se pudo leer: máximo 25 palabras. \
No expliques tu razonamiento, no describas lo que descartaste, no justifiques la \
clasificación. Si no hay nada que advertir, déjalo vacío.

Devuelve exclusivamente un objeto JSON con este esquema."""

SISTEMA_RESCATE = """Eres un transcriptor de documentos contractuales del sector \
eléctrico peruano (contratos de suministro entre generadoras/distribuidoras y usuarios \
libres, en español).

Recibes UNA página escaneada que otro sistema NO logró transcribir: sacó menos de 250 \
caracteres, o sea casi nada. Puede ser una página que es casi toda imagen (un plano, un \
anexo dibujado, un diagrama a página completa), un escaneo de mala calidad, o una página \
que de verdad está casi en blanco.

Tu trabajo es recuperar TODO lo que se pueda leer en ella.

REGLAS INVIOLABLES
1. Transcribe SOLO lo que se ve. No infieras, no completes, no deduzcas por contexto.
2. Un número o una palabra que no se lee con certeza se escribe `[ilegible]`. Nunca la \
aproximes ni la adivines.
3. Las tablas van en Markdown, celda por celda.
4. Los elementos gráficos (diagramas, esquemas, planos) se describen: nodos, conexiones, \
rótulos legibles.
5. Si la página está genuinamente en blanco o solo tiene un número de página, responde \
aporta_informacion=false y tipo="sin_grafico".
6. Escribe en español.

El campo `notas` es SOLO para advertir de lo que no se pudo leer: máximo 25 palabras. \
No expliques tu razonamiento, no describas lo que descartaste, no justifiques la \
clasificación. Si no hay nada que advertir, déjalo vacío.

Devuelve exclusivamente un objeto JSON con este esquema."""

ESQUEMA = {
    "type": "object",
    "properties": {
        "tipo": {
            "type": "string",
            "enum": [
                "grafico", "diagrama", "flujograma", "esquema_unifilar", "tabla_imagen",
                "mapa", "plano", "foto", "firmas", "sello_logo", "ilegible", "sin_grafico",
                "pagina_rescatada",
            ],
        },
        "aporta_informacion": {"type": "boolean"},
        "titulo": {"type": "string"},
        "contenido_markdown": {"type": "string"},
        "empresas_mencionadas": {"type": "array", "items": {"type": "string"}},
        "confianza": {"type": "string", "enum": ["alta", "media", "baja"]},
        "notas": {"type": "string"},
    },
    "required": [
        "tipo", "aporta_informacion", "titulo", "contenido_markdown",
        "empresas_mencionadas", "confianza", "notas",
    ],
    "additionalProperties": False,
}


def cargar_hechos(SALIDA: Path) -> set[str]:
    if not SALIDA.exists():
        return set()
    hechos = set()
    for linea in SALIDA.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            fila = json.loads(linea)
        except Exception:  # noqa: BLE001
            continue
        # Una página que falló NO está hecha: el siguiente job de la cadena la
        # vuelve a intentar. Darla por buena la perdería para siempre.
        if not fila.get("error"):
            hechos.add(fila["clave"])
    return hechos


# Un A4 a 200 DPI son ~3,9 Mpx (~1.250 tokens de imagen). Los planos de gran
# formato del corpus llegan a 36 Mpx: ~11.500 tokens, que con el prompt y la
# respuesta no caben en los 16.384 del contexto y el servidor devuelve 400.
# Reducirlos a 12 Mpx (~3.800 tokens) deja sitio de sobra y conserva bastante
# más detalle que bajar el DPI de todo el corpus.
MAX_PIXELES = 12_000_000


def b64(path: Path) -> str:
    with Image.open(path) as im:
        if im.width * im.height <= MAX_PIXELES:
            return base64.b64encode(path.read_bytes()).decode("ascii")
        factor = (MAX_PIXELES / (im.width * im.height)) ** 0.5
        chico = im.convert("RGB").resize(
            (max(1, int(im.width * factor)), max(1, int(im.height * factor))),
            Image.LANCZOS,
        )
        buf = io.BytesIO()
        chico.save(buf, "JPEG", quality=90, optimize=True)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def parsear(texto: str) -> dict:
    """Devuelve el objeto JSON de la respuesta, tolerando envoltorios.

    Un modelo híbrido de razonamiento puede anteponer un bloque de pensamiento,
    y una generación cortada por `max_tokens` deja el JSON a medias. Se intenta
    el parseo directo y, si falla, el fragmento entre la primera llave y la
    última — que es lo que salva los envoltorios sin inventar contenido.
    """
    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        pass
    i, j = texto.find("{"), texto.rfind("}")
    if i == -1 or j <= i:
        raise
    return json.loads(texto[i : j + 1])


async def procesar(
    cliente: httpx.AsyncClient, modelo: str, fila: dict, sem: asyncio.Semaphore
) -> dict | None:
    img = IMGS / f"{fila['clave']}.jpg"
    if not img.exists():
        return {"clave": fila["clave"], "error": "sin_imagen"}
    rescate = bool(fila.get("motivo", "").startswith("pagina_casi_vacia"))
    instruccion = (
        "El sistema de OCR no pudo transcribir esta página. Recupera todo lo legible."
        if rescate
        else "Extrae los elementos gráficos siguiendo las reglas."
    )
    # El cuerpo se construye DENTRO del semáforo: con 4.000 tareas creadas de
    # golpe, leer y codificar en base64 las 4.000 imágenes por adelantado
    # bloquea el bucle de eventos y retiene varios GB para nada.
    async with sem:
        return await _pedir(cliente, modelo, fila, img, rescate, instruccion)


async def _pedir(
    cliente: httpx.AsyncClient,
    modelo: str,
    fila: dict,
    img: Path,
    rescate: bool,
    instruccion: str,
) -> dict:
    sistema = SISTEMA_RESCATE if rescate else SISTEMA
    cuerpo = {
        "model": modelo,
        "messages": [
            {"role": "system", "content": sistema},
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
                            f"Página {fila['pagina']} del documento {fila['doc']}. "
                            + instruccion
                        ),
                    },
                ],
            },
        ],
        # Una página rescatada puede ser un unifilar denso con decenas de
        # rótulos; una con marcador casi siempre es una firma. Dar el mismo
        # presupuesto a las dos trunca las primeras o desperdicia en las
        # segundas.
        "max_tokens": 3600 if rescate else 2000,
        "temperature": 0.0,
        # La plantilla de chat de Qwen3.8 activa el razonamiento cuando nadie
        # dice lo contrario ("enable_thinking is undefined or is true"), y el
        # bloque <think> se comía casi todo el presupuesto de tokens: unas
        # respuestas se cortaban a medias y todas tardaban varias veces más.
        # Transcribir una figura no necesita cadena de pensamiento.
        "chat_template_kwargs": {"enable_thinking": False},
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "figura", "schema": ESQUEMA, "strict": True},
        },
    }
    for intento in range(3):
        try:
            r = await cliente.post("/v1/chat/completions", json=cuerpo, timeout=420)
            r.raise_for_status()
            eleccion = r.json()["choices"][0]
            txt = eleccion["message"]["content"] or ""
            try:
                datos = parsear(txt)
            except json.JSONDecodeError as exc:
                if intento == 2:
                    return {
                        "clave": fila["clave"],
                        "error": f"JSONDecodeError: {exc}"[:200],
                        "fin": eleccion.get("finish_reason"),
                        "crudo": txt[-400:],
                    }
                raise
            datos.update(
                {
                    "clave": fila["clave"],
                    "doc": fila["doc"],
                    "pagina": fila["pagina"],
                    "alts": fila["alts"],
                    "rescate": rescate,
                    "modelo": modelo,
                }
            )
            return datos
        except Exception as exc:  # noqa: BLE001
            if intento == 2:
                return {"clave": fila["clave"], "error": f"{type(exc).__name__}: {exc}"[:300]}
            await asyncio.sleep(2 * (intento + 1))
    return {"clave": fila["clave"], "error": "sin_resultado"}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--modelo", required=True)
    ap.add_argument("--concurrencia", type=int, default=16)
    ap.add_argument("--limite", type=int, default=0)
    ap.add_argument("--deadline", type=int, default=0, help="segundos de trabajo antes de parar")
    ap.add_argument("--salida", default=str(SALIDA))
    ap.add_argument(
        "--claves",
        type=Path,
        help="Fichero con una clave por línea: procesa solo esas (para comparar modelos)",
    )
    args = ap.parse_args()
    salida = Path(args.salida)

    filas = [
        json.loads(l)
        for l in (BASE / "worklist.jsonl").read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    hechos = cargar_hechos(salida)
    pendientes = [f for f in filas if f["clave"] not in hechos]
    if args.claves:
        quiero = {
            l.strip() for l in args.claves.read_text(encoding="utf-8").splitlines() if l.strip()
        }
        pendientes = [f for f in pendientes if f["clave"] in quiero]
    if args.limite:
        pendientes = pendientes[: args.limite]
    print(f"total={len(filas)} hechos={len(hechos)} pendientes={len(pendientes)}", flush=True)
    if not pendientes:
        return 0

    salida.parent.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(args.concurrencia)
    t0 = time.monotonic()
    escritos = errores = 0
    with salida.open("a", encoding="utf-8") as fh:
        async with httpx.AsyncClient(base_url=args.url) as cliente:
            tareas = [
                asyncio.create_task(procesar(cliente, args.modelo, f, sem)) for f in pendientes
            ]
            for done in asyncio.as_completed(tareas):
                res = await done
                if res is None:
                    continue
                fh.write(json.dumps(res, ensure_ascii=False) + "\n")
                fh.flush()
                escritos += 1
                errores += 1 if "error" in res else 0
                if escritos % 50 == 0:
                    dt = time.monotonic() - t0
                    ritmo = escritos / dt if dt else 0
                    queda = (len(pendientes) - escritos) / ritmo if ritmo else 0
                    print(
                        f"  {escritos}/{len(pendientes)} err={errores} "
                        f"{ritmo:.2f} pág/s eta={queda / 60:.0f} min",
                        flush=True,
                    )
                if args.deadline and time.monotonic() - t0 > args.deadline:
                    print("deadline alcanzado, corto limpio", flush=True)
                    for t in tareas:
                        t.cancel()
                    break
    print(f"escritos={escritos} errores={errores}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
