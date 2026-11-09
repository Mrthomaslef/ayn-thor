#!/bin/bash
# Mise à jour hebdomadaire de Recomp Check : régénère recomp/data et pousse sur GitHub Pages.
# Ne committe QUE recomp/data (les autres modifs locales en cours ne sont pas touchées).
set -euo pipefail
cd /Users/thomaslefevre/.t3/projects/ayn-thor
echo "=== $(date '+%F %T') ==="

/usr/bin/python3 scripts/build_recomp_data.py

# generatedAt change à chaque build : on ne publie que si la liste des jeux a vraiment changé
if git show HEAD:recomp/data/recomp.json 2>/dev/null | /usr/bin/python3 -c '
import json, sys
old = json.load(sys.stdin); new = json.load(open("recomp/data/recomp.json"))
old.pop("generatedAt", None); new.pop("generatedAt", None)
sys.exit(0 if old == new else 1)'; then
  echo "Aucun changement de compatibilité — rien à publier."
  git checkout -- recomp/data
  exit 0
fi

git commit -m "chore(recomp): mise à jour hebdomadaire Recomp Check ($(date +%d/%m/%Y))" -- recomp/data
git pull --rebase --autostash -q origin main
git push -q origin main
echo "Publié : https://mrthomaslef.github.io/ayn-thor/recomp/"
