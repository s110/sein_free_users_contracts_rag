#!/bin/bash
#SBATCH --job-name=fig-smoke
#SBATCH --partition=gpu
#SBATCH --qos=a-postgrado
#SBATCH --gres=gpu:rtxa6000:1
#SBATCH --cpus-per-task=12
#SBATCH --mem=64G
#SBATCH --time=01:00:00
#SBATCH --output=/home/sebastian.lopez/osinergmin/figuras/logs/smoke-%j.out
#SBATCH --error=/home/sebastian.lopez/osinergmin/figuras/logs/smoke-%j.out

set -uo pipefail
FIG=$HOME/osinergmin/figuras
MODELO=${MODELO:-$HOME/models/qwen38-fp8}
NOMBRE=${NOMBRE:-qwen38}
SIF=$HOME/containers/vllm.sif

echo "== $(date -Is) nodo=$(hostname) modelo=$MODELO =="
nvidia-smi --query-gpu=name,memory.total,compute_cap --format=csv,noheader

export XDG_CACHE_HOME=$FIG/.cache
export HF_HUB_OFFLINE=1
export VLLM_LOGGING_LEVEL=INFO
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
    --max-num-seqs 8 \
    --port 8000 \
    > "$FIG/logs/vllm-smoke-$SLURM_JOB_ID.log" 2>&1 &
VPID=$!
echo "vllm pid=$VPID"

# Espera activa: el servidor tarda varios minutos en cargar 27B.
for i in $(seq 1 90); do
  if curl -sf http://127.0.0.1:8000/health >/dev/null 2>&1; then
    echo "== servidor arriba a los $((i*10))s =="; break
  fi
  if ! kill -0 $VPID 2>/dev/null; then
    echo "!! vllm murió durante la carga"; tail -60 "$FIG/logs/vllm-smoke-$SLURM_JOB_ID.log"; exit 1
  fi
  sleep 10
done

if ! curl -sf http://127.0.0.1:8000/health >/dev/null 2>&1; then
  echo "!! timeout esperando al servidor"; tail -60 "$FIG/logs/vllm-smoke-$SLURM_JOB_ID.log"; kill $VPID; exit 1
fi

curl -s http://127.0.0.1:8000/v1/models | head -c 400; echo

# Prueba sobre 8 páginas, salida aparte para no contaminar la corrida real.
FIG_BASE=$FIG $FIG/venv/bin/python $FIG/extraer_figuras.py \
  --modelo "$NOMBRE" --concurrencia 4 --limite 8 --salida "$FIG/out/smoke.jsonl"
rc=$?
echo "== cliente rc=$rc =="
echo "== muestra de resultados =="
$FIG/venv/bin/python - <<'PY'
import json, pathlib
p = pathlib.Path.home()/"osinergmin/figuras/out/smoke.jsonl"
for l in p.read_text().splitlines()[:8]:
    d = json.loads(l)
    print("---", d.get("clave"), "|", d.get("tipo"), "| aporta:", d.get("aporta_informacion"),
          "| conf:", d.get("confianza"), "| err:", d.get("error"))
    print((d.get("contenido_markdown") or "")[:600])
PY
kill $VPID 2>/dev/null
echo "== fin $(date -Is) =="
