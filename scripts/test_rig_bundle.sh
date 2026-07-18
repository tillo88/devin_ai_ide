#!/usr/bin/env bash
# test_rig_bundle.sh — PROVA che il set snello (requirements-rig.txt) non rompe il
# backend prima di impacchettarlo con PyInstaller (FASE 1 della roadmap installer).
#
# Cosa fa, in un venv PULITO e usa-e-getta (non tocca il tuo ambiente):
#   1. crea il venv e installa SOLO requirements-rig.txt (niente flask/tk/playwright/crawl4ai)
#   2. importa l'entry FastAPI (devin.ui.fast_app): se qualcosa importasse "duro" uno
#      dei 4 pacchetti tolti, qui esplode -> lo scopriamo ORA, non dopo il build
#   3. fa partire il server per ~6s e verifica che /app risponda (boot reale)
#   4. gira la suite pytest per confermare che i fallback (TF-IDF, requests) tengono
#
# Uso:   bash scripts/test_rig_bundle.sh
# Verde = si puo' procedere con PyInstaller. Rosso = mi incolli l'output e sistemo.
set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
VENV="$REPO/.venv-rig-test"
PORT=5057
FAIL=0

echo "== 1/4  venv pulito in $VENV =="
rm -rf "$VENV"
python3 -m venv "$VENV"
# shellcheck disable=SC1091
source "$VENV/bin/activate"
pip install --quiet --upgrade pip
echo "== installo requirements-rig.txt (set snello) =="
pip install --quiet -r requirements-rig.txt pytest || { echo "!! pip install fallito"; FAIL=1; }

echo
echo "== 2/4  import dell'entry FastAPI con le sole dipendenze snelle =="
python -c "import devin.ui.fast_app; print('OK import devin.ui.fast_app')" \
  || { echo "!! import FALLITO: qualcosa importa 'duro' un pacchetto tolto"; FAIL=1; }

echo
echo "== 3/4  boot reale del backend (~6s) e check /app =="
( uvicorn devin.ui.fast_app:app --host 127.0.0.1 --port "$PORT" >/tmp/rig_boot.log 2>&1 & echo $! > /tmp/rig_boot.pid )
sleep 6
if curl -fsS "http://127.0.0.1:$PORT/app" -o /dev/null 2>/dev/null; then
  echo "OK /app risponde su :$PORT"
else
  echo "!! /app NON risponde — log:"; tail -n 20 /tmp/rig_boot.log; FAIL=1
fi
kill "$(cat /tmp/rig_boot.pid 2>/dev/null)" 2>/dev/null || true

echo
echo "== 4/4  suite pytest (i fallback devono reggere senza i 4 pacchetti) =="
python -m pytest -q 2>&1 | tail -n 25 || FAIL=1

deactivate || true
echo
if [ "$FAIL" -eq 0 ]; then
  echo "########  VERDE: il set snello non rompe nulla -> via libera a PyInstaller."
else
  echo "########  ROSSO: incolla l'output qui sopra, sistemo prima di impacchettare."
fi
rm -rf "$VENV"
exit "$FAIL"
