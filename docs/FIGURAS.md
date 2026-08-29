# Enriquecimiento de figuras: leer lo que el OCR no supo leer

El pipeline OCR (GLM-OCR 0.9B) transcribe texto y tablas mejor que casi
cualquier cosa de su tamaño, pero no *entiende* imágenes. Cuando encuentra un
diagrama, un flujograma o un gráfico escribe un placeholder y sigue:

```markdown
![image](https://i.imgur.com/3X77777.png)
```

Esa URL es una alucinación: no apunta a nada, nunca existió. Y detrás de ella
se quedaba, sin llegar al índice, contenido real — flujogramas de
procedimientos de interrupción, esquemas unifilares, anexos dibujados como
imagen.

**Alcance del problema en el corpus:** 20.845 marcadores repartidos en
**2.916 páginas** de **2.066 documentos** (de 7.767).

### La segunda fuga: páginas que el OCR no pudo leer

El marcador solo aparece cuando el OCR *reconoce* que hay una figura. Hay un
segundo agujero que no deja marcador: páginas que salieron prácticamente en
blanco. La página mediana del corpus rinde **2.865 caracteres**; hay **1.128
páginas** que rinden menos de 250 y no llevan ningún marcador — 244 de ellas
menos de 50. Son planos a página completa, anexos dibujados y escaneos malos
que el OCR devolvió vacíos.

Entre las dos señales: **4.034 páginas** a releer, de 107.184 que tiene el
corpus. Releerlo entero con un modelo de 27B costaría del orden de cien horas
de GPU para tocar, en su mayoría, texto que ya está bien transcrito.

Mirar esas páginas confirma la sospecha. Una que rindió 71 caracteres es un
**diagrama unifilar** completo — subestaciones, secciones de cable (107 mm²
AAAC), tensiones de 60 y 10 kV — del que el OCR sacó únicamente el nombre de la
firmante. Otra de 212 caracteres es el unifilar del sistema de Hidrandina S.A.
en Chimbote, Nepeña y Casma: números de suministro, potencias de
transformación (24/24/10 MVA), longitudes de línea (13,60 km, 17,45 km,
22,42 km). Nada de eso estaba en el índice.

## El modelo: Qwen3.8-27B

La tarea no es OCR. Es comprensión de gráficos: reconstruir la tabla que hay
detrás de unas barras, seguir las flechas de un flujograma, leer los rótulos de
un esquema. En eso los OCR especializados de <1B — que ganan OmniDocBench —
no compiten; hace falta un VLM generalista con razonamiento visual.

El benchmark que mide exactamente esto es **CharXiv** (2.323 gráficos reales de
papers científicos, preguntas de razonamiento). Estado del arte a agosto 2026:

| Modelo | CharXiv RQ | Pesos | Cabe en 1 GPU de 48 GB |
|---|---|---|---|
| Claude Mythos 5 | 93.5 | cerrado | — |
| Qwen3.8 Max | 93.5 | abierto | no |
| Kimi K3 | 91.3 | cerrado | — |
| Claude Opus 4.7 | 91.0 | cerrado | — |
| Qwen3.8-Flash-Next | 90.6 | abierto | no |
| **Qwen3.8-27B** | **90.2** | **Apache-2.0** | **sí** |
| GLM-5.3-Flash | 89.4 | abierto | no |
| Qwen3.6-27B | 78.4 | Apache-2.0 | sí |

**Qwen3.8-27B** (14 de agosto de 2026) es el mejor modelo de pesos abiertos de
hasta 30B y queda a 3,3 puntos del mejor modelo del mundo. Es el elegido: cabe
en una RTX A6000, la licencia permite uso comercial y el salto sobre su
antecesor de abril (78.4 → 90.2) es de otra generación.

Complementa al OCR, no lo reemplaza: en OmniDocBench 1.5 GLM-OCR marca 94.62 y
Qwen3.8-27B 91.1. El especialista sigue siendo mejor transcribiendo; el
generalista es el único que lee un gráfico. Cada uno hace lo suyo.

Los modelos *especializados* en gráficos (OneChart, TinyChart, DePlot) se
descartaron con dato: en las comparativas de 2026 sobre extracción de datos de
gráficos quedan por debajo de los VLM generalistas, que traen razonamiento
visual que ellos no tienen.

### Lo que este modelo no resuelve

