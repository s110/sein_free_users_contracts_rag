#!/bin/bash
#SBATCH --job-name=fig-prep
#SBATCH --partition=standard
#SBATCH --qos=a-postgrado
#SBATCH --cpus-per-task=16
#SBATCH --mem=24G
#SBATCH --time=02:00:00
#SBATCH --output=/home/sebastian.lopez/osinergmin/figuras/logs/prep-%j.out
#SBATCH --error=/home/sebastian.lopez/osinergmin/figuras/logs/prep-%j.out

set -uo pipefail
export PATH="$HOME/osinergmin/popplerenv/bin:$PATH"
export FIG_WORKERS=16
export FIG_DPI=200
module load python3/3.11.11 2>/dev/null || true

echo "== inicio $(date -Is) en $(hostname) =="
pdftoppm -v 2>&1 | head -1
python3 -V
python3 "$HOME/osinergmin/figuras/prep_figuras.py"
rc=$?
echo "== fin $(date -Is) rc=$rc =="
du -sh "$HOME/osinergmin/figuras/imgs"
ls "$HOME/osinergmin/figuras/imgs" | wc -l
exit $rc
