"""Construye el informe del enriquecimiento de figuras como página HTML.

Reúne en un solo sitio lo que hay que poder revisar: por qué se eligió el
modelo, qué se detectó en el corpus, qué se extrajo y — lo único que zanja la
calidad — la página escaneada al lado de lo que el modelo leyó en ella.
"""

from __future__ import annotations

import argparse
import base64
import html
import io
import json
import random
import subprocess
from pathlib import Path

from PIL import Image

BASE = Path("/Volumes/Datos/osinergmin_data/charts")
ANCHO = 700
CALIDAD = 70

LEADERBOARD = [
    ("Claude Mythos 5", "Anthropic", "93.5", "cerrado", False),
    ("Qwen3.8 Max", "Alibaba", "93.5", "abierto", False),
    ("Kimi K3", "Moonshot", "91.3", "cerrado", False),
    ("Claude Opus 4.7", "Anthropic", "91.0", "cerrado", False),
    ("Qwen3.8-Flash-Next", "Alibaba", "90.6", "abierto", False),
    ("Qwen3.8-27B", "Alibaba", "90.2", "Apache-2.0", True),
    ("Claude Opus 4.8", "Anthropic", "89.9", "cerrado", False),
    ("GLM-5.3-Flash", "Z.AI", "89.4", "abierto", False),
    ("Qwen3.6-27B", "Alibaba", "78.4", "Apache-2.0", False),
]


def miniatura(jpg: Path) -> str:
    im = Image.open(jpg)
    im.thumbnail((ANCHO, ANCHO * 2))
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG", quality=CALIDAD, optimize=True)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def traer_imagenes(claves: list[str], destino: Path) -> None:
    destino.mkdir(parents=True, exist_ok=True)
    faltan = [c for c in claves if not (destino / f"{c}.jpg").exists()]
    if not faltan:
        return
    lista = destino / "_traer.txt"
    lista.write_text("".join(f"{c}.jpg\n" for c in faltan), encoding="utf-8")
    subprocess.run(
        ["rsync", "-a", "--files-from", str(lista), "khipu:osinergmin/figuras/imgs/", str(destino)],
        check=False,
    )
    lista.unlink(missing_ok=True)


def tarjeta(res: dict, img_b64: str) -> str:
    tipo = html.escape(res.get("tipo") or "?")
    conf = html.escape(res.get("confianza") or "?")
    aporta = res.get("aporta_informacion")
    titulo = html.escape(res.get("titulo") or "")
    notas = html.escape(res.get("notas") or "")
    contenido = html.escape(res.get("contenido_markdown") or "")
    empresas = ", ".join(html.escape(e) for e in (res.get("empresas_mencionadas") or []))
    clase = "si" if aporta else "no"
    etiqueta = "entra al índice" if aporta else "se descarta"
    origen = "página rescatada" if res.get("rescate") else "figura marcada"
    return f"""
<article class="par">
  <h3>{html.escape(res['doc'])} · pág. {res['pagina']}</h3>
  <p class="chips">
    <span class="chip">{origen}</span>
    <span class="chip">{tipo}</span>
    <span class="chip conf-{conf}">confianza {conf}</span>
    <span class="chip {clase}">{etiqueta}</span>
  </p>
  {f'<p class="titulo">{titulo}</p>' if titulo else ''}
  {f'<p class="sec"><b>Empresas nombradas:</b> {empresas}</p>' if empresas else ''}
  {f'<p class="sec"><b>Nota de lectura:</b> {notas}</p>' if notas else ''}
  <div class="lado">
    <figure><img src="data:image/jpeg;base64,{img_b64}" alt="Página escaneada" loading="lazy"></figure>
    <pre class="salida">{contenido or '(sin contenido: el modelo no encontró información que recuperar)'}</pre>
  </div>
</article>"""