CharXiv mide gráficos de papers. **Chartography** (agosto 2026) mide gráficos
profesionales difíciles — estimación visual fina, convenciones de dominio,
geometría compleja — y ahí la mejor configuración de 30 modelos frontera llega
al **45,0 %**. Ningún modelo de hoy lee bien un gráfico técnico exigente.

Para este corpus eso importa poco: lo que hay son flujogramas de procedimiento,
tablas dibujadas como imagen y recuadros de firma — el régimen fácil. Pero es
la razón por la que cada lectura entra al vault etiquetada como lectura de
máquina y con su nota de confianza, en vez de mezclarse con el articulado.

### La cuantización se eligió midiendo, no suponiendo

27B en bf16 son ~54 GB: no entran en los 48 GB de la A6000, y la QoS
`a-postgrado` de Khipu concede **una** GPU. Quedan dos candidatos:

- **FP8 oficial de Qwen** (29 GB). La A6000 es Ampere (sm_86) y no tiene
  unidades FP8 nativas, así que vLLM cae en `MarlinFP8ScaledMMLinearKernel`:
  guarda los pesos en FP8 y los descomprime a FP16 dentro del kernel.
- **AWQ INT4 W4A16** de la comunidad (20 GB), con kernels Marlin nativos de
  Ampere.

La primera corrida arrancó con FP8 y dio **7 tokens/s por secuencia** — a ese
ritmo el corpus tardaba ~13 h. Antes de cambiar por una intuición se hizo una
comparación controlada: **las mismas 10 páginas** con las dos cuantizaciones.

| | FP8 (Marlin dequant) | AWQ INT4 |
|---|---|---|
| Generación agregada | 73-83 tok/s @ 12 concurrentes | **373 tok/s @ 16** |
| Por secuencia | ~7 tok/s | ~23 tok/s |
| Tablas extraídas | — | **idénticas carácter a carácter** |

Donde ambos leyeron, la salida coincidió byte a byte: las mismas tablas, las
mismas cifras, los mismos encabezados. AWQ es ~5× más rápido sin degradar la
lectura, así que la corrida va con AWQ.

Las tres discrepancias de esa muestra **no eran de calidad**: eran
`JSONDecodeError` del cliente. Ver abajo.

### El razonamiento estaba encendido y nadie lo había pedido

Con AWQ y 16 peticiones en paralelo el ritmo real era de ~0,06 páginas/s — unas
20 horas para el corpus — pese a que el motor reportaba 360 tokens/s de
generación. La cuenta no cerraba: si cada respuesta fuesen ~500 tokens, 16 en
vuelo a 360 tok/s darían 0,7 páginas/s. La única explicación posible era que
las respuestas fuesen diez veces más largas de lo esperado.

Lo eran. La plantilla de chat de Qwen3.8 dice:

```jinja
{%- if enable_thinking is undefined or enable_thinking is true %}
```

El modo de razonamiento se activa **cuando nadie dice lo contrario**. El bloque
`<think>` se llevaba casi todo el presupuesto de tokens antes de llegar al JSON
— lo que además explica los `JSONDecodeError`: no era el JSON el que estaba
mal, era que la generación se agotaba en el razonamiento y se cortaba a medias.

La petición ahora lleva:

```json
"chat_template_kwargs": {"enable_thinking": false}
```

Transcribir una figura no necesita cadena de pensamiento.

### El JSON del modelo no siempre es JSON

Tres de diez respuestas fallaron al parsear a ~2.500 caracteres. La causa
importa porque determina el arreglo:

- `max_tokens` se subió de 2.400 a 3.600.
- El cliente ya no hace `json.loads` a pelo: si falla, reintenta con el
  fragmento entre la primera `{` y la última `}` — lo que salva un envoltorio
  de razonamiento sin inventar contenido.
- El registro del error guarda `finish_reason` y los últimos 400 caracteres
  crudos, para poder diagnosticar en vez de adivinar.
- **Una página con error ya no cuenta como hecha.** `cargar_hechos` ignora las
  filas con `error`, así que el siguiente job de la cadena las reintenta. Antes
  un fallo transitorio perdía esa página para siempre.

## El pipeline

