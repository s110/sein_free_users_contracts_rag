#!/bin/zsh
# Aplica al índice lo que la extracción lleva leído hasta ahora.
#
# Se puede correr varias veces conforme avanza la extracción: la fusión es
# idempotente (una página que ya lleva su bloque se salta) y el reindexado
# toca solo los documentos que cambiaron en esta pasada.
#
#   ./aplicar_oleada.sh            simulacro: enseña qué haría y no toca nada
#   ./aplicar_oleada.sh --aplicar  lo hace
set -u

APLICAR=${1:-}
FIG=osinergmin/figuras
LOCAL=/Volumes/Datos/osinergmin_data/charts
REPO=/Volumes/Datos/proyectos_personales/sein_free_users_contracts_rag
VAULT="/Users/sebastianlopez/Library/Application Support/osinergmin/vault"
SIF='$HOME/containers/vllm.sif'

echo "══ 1. Fusión en Khipu (el vault vive allí; el rsync lo propaga) ══"
ssh khipu "apptainer exec --bind \$HOME/osinergmin:\$HOME/osinergmin $SIF \
  python3 \$HOME/$FIG/fusionar_figuras.py \
    --figuras \$HOME/$FIG/out/figuras.jsonl \
    --vault   \$HOME/osinergmin/vault \
    --salida  \$HOME/$FIG/out \
    ${APLICAR}" || exit 1

[[ "$APLICAR" != "--aplicar" ]] && { echo; echo "Simulacro. Con --aplicar se ejecuta."; exit 0; }

echo
echo "══ 2. Traer el vault enriquecido al Mac ══"
/usr/bin/rsync -az --info=stats2 khipu:osinergmin/vault/ "$VAULT/" | tail -4 || exit 1

echo
echo "══ 3. Traer la lista de documentos a reindexar ══"
rsync -q khipu:"$FIG/out/docs_a_reindexar.txt" "$LOCAL/docs_a_reindexar.txt" || exit 1
N=$(wc -l < "$LOCAL/docs_a_reindexar.txt" | tr -d ' ')
echo "documentos a reindexar: $N"
[[ "$N" -eq 0 ]] && { echo "nada que reindexar"; exit 0; }

echo
echo "══ 4. Reindexar solo esos documentos ══"
cd "$REPO" || exit 1
/usr/local/bin/docker compose run --rm \
  -v "$LOCAL/docs_a_reindexar.txt:/data/lista.txt:ro" \
  ingest sein-rag-ingest --reindexar-lista /data/lista.txt 2>&1 | tail -3

echo
echo "══ 5. Verificar las invariantes del vault ══"
"$REPO/backend/.venv/bin/python" "$REPO/tools/figuras/verificar_enriquecimiento.py"