CSS = """
:root{--bg:#fbfaf8;--surface:#fff;--surface2:#f4f1ec;--border:#e3dfd8;--text:#1f1d1a;
--dim:#6b6660;--accent:#8a5a2b;--ok:#2f6f3e;--no:#8a8580;--warn:#9a6a10}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){
--bg:#14130f;--surface:#1c1a16;--surface2:#232019;--border:#322e27;--text:#ece7df;
--dim:#9a938a;--accent:#d9a066;--ok:#6cc07f;--no:#7d776f;--warn:#d6a52e}}
:root[data-theme="dark"]{--bg:#14130f;--surface:#1c1a16;--surface2:#232019;--border:#322e27;
--text:#ece7df;--dim:#9a938a;--accent:#d9a066;--ok:#6cc07f;--no:#7d776f;--warn:#d6a52e}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);
font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:1120px;margin:0 auto;padding:3rem 1.25rem 6rem}
h1{font-size:2.05rem;line-height:1.15;margin:0 0 .5rem;letter-spacing:-.025em}
p.lede{color:var(--dim);font-size:1.05rem;margin:0 0 2.5rem;max-width:64ch}
h2{font-size:1.25rem;margin:3rem 0 1rem;padding-bottom:.45rem;border-bottom:1px solid var(--border);
letter-spacing:-.01em}
h3{font-size:.9rem;margin:0 0 .5rem;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
word-break:break-all;color:var(--dim);font-weight:600}
p{margin:0 0 1rem}
.cifras{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:.8rem;margin:0 0 1rem}
.dato{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:.9rem 1rem}
.dato b{display:block;font-size:1.65rem;line-height:1.15;font-variant-numeric:tabular-nums;
letter-spacing:-.02em}
.dato span{color:var(--dim);font-size:.8rem}
.tabla-envoltorio{overflow-x:auto;margin:0 0 1rem}
table{width:100%;border-collapse:collapse;font-size:.9rem;min-width:480px}
th,td{border-bottom:1px solid var(--border);padding:.5rem .65rem;text-align:left}
th{color:var(--dim);font-weight:600;font-size:.82rem}
td.n{text-align:right;font-variant-numeric:tabular-nums}
tr.destacada td{background:var(--surface2);font-weight:600}
.par{background:var(--surface);border:1px solid var(--border);border-radius:12px;
padding:1.15rem;margin:0 0 1.5rem}
.chips{margin:0 0 .55rem;display:flex;gap:.4rem;flex-wrap:wrap}
.chip{font-size:.73rem;padding:.13rem .55rem;border-radius:999px;border:1px solid var(--border);
color:var(--dim);white-space:nowrap}
.chip.si{color:var(--ok);border-color:var(--ok)}
.chip.no{color:var(--no)}
.chip.conf-baja{color:var(--warn);border-color:var(--warn)}
.titulo{margin:.15rem 0;font-weight:600}
.sec{margin:.15rem 0;font-size:.86rem;color:var(--dim)}
.lado{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:1rem;align-items:start;
margin-top:.7rem}
@media(max-width:820px){.lado{grid-template-columns:1fr}}
figure{margin:0;border:1px solid var(--border);border-radius:8px;overflow:hidden;background:#fff}
figure img{display:block;width:100%;height:auto}
pre.salida{margin:0;background:var(--bg);border:1px solid var(--border);border-radius:8px;
padding:.85rem;font:12.5px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;
white-space:pre-wrap;word-break:break-word;max-height:620px;overflow:auto}
pre.bloque{background:var(--surface2);border:1px solid var(--border);border-radius:8px;
padding:.85rem;overflow-x:auto;font:12.5px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace}
blockquote{margin:0 0 1rem;padding:.7rem 1rem;border-left:3px solid var(--accent);
background:var(--surface2);border-radius:0 8px 8px 0;color:var(--dim)}
ul{margin:0 0 1rem;padding-left:1.2rem}li{margin:.25rem 0}
footer{margin-top:3.5rem;padding-top:1rem;border-top:1px solid var(--border);
color:var(--dim);font-size:.85rem}
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--figuras", type=Path, default=BASE / "out" / "figuras.jsonl")
    ap.add_argument("--fusion", type=Path, default=BASE / "informe_fusion.json")
    ap.add_argument("--muestras", type=int, default=18)
    ap.add_argument("--salida", type=Path, default=BASE / "informe_figuras.html")
    args = ap.parse_args()

    filas = [json.loads(l) for l in args.figuras.read_text().splitlines() if l.strip()]
    buenos = [f for f in filas if not f.get("error")]
    errores = len(filas) - len(buenos)
    aportan = [f for f in buenos if f.get("aporta_informacion")]
    rescates = [f for f in buenos if f.get("rescate")]
    tipos: dict[str, int] = {}
    for f in buenos:
        tipos[f.get("tipo", "?")] = tipos.get(f.get("tipo", "?"), 0) + 1

    fusion = {}
    if args.fusion.exists():
        inf = json.loads(args.fusion.read_text())
        fusion = {
            "docs": sum(1 for i in inf if i.get("cambio")),
            "insertadas": sum(i.get("insertadas", 0) for i in inf),
            "marcadores": sum(i.get("marcadores_borrados", 0) for i in inf),
        }

    random.seed(11)
    por_tipo: dict[str, list[dict]] = {}
    for f in aportan:
        por_tipo.setdefault(f["tipo"], []).append(f)
    muestra: list[dict] = []
    while len(muestra) < args.muestras and any(por_tipo.values()):
        for t in sorted(por_tipo, key=lambda t: -len(por_tipo[t])):
            if por_tipo[t] and len(muestra) < args.muestras:
                muestra.append(por_tipo[t].pop(random.randrange(len(por_tipo[t]))))
    descartados = [f for f in buenos if not f.get("aporta_informacion")]
    muestra += random.sample(descartados, min(2, len(descartados)))

    destino = BASE / "revision_imgs"
    traer_imagenes([m["clave"] for m in muestra], destino)
    tarjetas = [
        tarjeta(m, miniatura(destino / f"{m['clave']}.jpg"))
        for m in muestra
        if (destino / f"{m['clave']}.jpg").exists()
    ]

    fila_lb = "".join(
        f"<tr class='{'destacada' if d else ''}'><td>{html.escape(n)}</td>"
        f"<td>{html.escape(o)}</td><td class='n'>{s}</td><td>{html.escape(lic)}</td></tr>"
        for n, o, s, lic, d in LEADERBOARD
    )
    fila_tipos = "".join(
        f"<tr><td>{html.escape(k)}</td><td class='n'>{v}</td></tr>"
        for k, v in sorted(tipos.items(), key=lambda x: -x[1])
    )

    doc = f"""<title>Figuras del corpus SEIN</title>
