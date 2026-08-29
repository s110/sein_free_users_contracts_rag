# Pipeline de enriquecimiento de figuras

Recupera el contenido visual que el OCR no supo leer y lo incorpora al vault.
El razonamiento completo — qué modelo, por qué, cómo se decidió la
cuantización y qué garantías se mantienen — está en
[`docs/FIGURAS.md`](../../docs/FIGURAS.md).

Estos scripts no son parte del servicio: corren a mano, por lotes, cuando hay
corpus nuevo que enriquecer.

| Script | Dónde corre | Qué hace |
|---|---|---|
| `prep_figuras.py` | Khipu (CPU) | Detecta las páginas a releer y las renderiza a 200 DPI |
| `render_pendientes.py` | Khipu (CPU) | Renderiza lo que falte sin reconstruir el worklist |
| `extraer_figuras.py` | Khipu (GPU) | Cliente asíncrono contra vLLM; JSON con esquema forzado |
| `fusionar_figuras.py` | **Khipu** | Inserta las lecturas en el vault y lista qué reindexar |
| `verificar_enriquecimiento.py` | local | Comprueba las invariantes del vault enriquecido |
| `comparar_cuantizacion.py` | local | FP8 vs AWQ sobre las mismas páginas |
| `informe_figuras.py` | local | Página HTML de revisión: escaneo contra lo leído |
| `aplicar_oleada.sh` | local | Encadena fusión → rsync → reindexado → verificación |

SLURM: `prep_figuras.slurm` (CPU), `figuras_job.slurm` (GPU, encadenado y
reanudable), `figuras_smoke.slurm` (validación del stack en una hora).

## Orden de ejecución

```bash
# En Khipu
sbatch ~/osinergmin/figuras/prep_figuras.slurm      # detectar y renderizar
sbatch ~/osinergmin/figuras/figuras_job.slurm       # leer con Qwen3.8-27B

apptainer exec --bind $HOME/osinergmin:$HOME/osinergmin $HOME/containers/vllm.sif \
  python3 ~/osinergmin/figuras/fusionar_figuras.py \
    --figuras ~/osinergmin/figuras/out/figuras.jsonl \
    --vault   ~/osinergmin/vault \
    --salida  ~/osinergmin/figuras/out \
    --aplicar

# En el Mac, tras el rsync del vault
docker compose run --rm -v <ruta>/docs_a_reindexar.txt:/data/lista.txt:ro \
  ingest sein-rag-ingest --reindexar-lista /data/lista.txt
backend/.venv/bin/python tools/figuras/verificar_enriquecimiento.py

# informe_figuras.py necesita Pillow para las miniaturas; el venv del backend
# no lo trae y el del pipeline OCR sí.
/Volumes/Datos/proyectos_personales/ocr_pdf_markdown/.venv/bin/python \
  tools/figuras/informe_figuras.py
```

`aplicar_oleada.sh` hace los cuatro últimos pasos de una vez y se puede correr
varias veces mientras la extracción avanza: la fusión es idempotente y el
reindexado toca solo los documentos que cambiaron en esa pasada. Sin
`--aplicar` es un simulacro que imprime el recuento y los motivos de descarte
sin tocar un solo fichero. Conviene mirarlo antes.

Aunque una página no aporte ninguna figura, su documento cambia igual: los
marcadores `![image](https://i.imgur.com/…)` se borran siempre. Son URLs
inventadas por el OCR dentro de un corpus legal.

## Dos avisos que cuestan caro si se ignoran

**La fusión va en Khipu.** `com.osinergmin.vault.sync` hace `rsync -az` desde
Khipu al Mac cada dos horas. Enriquecer solo en local significa perderlo todo
en el siguiente rsync, en silencio.

**El cliente corre con el Python del contenedor.** En el nodo de login
`/usr/bin/python3` es 3.11; en `ds001` es 3.6. Un venv creado en el login
apunta ahí por symlink y revienta en el nodo de cómputo.