```
vault/*.md ──┐
             ├─▶ prep_figuras.py ─▶ worklist.jsonl + imgs/*.jpg   (CPU, SLURM)
data/pdfs/ ──┘        detecta marcadores, renderiza esas páginas a 200 DPI

imgs/*.jpg ──▶ vLLM + Qwen3.8-27B ──▶ out/figuras.jsonl           (GPU, SLURM)
                  extraer_figuras.py: JSON con esquema forzado

figuras.jsonl ──▶ fusionar_figuras.py ──▶ vault/*.md editado      (EN KHIPU)
                  + docs_a_reindexar.txt

    rsync khipu:vault/ ──▶ vault local                            (launchd, 2 h)

docs_a_reindexar.txt ──▶ sein-rag-ingest --reindexar-lista        (local)
```

### El marcador rinde menos de lo que parecía

Sobre las primeras páginas con marcador procesadas, el resultado fue tozudo:

- 9 de 12 eran **firmas y sellos** — el modelo las clasificó bien y las
  descartó solo.
- Las 3 restantes eran tablas dibujadas como imagen que el modelo leyó
  correctamente… y que **el OCR ya tenía transcritas**. Medido con la prueba
  de novedad: **cero tokens nuevos** en las tres.

O sea: GLM-OCR sí captura las tablas. El `![image]` de esas páginas apunta a la
firma de al lado, no a la tabla. El marcador señala "aquí hay algo dibujado",
no "aquí se perdió información".

Por eso el worklist se reordenó por rendimiento esperado:

| Orden | Páginas | Por qué |
|---|---|---|
| 1º | 1.118 rescates | El índice no tiene **nada** de esas páginas |
| 2º | 19 marcadores con alt descriptivo | `flujograma`, `diagram`, `chart`: no pueden estar en el texto |
| 3º | 2.897 marcadores genéricos | Mayoritariamente firmas; se procesan igual, pero al final |

Todo se procesa; lo que cambia es que el valor entra primero y una
interrupción no se lleva lo importante.

### Qué se inserta y qué no

Un marcador de figura no significa que se haya perdido información. La mayoría
son sellos notariales, logotipos y firmas manuscritas: el nombre y el cargo del
firmante ya están transcritos como texto al lado. Insertarlos otra vez solo
infla el índice.

Entra en el vault únicamente lo que cumple las tres condiciones:

1. El modelo marcó `aporta_informacion: true`.
2. El tipo es visual — `grafico`, `diagrama`, `flujograma`, `esquema_unifilar`,
   `tabla_imagen`, `mapa`, `plano`, `foto`, `pagina_rescatada` — y no `firmas`
   ni `sello_logo`.
3. **Aporta tokens que no están ya en el texto de esa página.** Si el modelo se
   puso a transcribir el cuerpo en vez de la figura, la lectura se descarta.
   Las páginas rescatadas se saltan esta comprobación: venían vacías, así que no
   hay nada con lo que puedan duplicarse.

Los marcadores `![image](url-alucinada)` se borran en todos los casos.

### Dos prompts, porque son dos problemas

Una página con marcador tiene su texto ya transcrito: pedirle al modelo que lo
vuelva a transcribir solo generaría duplicados y contradicciones. A esas se les
dice explícitamente *"el texto corrido ya está capturado; limítate al gráfico"*.

Una página casi vacía es lo contrario: no hay nada capturado. A esas se les
pide transcribirlo todo. El resultado se marca `pagina_rescatada` y se inserta
con otro encabezado, porque no es lo mismo leer una figura que suplir al OCR.

### El bloque insertado dice de dónde viene

```markdown
**Figura de la página 30 leída de la imagen — ANEXO 1: Flujograma SAR.**
Tipo: flujograma. Lectura automática con modelo de visión (Qwen3.8-27B);
no es texto transcrito del contrato.

[contenido]

*Nota de lectura: los rótulos del carril inferior están borrosos.*
```

Esto no es cosmético. Después del caso Celepsa→Pluz, la arquitectura entera
está construida sobre la idea de que **cada afirmación tiene que poder
rastrearse hasta algo concreto**. Una lectura de máquina que se mezclara con el
articulado sería indistinguible de una cláusula, y el verificador adversario la
daría por buena. Por eso:

- La regla 9 de `GENERATE_SYSTEM` obliga al generador a decir siempre que un
  dato sale de la lectura de una figura, nunca a presentarlo como cláusula.
- `REFUTE_PROMPT` establece que un bloque de figura sustenta afirmaciones sobre
  lo que la figura muestra, **no** sobre el tenor literal de una cláusula, y que
  un valor marcado `[ilegible]` no sustenta ninguna cifra.