<style>{CSS}</style>
<div class="wrap">

<h1>Leer lo que el OCR no supo leer</h1>
<p class="lede">El pipeline OCR transcribe texto y tablas muy bien, pero cuando encuentra un
diagrama escribe un placeholder con una URL inventada y sigue. Detrás de esos marcadores se
quedaba, sin llegar al índice, contenido real. Esto lo recupera.</p>

<div class="cifras">
<div class="dato"><b>{len(filas):,}</b><span>páginas releídas</span></div>
<div class="dato"><b>{len(aportan):,}</b><span>con información recuperable</span></div>
<div class="dato"><b>{len(buenos) - len(aportan):,}</b><span>solo sellos, logos o firmas</span></div>
<div class="dato"><b>{errores}</b><span>errores de lectura</span></div>
</div>

<h2>Qué modelo, y por qué</h2>
<p>La tarea no es OCR: es comprensión de gráficos — reconstruir la tabla que hay detrás de unas
barras, seguir las flechas de un flujograma, leer los rótulos de un esquema. Los OCR
especializados de menos de 1B, que ganan OmniDocBench, no compiten aquí; y los modelos
<em>especializados en gráficos</em> (OneChart, TinyChart, DePlot) quedan por debajo de los VLM
generalistas en las comparativas de 2026.</p>
<p>El benchmark que mide esto es <b>CharXiv</b>: 2.323 gráficos reales con preguntas de
razonamiento. Estado del arte a agosto de 2026:</p>
<div class="tabla-envoltorio">
<table><thead><tr><th>Modelo</th><th>Organización</th><th class="n">CharXiv RQ</th><th>Pesos</th></tr></thead>
<tbody>{fila_lb}</tbody></table>
</div>
<p><b>Qwen3.8-27B</b> (14 de agosto de 2026) es el mejor de pesos abiertos hasta 30B y queda a
3,3 puntos del mejor modelo del mundo. Es Apache-2.0 y cabe en una sola RTX A6000 — que es
exactamente lo que concede la QoS del clúster. El salto sobre su antecesor de abril
(78,4 → 90,2) es de otra generación.</p>
<blockquote>Honestidad sobre el límite: <b>Chartography</b> (agosto 2026) mide gráficos
profesionales difíciles y ahí la mejor de 30 configuraciones frontera llega al 45,0 %. Ningún
modelo de hoy lee bien un gráfico técnico exigente. En este corpus lo que hay son flujogramas,
tablas dibujadas y recuadros de firma — el régimen fácil — pero es la razón por la que cada
lectura entra etiquetada como lectura de máquina, no como texto del contrato.</blockquote>

