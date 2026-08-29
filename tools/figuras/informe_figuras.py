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
/* Identidad: el vocabulario del dibujo técnico eléctrico — papel de vellum
   frío, tinta de instrumento, rótulos monoespaciados. Nada de crema cálida. */
:root{
  --papel:#f4f6f5; --lamina:#ffffff; --lamina-2:#eaeeed;
  --tinta:#151a1b; --tinta-media:#5c6a6a; --linea:#d3dad8;
  --instrumento:#0d6b60; --instrumento-suave:#e2efec;
  --descarte:#a8521c; --alerta:#8a6a12;
  /* El escaneo es papel blanco en los dos temas a propósito: sobre fondo
     oscuro una página escaneada se lee mal y engaña sobre su calidad. */
  --escaneo:#ffffff;
}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){
  --papel:#0d1112; --lamina:#151b1c; --lamina-2:#1d2526;
  --tinta:#e8efed; --tinta-media:#93a3a1; --linea:#2a3435;
  --instrumento:#4cc3b1; --instrumento-suave:#12302c;
  --descarte:#d98a53; --alerta:#d6b24a;
}}
:root[data-theme="dark"]{
  --papel:#0d1112; --lamina:#151b1c; --lamina-2:#1d2526;
  --tinta:#e8efed; --tinta-media:#93a3a1; --linea:#2a3435;
  --instrumento:#4cc3b1; --instrumento-suave:#12302c;
  --descarte:#d98a53; --alerta:#d6b24a;
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{
  margin:0; background:var(--papel); color:var(--tinta);
  font-family:"Source Serif 4",Georgia,"Times New Roman",serif;
  font-size:17px; line-height:1.62;
}
.wrap{max-width:1080px;margin:0 auto;padding:clamp(2rem,5vw,4.5rem) 1.25rem 6rem}
.prosa{max-width:66ch}

/* --- Cabecera --- */
.eyebrow{
  font-family:"IBM Plex Mono",ui-monospace,Menlo,monospace;
  font-size:.7rem; letter-spacing:.14em; text-transform:uppercase;
  color:var(--instrumento); margin:0 0 .9rem;
}
h1{
  font-family:Archivo,"Helvetica Neue",Arial,sans-serif;
  font-weight:700; font-size:clamp(2rem,4.6vw,3.1rem); line-height:1.04;
  letter-spacing:-.03em; text-wrap:balance; margin:0 0 .9rem; max-width:20ch;
}
.entradilla{font-size:1.12rem;color:var(--tinta-media);margin:0 0 2.75rem;max-width:60ch}

h2{
  font-family:Archivo,"Helvetica Neue",Arial,sans-serif;
  font-weight:600; font-size:1.3rem; letter-spacing:-.015em; text-wrap:balance;
  margin:3.4rem 0 1rem; padding-top:1rem; border-top:1px solid var(--linea);
}
h2 .paso{
  font-family:"IBM Plex Mono",ui-monospace,monospace; font-size:.72rem;
  color:var(--instrumento); letter-spacing:.1em; display:block;
  margin-bottom:.35rem; font-weight:500;
}
h3{
  font-family:"IBM Plex Mono",ui-monospace,Menlo,monospace;
  font-size:.78rem; font-weight:500; letter-spacing:.02em;
  color:var(--tinta-media); margin:0 0 .6rem; word-break:break-all;
}
p{margin:0 0 1.05rem}
ul{margin:0 0 1.05rem;padding-left:1.15rem}
li{margin:.3rem 0}
strong{font-weight:600}
code{font-family:"IBM Plex Mono",ui-monospace,monospace;font-size:.88em;
  background:var(--lamina-2);padding:.08em .32em;border-radius:3px}

/* --- Cifras de cabecera --- */
.cifras{display:grid;grid-template-columns:repeat(auto-fit,minmax(158px,1fr));
  gap:1px;background:var(--linea);border:1px solid var(--linea);margin:0 0 1.4rem}
.dato{background:var(--lamina);padding:1rem 1.05rem}
.dato b{
  display:block; font-family:Archivo,Arial,sans-serif; font-weight:700;
  font-size:1.85rem; line-height:1.1; letter-spacing:-.03em;
  font-variant-numeric:tabular-nums;
}
.dato span{
  display:block; margin-top:.2rem;
  font-family:"IBM Plex Mono",ui-monospace,monospace;
  font-size:.68rem; letter-spacing:.07em; text-transform:uppercase;
  color:var(--tinta-media);
}

/* --- Tablas --- */
.desborde{overflow-x:auto;margin:0 0 1.3rem;border:1px solid var(--linea);background:var(--lamina)}
table{width:100%;border-collapse:collapse;font-size:.92rem;min-width:460px}
th,td{padding:.52rem .8rem;text-align:left;border-bottom:1px solid var(--linea)}
th{
  font-family:"IBM Plex Mono",ui-monospace,monospace; font-weight:500;
  font-size:.68rem; letter-spacing:.08em; text-transform:uppercase;
  color:var(--tinta-media); background:var(--lamina-2);
}
tbody tr:last-child td{border-bottom:none}
td.n{text-align:right;font-variant-numeric:tabular-nums;
  font-family:"IBM Plex Mono",ui-monospace,monospace;font-size:.88rem}
tr.destacada td{background:var(--instrumento-suave);font-weight:600}
tr.destacada td:first-child{box-shadow:inset 3px 0 0 var(--instrumento)}

blockquote{
  margin:0 0 1.3rem; padding:.95rem 1.1rem; background:var(--lamina);
  border:1px solid var(--linea); border-left:3px solid var(--alerta);
  color:var(--tinta-media); font-size:.96rem;
}
pre.bloque{
  background:var(--lamina); border:1px solid var(--linea); padding:.9rem 1rem;
  overflow-x:auto; margin:0 0 1.3rem;
  font-family:"IBM Plex Mono",ui-monospace,monospace; font-size:.78rem; line-height:1.55;
}

/* --- Láminas: escaneo contra lectura, como plancha y pie en un informe --- */
.par{border-top:1px solid var(--linea);padding:1.5rem 0 .4rem}
.par:first-of-type{border-top:2px solid var(--tinta)}
.chips{display:flex;gap:.4rem;flex-wrap:wrap;margin:0 0 .55rem}
.chip{
  font-family:"IBM Plex Mono",ui-monospace,monospace; font-size:.66rem;
  letter-spacing:.05em; text-transform:uppercase; padding:.16rem .5rem;
  border:1px solid var(--linea); color:var(--tinta-media); white-space:nowrap;
}
.chip.si{color:var(--instrumento);border-color:var(--instrumento);background:var(--instrumento-suave)}
.chip.no{color:var(--descarte);border-color:var(--descarte)}
.chip.conf-baja{color:var(--alerta);border-color:var(--alerta)}
.titulo{margin:.1rem 0 .35rem;font-weight:600}
.sec{margin:.1rem 0;font-size:.88rem;color:var(--tinta-media)}
.lado{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:1.1rem;
  align-items:start;margin-top:.85rem}
@media(max-width:840px){.lado{grid-template-columns:1fr}}
figure{margin:0;border:1px solid var(--linea);background:var(--escaneo);overflow:hidden}
figure img{display:block;width:100%;height:auto}
pre.salida{
  margin:0; background:var(--lamina); border:1px solid var(--linea);
  padding:.85rem .95rem; font-family:"IBM Plex Mono",ui-monospace,monospace;
  font-size:.76rem; line-height:1.6; white-space:pre-wrap; word-break:break-word;
  max-height:600px; overflow:auto;
}
footer{
  margin-top:3.5rem; padding-top:1.1rem; border-top:1px solid var(--linea);
  color:var(--tinta-media); font-size:.85rem;
  font-family:"IBM Plex Mono",ui-monospace,monospace; line-height:1.7;
}
a{color:var(--instrumento)}
:focus-visible{outline:2px solid var(--instrumento);outline-offset:2px}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--figuras", type=Path, default=BASE / "out" / "figuras.jsonl")
    ap.add_argument("--fusion", type=Path, default=BASE / "informe_fusion.json")
    ap.add_argument("--verificacion", type=Path, default=BASE / "out" / "verificacion.jsonl")
    ap.add_argument("--muestras", type=int, default=18)
    ap.add_argument("--salida", type=Path, default=BASE / "informe_figuras.html")
    args = ap.parse_args()

    # El fichero acumula una línea por intento: una página que fallo y luego se
    # releyó aparece dos veces. Se queda la última lectura de cada clave, que es
    # el estado real de esa página.
    por_clave: dict[str, dict] = {}
    for l in args.figuras.read_text().splitlines():
        if l.strip():
            d = json.loads(l)
            por_clave[d["clave"]] = d
    filas = list(por_clave.values())
    buenos = [f for f in filas if not f.get("error")]
    errores = len(filas) - len(buenos)
    aportan = [f for f in buenos if f.get("aporta_informacion")]
    rescates = [f for f in buenos if f.get("rescate")]
    tipos: dict[str, int] = {}
    for f in buenos:
        tipos[f.get("tipo", "?")] = tipos.get(f.get("tipo", "?"), 0) + 1

    revisadas = reparos = 0
    ejemplos_reparo: list[tuple[str, str, str]] = []
    if args.verificacion.exists():
        for linea in args.verificacion.read_text(encoding="utf-8").splitlines():
            if not linea.strip():
                continue
            v = json.loads(linea)
            if v.get("error"):
                continue
            revisadas += 1
            for d in v.get("dudosas") or []:
                reparos += 1
                if len(ejemplos_reparo) < 6:
                    ejemplos_reparo.append(
                        (v["clave"], d.get("fragmento", ""), d.get("motivo", ""))
                    )

    fusion = {}
    if args.fusion.exists():
        inf = json.loads(args.fusion.read_text())
        fusion = {
            "docs": sum(1 for i in inf if i.get("insertadas")),
            "tocados": sum(1 for i in inf if i.get("cambio")),
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

    if revisadas:
        filas_rep = "".join(
            f"<tr><td><code>{html.escape(k[:40])}</code></td>"
            f"<td>{html.escape(f[:70])}</td><td>{html.escape(m[:90])}</td></tr>"
            for k, f, m in ejemplos_reparo
        )
        revision_html = (
            f'<p><strong>{revisadas:,}</strong> lecturas de dibujo pasaron por la segunda '
            f'pasada; <strong>{reparos:,}</strong> afirmaciones quedaron marcadas como no '
            f'confirmadas.</p>'
            + (
                '<div class="desborde"><table><thead><tr><th>Página</th>'
                '<th>Lo que escribió</th><th>Por qué no se confirma</th></tr></thead>'
                f"<tbody>{filas_rep}</tbody></table></div>"
                if filas_rep
                else ""
            )
        )
    else:
        revision_html = ""

    doc = f"""<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@600;700&family=IBM+Plex+Mono:wght@400;500&family=Source+Serif+4:ital,opsz,wght@0,8..60,400;0,8..60,600;1,8..60,400&display=swap">
<title>Figuras perdidas del corpus SEIN</title>
<style>{CSS}</style>
<div class="wrap">

<header class="prosa">
<p class="eyebrow">Corpus de contratos SEIN · Osinergmin</p>
<h1>Leer lo que el OCR no supo leer</h1>
<p class="entradilla">El pipeline OCR transcribe texto y tablas muy bien, pero cuando encuentra
un diagrama escribe un marcador con una URL inventada y sigue de largo. Detrás de esos
marcadores se quedaba, sin llegar al índice, contenido real.</p>
</header>

<section class="cifras">
<div class="dato"><b>{len(buenos):,}</b><span>páginas releídas</span></div>
<div class="dato"><b>{len(aportan):,}</b><span>con información nueva</span></div>
<div class="dato"><b>{len(buenos) - len(aportan):,}</b><span>solo sellos y firmas</span></div>
<div class="dato"><b>{errores}</b><span>que el modelo no pudo leer</span></div>
</section>

<div class="prosa">
<h2><span class="paso">Etapa 1 — decidir el modelo</span>Comprensión de gráficos, no OCR</h2>
<p>Reconstruir la tabla que hay detrás de unas barras, seguir las flechas de un flujograma,
leer los rótulos de un unifilar: eso no lo hace un OCR. Los especializados de menos de 1B que
ganan OmniDocBench no compiten aquí, y los modelos <em>especializados en gráficos</em>
(OneChart, TinyChart, DePlot) quedan por debajo de los VLM generalistas en las comparativas
de 2026.</p>
<p>El banco de pruebas que mide exactamente esta capacidad es <strong>CharXiv</strong>:
2.323 gráficos reales con preguntas de razonamiento.</p>
</div>

<div class="desborde">
<table><thead><tr><th>Modelo</th><th>Organización</th><th class="n">CharXiv RQ</th><th>Pesos</th></tr></thead>
<tbody>{fila_lb}</tbody></table>
</div>

<div class="prosa">
<p><strong>Qwen3.8-27B</strong> (14 de agosto de 2026) es el mejor de pesos abiertos hasta 30B
y queda a 3,3 puntos del mejor modelo del mundo. Apache-2.0, y cabe en una sola RTX A6000 —
que es exactamente lo que concede la cuota del clúster. El salto sobre su antecesor de abril
(78,4 → 90,2) es de otra generación.</p>
<blockquote>Sobre el límite, sin adornos: <strong>Chartography</strong> (agosto de 2026) mide
gráficos profesionales difíciles y ahí la mejor de 30 configuraciones frontera llega al 45 %.
Ningún modelo de hoy lee bien un gráfico técnico exigente. En este corpus lo que hay son
flujogramas, unifilares y tablas dibujadas — el régimen fácil — pero es la razón por la que
cada lectura entra al índice etiquetada como lectura de máquina.</blockquote>

<h2><span class="paso">Etapa 2 — decidir qué releer</span>Dos señales, 4.034 páginas</h2>
<p>Releer las 107.184 páginas del corpus con un modelo de 27B costaría del orden de cien horas
de GPU para tocar, en su mayoría, texto ya bien transcrito. Se releyó lo que dos señales
independientes marcan como sospechoso:</p>
<ul>
<li><strong>El marcador del OCR.</strong> 20.847 placeholders <code>![image](…)</code> en 2.916
páginas de 2.066 documentos. La URL siempre es inventada.</li>
<li><strong>La página casi vacía.</strong> La página mediana rinde 2.865 caracteres; 1.128
rinden menos de 250 <em>sin</em> marcador alguno. Son unifilares a página completa y escaneos
que el OCR devolvió en blanco.</li>
</ul>
<p>Mirarlas confirma la sospecha: una página que rindió 71 caracteres es un unifilar completo
con subestaciones, secciones de cable y tensiones, del que el OCR sacó solo el nombre de la
firmante.</p>

<h2><span class="paso">Etapa 3 — leer</span>Qué encontró</h2>
</div>
<div class="desborde">
<table><thead><tr><th>Tipo de elemento</th><th class="n">Páginas</th></tr></thead><tbody>{fila_tipos}</tbody></table>
</div>
<div class="prosa">
{f'<p>Al fusionar: <strong>{fusion["insertadas"]:,}</strong> lecturas insertadas en <strong>{fusion["docs"]:,}</strong> documentos. Y, con figura o sin ella, <strong>{fusion["marcadores"]:,}</strong> marcadores con URL inventada desaparecieron de <strong>{fusion["tocados"]:,}</strong> documentos.</p>' if fusion else ''}

<h2><span class="paso">Etapa 4 — desconfiar</span>El modelo inventa cifras dentro de los dibujos</h2>
<p>Comprobado, no supuesto. En el flujograma de atención de interrupciones de un contrato de
Atria, la lectura escribió <strong>«Tiempo de llegada: Max 30'»</strong>. Ampliando la página,
el rótulo real dice <strong>«Tiempo de llegada / Mapa SAR»</strong>: no hay ninguna cifra. Todo
lo demás de esa lectura es fiel — los carriles, los rombos de decisión, «Reportar caso en
(Teams y CRM)» — pero se inventó un compromiso de nivel de servicio perfectamente plausible.</p>
<p>El verificador adversario del RAG <em>no puede cazar esto</em>: comprueba que una afirmación
esté sustentada por el fragmento que cita, y el fragmento contiene la invención. El error se
cometió antes, al leer la imagen.</p>
<p>Por eso hay una segunda pasada que devuelve la imagen al modelo junto con lo que escribió y
le pide que señale lo que no aparece en ella. Lo señalado no se borra —un hueco silencioso no
se puede auditar— sino que se tacha con su motivo:</p>
<pre class="bloque">- ~~Tiempo de llegada: Max 30'~~ [no confirmado]

&gt; **Una segunda lectura de la imagen no encontró esto:**
&gt; - Tiempo de llegada: Max 30' — el dibujo dice "Tiempo de llegada / Mapa SAR"</pre>
{revision_html}

<h2><span class="paso">Etapa 5 — indexar</span>Cómo entra sin contaminar el índice</h2>
<p>Una lectura de máquina mezclada con el articulado sería indistinguible de una cláusula, y
el verificador adversario la daría por buena. Por eso cada bloque se identifica:</p>
</div>
<pre class="bloque">**Figura de la página 30 leída de la imagen — ANEXO 1: Flujograma SAR.**
Tipo: flujograma. Lectura automática con modelo de visión (Qwen3.8-27B);
no es texto transcrito del contrato.

[contenido]

*Nota de lectura: los rótulos del carril inferior están borrosos.*</pre>
<div class="prosa">
<ul>
<li>El generador debe decir siempre que un dato sale de la lectura de una figura.</li>
<li>El verificador acepta el bloque como sustento de lo que la figura muestra, no del tenor
literal de una cláusula; un valor marcado <code>[ilegible]</code> no sustenta ninguna cifra.</li>
<li>Solo entra lo que aporta términos que no estaban ya en el texto de esa página: si el
modelo se puso a transcribir el cuerpo, la lectura se descarta.</li>
</ul>

<h2><span class="paso">Verificación</span>La página escaneada contra lo que el modelo leyó</h2>
<p>Es la única forma honesta de juzgar una extracción visual. Muestra estratificada por tipo
con semilla fija; los últimos casos son descartes deliberados.</p>
</div>

{''.join(tarjetas)}

<footer>
Extracción con Qwen3.8-27B (AWQ INT4) sobre vLLM en una RTX A6000 del clúster Khipu, UTEC.<br>
{len(rescates):,} de las páginas releídas venían casi vacías del OCR.<br>
Corpus de contratos de suministro de usuarios libres del SEIN publicados por Osinergmin.
</footer>
</div>"""
    args.salida.write_text(doc, encoding="utf-8")
    print(f"escrito {args.salida} ({args.salida.stat().st_size / 1e6:.1f} MB)")
    print(f"tarjetas con imagen: {len(tarjetas)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
