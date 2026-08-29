#!/bin/zsh
# Aplica al índice lo que la extracción lleva leído hasta ahora.
#
# Se puede correr varias veces conforme avanza la extracción: la fusión es
# idempotente (una página que ya lleva su bloque se salta) y el reindexado
# toca solo los documentos que cambiaron en esta pasada.
#
#   ./aplicar_oleada.sh                  simulacro: enseña qué haría, no toca nada
#   ./aplicar_oleada.sh --aplicar        aplica y reindexa lo que ganó una figura
#   ./aplicar_oleada.sh --aplicar todo   reindexa además los que solo pierden
#                                        marcadores falsos (mucho más lento)
set -u

APLICAR=${1:-}
ALCANCE=${2:-figuras}
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
# macOS trae openrsync (compatible con rsync 2.6.9): no entiende --info=.
# Y nada de tuberías aquí: en zsh el estado de una tubería es el del último
# comando, así que `rsync ... | tail` devolvía 0 aunque rsync fallara y el
# script seguía adelante reindexando un vault que no se habia actualizado.
SALIDA_RSYNC=$(/usr/bin/rsync -az --stats khipu:osinergmin/vault/ "$VAULT/" 2>&1)
RC=$?
if [[ $RC -ne 0 ]]; then
  echo "rsync falló (rc=$RC):"; echo "$SALIDA_RSYNC" | tail -5; exit 1
fi
echo "$SALIDA_RSYNC" | grep -E "files transferred|Number of files transferred|total size" | head -3

echo
echo "══ 3. Traer la lista de documentos a reindexar ══"
# Reindexar cuesta ~8 s de embeddings por documento. Por defecto entran solo
# los que ganaron una figura; los que únicamente perdieron un marcador falso
# mejoran igual, pero pueden esperar a una pasada nocturna.
if [[ "$ALCANCE" == "todo" ]]; then
  REMOTA="docs_a_reindexar.txt"
else
  REMOTA="docs_con_figura.txt"
fi
rsync -q khipu:"$FIG/out/$REMOTA" "$LOCAL/docs_a_reindexar.txt" || exit 1
N=$(wc -l < "$LOCAL/docs_a_reindexar.txt" | tr -d ' ')
echo "alcance: $ALCANCE ($REMOTA)"
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