<h2>Cómo se detectó qué releer</h2>
<p>Releer las 107.184 páginas del corpus con un modelo de 27B costaría del orden de cien horas
de GPU para tocar, en su mayoría, texto ya bien transcrito. Se releyeron las páginas que dos
señales independientes marcan como sospechosas:</p>
<ul>
<li><b>El marcador del OCR</b> — 20.845 placeholders <code>![image](…)</code> en 2.916 páginas
de 2.066 documentos. La URL siempre es una alucinación.</li>
<li><b>La página casi vacía</b> — la página mediana rinde 2.865 caracteres; 1.128 páginas
rinden menos de 250 <em>sin</em> marcador alguno. Son planos a página completa y escaneos que
el OCR devolvió en blanco. A estas se les aplica otro prompt: no "describe el gráfico" sino
"transcribe todo lo que puedas".</li>
</ul>

<h2>Qué encontró</h2>
<div class="tabla-envoltorio">
<table><thead><tr><th>Tipo</th><th class="n">Páginas</th></tr></thead><tbody>{fila_tipos}</tbody></table>
</div>
{f'<p>Al fusionar: <b>{fusion["insertadas"]:,}</b> lecturas insertadas en <b>{fusion["docs"]:,}</b> documentos, y <b>{fusion["marcadores"]:,}</b> marcadores con URL falsa eliminados del corpus.</p>' if fusion else ''}

<h2>Cómo entra al índice sin contaminarlo</h2>
<p>Una lectura de máquina mezclada con el articulado sería indistinguible de una cláusula, y el
verificador adversario la daría por buena. Por eso cada bloque insertado se identifica:</p>
<pre class="bloque">**Figura de la página 30 leída de la imagen — ANEXO 1: Flujograma SAR.**
Tipo: flujograma. Lectura automática con modelo de visión (Qwen3.8-27B);
no es texto transcrito del contrato.

[contenido]

*Nota de lectura: los rótulos del carril inferior están borrosos.*</pre>
<ul>
<li>El generador debe decir siempre que un dato sale de la lectura de una figura.</li>
<li>El verificador acepta un bloque de figura como sustento de lo que la figura muestra, no del
tenor literal de una cláusula; y un valor marcado <code>[ilegible]</code> no sustenta cifra
alguna.</li>
<li>Solo entra lo que aporta tokens que no estaban ya en el texto de esa página: si el modelo
se puso a transcribir el cuerpo, la lectura se descarta.</li>
</ul>

<h2>La muestra: página escaneada contra lo que el modelo leyó</h2>
<p>Es la única forma honesta de juzgar una extracción visual. Muestra estratificada por tipo con
semilla fija; los últimos casos son descartes deliberados.</p>
{''.join(tarjetas)}

<footer>Corpus de contratos de suministro del SEIN publicados por Osinergmin ·
extracción con Qwen3.8-27B sobre vLLM en el clúster Khipu (UTEC) ·
{len(rescates):,} de las páginas releídas venían casi vacías del OCR.</footer>
</div>"""
    args.salida.write_text(doc, encoding="utf-8")
    print(f"escrito {args.salida} ({args.salida.stat().st_size / 1e6:.1f} MB)")
    print(f"tarjetas con imagen: {len(tarjetas)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
