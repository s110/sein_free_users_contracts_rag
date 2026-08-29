#!/bin/zsh
# Pasada de cierre, una vez terminada la extracción.
#
# Rehace la fusión desde el vault virgen en vez de acumular oleadas: los
# bloques insertados antes de que existiera el cierre `*(fin de la lectura
# automática…)*` no lo llevan, y sin él el chunker no sabe dónde acaba un
# bloque para ponerle la cabecera a sus fragmentos. La fusión es determinista a
# partir de figuras.jsonl, así que rehacerla desde cero da el resultado
# correcto y consistente.
#
#   ./cierre.sh            simulacro
#   ./cierre.sh --aplicar  lo hace
set -u

APLICAR=${1:-}
FIG=osinergmin/figuras
LOCAL=/Volumes/Datos/osinergmin_data/charts
REPO=/Volumes/Datos/proyectos_personales/sein_free_users_contracts_rag
VAULT="/Users/sebastianlopez/Library/Application Support/osinergmin/vault"

echo "══ 0. Estado de la extracción ══"
ssh khipu 'f=$HOME/osinergmin/figuras/out/figuras.jsonl
  echo "leídas: $(cat $f | wc -l) de $(cat $HOME/osinergmin/figuras/worklist.jsonl | wc -l)"
  echo "errores: $(grep -ac "\"error\"" $f; true)"
  echo "jobs en cola: $(squeue -u $USER -h | wc -l)"'

if [[ "$APLICAR" != "--aplicar" ]]; then
  echo; echo "Simulacro. Con --aplicar se ejecuta el cierre."; exit 0
fi

echo
echo "══ 1. Restaurar el vault virgen en Khipu ══"
ssh khipu 'set -e
  B=$(ls -t $HOME/osinergmin/figuras/out/vault_backup_*.tar.gz | tail -1)
  echo "restaurando desde $(basename $B)"
  tar -xzf "$B" -C $HOME/osinergmin/vault
  echo "documentos con sello tras restaurar: $(grep -l "^figuras_leidas:" $HOME/osinergmin/vault/*.md 2>/dev/null | wc -l)"' || exit 1

echo
echo "══ 2. Fusión completa ══"
ssh khipu "apptainer exec --bind \$HOME/osinergmin:\$HOME/osinergmin \$HOME/containers/vllm.sif \
  python3 \$HOME/$FIG/fusionar_figuras.py \
    --figuras \$HOME/$FIG/out/figuras.jsonl \
    --vault   \$HOME/osinergmin/vault \
    --salida  \$HOME/$FIG/out \
    --aplicar" || exit 1

echo
echo "══ 3. Traer el vault al Mac ══"
SALIDA=$(/usr/bin/rsync -az --stats khipu:osinergmin/vault/ "$VAULT/" 2>&1)
RC=$?
[[ $RC -ne 0 ]] && { echo "rsync falló (rc=$RC):"; echo "$SALIDA" | tail -5; exit 1; }
echo "$SALIDA" | grep -E "files transferred|total size" | head -2

echo
echo "══ 4. Reconstruir la imagen del backend (cambió el chunker) ══"
cd "$REPO" || exit 1
/usr/local/bin/docker compose build backend ingest 2>&1 | tail -2
/usr/local/bin/docker compose up -d backend 2>&1 | tail -1

echo
echo "══ 5. Reindexar todo documento con sello ══"
: > "$LOCAL/docs_a_reindexar.txt"
for f in "$VAULT"/*.md; do
  grep -q '^figuras_leidas:' "$f" && basename "$f" .md >> "$LOCAL/docs_a_reindexar.txt"
done
N=$(wc -l < "$LOCAL/docs_a_reindexar.txt" | tr -d ' ')
echo "documentos con figura: $N"
if [[ "$N" -gt 0 ]]; then
  /usr/local/bin/docker compose run --rm \
    -v "$LOCAL/docs_a_reindexar.txt:/data/lista.txt:ro" \
    ingest sein-rag-ingest --reindexar-lista /data/lista.txt 2>&1 | tail -2
fi

echo
echo "══ 6. Verificar ══"
"$REPO/backend/.venv/bin/python" "$REPO/tools/figuras/verificar_enriquecimiento.py"
