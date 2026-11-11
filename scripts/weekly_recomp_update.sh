#!/bin/bash
# Mise à jour quotidienne (9h) de Recomp Check + page Mises à jour : régénère recomp/data et pousse sur GitHub Pages.
# Ne committe QUE recomp/data (les autres modifs locales en cours ne sont pas touchées).
set -euo pipefail
cd /Users/thomaslefevre/.t3/projects/ayn-thor
echo "=== $(date '+%F %T') ==="

/usr/bin/python3 scripts/build_recomp_data.py
/usr/bin/python3 scripts/build_updates_data.py || echo "⚠ build_updates_data.py a échoué"

# generatedAt / seenAt changent à chaque build : on ne publie que si le contenu a vraiment changé
same() {  # $1 = fichier json ; ignore les horodatages de contrôle
  git show "HEAD:$1" 2>/dev/null | /usr/bin/python3 -c '
import json, sys
def strip(o):
    if isinstance(o, dict): return {k: strip(v) for k, v in o.items() if k not in ("generatedAt", "seenAt")}
    if isinstance(o, list): return [strip(v) for v in o]
    return o
old = json.load(sys.stdin); new = json.load(open(sys.argv[1]))
sys.exit(0 if strip(old) == strip(new) else 1)' "$1"
}
if same recomp/data/recomp.json && same recomp/data/updates.json; then
  echo "Aucun changement (compatibilité ni mises à jour) — rien à publier."
  git checkout -- recomp/data
  exit 0
fi

git commit -m "chore(recomp): mise à jour quotidienne Recomp Check + Mises à jour ($(date +%d/%m/%Y))" -- recomp/data
git pull --rebase --autostash -q origin main
git push -q origin main
echo "Publié : https://mrthomaslef.github.io/ayn-thor/recomp/"
