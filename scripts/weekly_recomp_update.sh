#!/bin/bash
# Mise à jour tous les 3 jours de Recomp Check + page Mises à jour : régénère recomp/data et pousse sur GitHub Pages.
# Ne committe QUE recomp/data (les autres modifs locales en cours ne sont pas touchées).
set -euo pipefail
cd /Users/thomaslefevre/.t3/projects/ayn-thor
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

# launchd déclenche chaque jour à 9h (+ au réveil/démarrage) : on ne travaille que si la dernière
# mise à jour réussie date d'au moins 3 jours (marge de 2 h pour l'heure exacte de déclenchement).
STAMP="$HOME/.local/state/recompcheck/last_success"
INTERVAL=$((3*24*3600 - 2*3600))
mkdir -p "$(dirname "$STAMP")"
if [ "${1:-}" != "--force" ] && [ -f "$STAMP" ]; then
  age=$(( $(date +%s) - $(cat "$STAMP") ))
  if [ "$age" -lt "$INTERVAL" ]; then exit 0; fi
fi
echo "=== $(date '+%F %T') ==="
trap 'echo "✗ Échec ($(date "+%F %T"))"' ERR

/usr/bin/python3 scripts/build_recomp_data.py
/usr/bin/python3 scripts/build_updates_data.py || echo "⚠ build_updates_data.py a échoué"

# generatedAt / seenAt changent à chaque build : sert seulement à choisir le message de commit
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
# On publie à chaque passage pour que la date « Mis à jour le … » affichée dans les apps reste juste.
if same recomp/data/recomp.json && same recomp/data/updates.json; then
  MSG="chore(recomp): vérification auto Recomp Check — aucun changement ($(date +%d/%m/%Y))"
else
  MSG="chore(recomp): mise à jour auto (3 jours) Recomp Check + Mises à jour ($(date +%d/%m/%Y))"
fi

git commit -q -m "$MSG" -- recomp/data
git pull --rebase --autostash -q origin main
git push -q origin main
date +%s > "$STAMP"
echo "Publié : https://mrthomaslef.github.io/ayn-thor/recomp/"