- El prompt de extracción prohíbe inferir: lo que no se distingue se escribe
  `[ilegible]`, nunca se aproxima.

### Reindexado sin reprocesar el corpus

`source_hash` es el hash del **PDF**, no del markdown. Un enriquecimiento
reescribe el `.md` y deja el PDF intacto, así que el hash no cambia y el
documento se saltaría para siempre. De ahí `--reindexar-lista`:

```bash
docker compose run --rm \
  -v /Volumes/Datos/osinergmin_data/charts/docs_a_reindexar.txt:/data/lista.txt:ro \
  ingest sein-rag-ingest --reindexar-lista /data/lista.txt
```

(El vault se monta de solo lectura, así que la lista entra por su propio bind.)

Reindexa exactamente esos documentos. No hace falta un `--force` sobre los
7.767 (que además recalcularía 126.000 embeddings sin motivo).

## Operación en Khipu

```bash
# 1. Preparación (CPU, ~5 min para las 4.034 páginas)
sbatch ~/osinergmin/figuras/prep_figuras.slurm

# 2. Extracción (GPU, encadenada y reanudable)
sbatch ~/osinergmin/figuras/figuras_job.slurm

# progreso
squeue -u $USER
tail -f ~/osinergmin/figuras/logs/vlm-*.out
```

`figuras_job.slurm` hereda las dos lecciones caras del pipeline OCR:

- **El encadenado se registra al arrancar**, con
  `sbatch --dependency=afterany:$SLURM_JOB_ID`. Registrarlo al final no sirve:
  cuando SLURM mata un job por TIMEOUT manda SIGKILL y no se ejecuta ninguna
  línea posterior. Así se rompió la cadena del OCR en el job 50046.
- **La cadena se corta sola al terminar.** Cada job cuenta los pendientes antes
  de encadenar; si no queda ninguno, sale sin dejar sucesor.

Es reanudable por clave: `figuras.jsonl` lleva un `clave` por línea y una
segunda corrida salta lo ya hecho. Con 8 h de wall time en la QoS
`a-postgrado`, eso no es un lujo.

### El cliente corre con el Python del contenedor, no con un venv

Khipu es heterogéneo. En el nodo de login `/usr/bin/python3` es 3.11; en `ds001`
es **3.6**. Un venv creado en el login guarda un symlink a `/usr/bin/python3`,
así que al ejecutarse en el nodo de cómputo resuelve al 3.6 y revienta en la
primera línea del cliente:

```
File "extraer_figuras.py", line 10
    from __future__ import annotations
SyntaxError: future feature annotations is not defined
```

El contenedor trae Python 3.12 y httpx, y es idéntico en todos los nodos. De
ahí que el job invoque `apptainer exec … python3` también para el cliente y no
solo para el servidor.


### La fusión corre en Khipu, no en el Mac

No es un detalle de comodidad. `com.osinergmin.vault.sync` hace cada dos horas:

```bash
rsync -az khipu:osinergmin/vault/ "$VAULT_LOCAL/"
```

Khipu es el origen del vault. Si el enriquecimiento se aplicara solo en local,
el siguiente rsync traería los `.md` originales de Khipu —más viejos pero
distintos en tamaño y fecha— y **borraría el trabajo**. Silenciosamente, dos
horas después, sin ningún error.

Aplicándolo en Khipu los dos lados quedan iguales y el rsync propaga el
enriquecimiento en vez de destruirlo. El script se ejecuta con el Python del
contenedor por la misma razón que el cliente de extracción.

## Qué sobrevive a qué

El bloque de figura vive dentro del `.md`, y el `.md` lo reescribe el pipeline
OCR cuando el `source_hash` del PDF cambia. En operación normal eso no pasa —
los PDFs del registro no se editan — pero conviene tenerlo presente:

| Operación | El enriquecimiento |
|---|---|
| `ocr-pipeline run` sobre PDFs sin cambios | sobrevive (se saltan por hash) |
| `ocr-pipeline run --force` o PDF modificado | **se pierde**: hay que reenriquecer |
| `sein-rag-ingest` normal | sobrevive (no toca el vault) |
| `sein-rag-ingest --force` | sobrevive (reindexa, no reescribe el vault) |

Cada documento enriquecido lleva el sello en su frontmatter, así que
recuperar la lista no depende de ningún fichero externo:

```bash
grep -l '^figuras_leidas:' "$VAULT"/*.md | wc -l
```
