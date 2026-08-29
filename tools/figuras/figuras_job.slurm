#!/bin/bash
#SBATCH --job-name=fig-vlm
#SBATCH --partition=gpu
#SBATCH --qos=a-postgrado
#SBATCH --gres=gpu:rtxa6000:1
#SBATCH --cpus-per-task=12
#SBATCH --mem=72G
#SBATCH --time=08:00:00
#SBATCH --output=/home/sebastian.lopez/osinergmin/figuras/logs/vlm-%j.out
#SBATCH --error=/home/sebastian.lopez/osinergmin/figuras/logs/vlm-%j.out

set -uo pipefail
FIG=$HOME/osinergmin/figuras
MODELO=${MODELO:-$HOME/models/qwen38-awq}
NOMBRE=qwen38
SIF=$HOME/containers/vllm.sif
SALIDA=$FIG/out/figuras.jsonl
# El wall time es de 8 h; el cliente para antes para que el resumen se escriba
# con la sesión viva. Un SIGKILL de SLURM no ejecuta ninguna línea posterior.
DEADLINE=$((6 * 3600 + 30 * 60))

# El Python del contenedor (3.12) y no el del venv: en el nodo de cómputo
# /usr/bin/python3 es 3.6 y el venv, que apunta ahí por symlink, revienta con
# `from __future__ import annotations`. El contenedor es idéntico en todos los
# nodos, que es justo lo que hace falta en un clúster heterogéneo.
PY="apptainer exec --bind $FIG:$FIG $SIF python3"

echo "== $(date -Is) job=$SLURM_JOB_ID nodo=$(hostname) =="
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader

pendientes() {
  $PY - "$SALIDA" <<'PY'
import json, pathlib, sys
base = pathlib.Path.home() / "osinergmin/figuras"
total = {json.loads(l)["clave"] for l in (base / "worklist.jsonl").read_text().splitlines() if l.strip()}
p = pathlib.Path(sys.argv[1])
hechos = set()
if p.exists():
    for l in p.read_text(errors="replace").splitlines():
        try:
            hechos.add(json.loads(l)["clave"])
        except Exception:
            pass
print(len(total - hechos))
PY
}

PEND=$(pendientes | tail -1)
echo "pendientes al arrancar: $PEND"
if [ "${PEND:-0}" -eq 0 ]; then
  echo "== nada que hacer: no encadeno y salgo =="
  exit 0
fi

# El encadenado se registra AHORA, no al final. Si SLURM mata este job por
# TIMEOUT, el SIGKILL no ejecuta ninguna línea posterior — así se rompió la
# cadena del OCR en el job 50046 y se perdieron horas de cómputo.
SIG=$(sbatch --parsable --dependency=afterany:$SLURM_JOB_ID "$FIG/figuras_job.slurm" 2>/dev/null)
echo "sucesor encadenado: ${SIG:-ninguno}"

export XDG_CACHE_HOME=$FIG/.cache
mkdir -p "$XDG_CACHE_HOME" "$FIG/out"

apptainer exec --nv \
  --bind "$HOME/models:$HOME/models" \
  --bind "$FIG:$FIG" \
  --env XDG_CACHE_HOME="$XDG_CACHE_HOME" \
  --env HF_HUB_OFFLINE=1 \
  "$SIF" \
  vllm serve "$MODELO" \
    --served-model-name "$NOMBRE" \
    --tensor-parallel-size 1 \
    --max-model-len 16384 \
    --gpu-memory-utilization 0.92 \
    --limit-mm-per-prompt '{"image":1}' \
    --max-num-seqs 20 \
    --port 8000 \
    > "$FIG/logs/vllm-$SLURM_JOB_ID.log" 2>&1 &
VPID=$!

for i in $(seq 1 150); do
  curl -sf http://127.0.0.1:8000/health >/dev/null 2>&1 && { echo "servidor arriba a los $((i*10))s"; break; }
  kill -0 $VPID 2>/dev/null || { echo "!! vllm murió"; tail -40 "$FIG/logs/vllm-$SLURM_JOB_ID.log"; exit 1; }
  sleep 10
done
curl -sf http://127.0.0.1:8000/health >/dev/null 2>&1 || {
  echo "!! timeout esperando al servidor"; tail -40 "$FIG/logs/vllm-$SLURM_JOB_ID.log"; kill $VPID; exit 1; }

# Comparación controlada contra la corrida FP8: exactamente las mismas páginas,
# para poder decidir la cuantización mirando las dos salidas y no una intuición.
if [ -f "$FIG/out/claves_comparacion.txt" ] && [ ! -s "$FIG/out/muestra_awq.jsonl" ]; then
  echo "== muestra comparativa AWQ vs FP8 =="
  $PY "$FIG/extraer_figuras.py" --modelo "$NOMBRE" --concurrencia 8 \
    --claves "$FIG/out/claves_comparacion.txt" --salida "$FIG/out/muestra_awq.jsonl"
fi

$PY "$FIG/extraer_figuras.py" \
  --modelo "$NOMBRE" --concurrencia 16 --deadline $DEADLINE --salida "$SALIDA"
rc=$?

kill $VPID 2>/dev/null
sleep 5
echo "== cliente rc=$rc | pendientes al terminar: $(pendientes | tail -1) =="
echo "== fin $(date -Is) =="
exit $rc
