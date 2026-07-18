#!/usr/bin/env bash
set -euo pipefail

mkdir -p logs

if pgrep -f "devin/ui/fast_app.py" >/dev/null 2>&1; then
  echo "backend already running"
  exit 0
fi

# DEVIN_DESKTOP_CLOSE_STOPS_BACKEND=1: questo backend esiste per servire la
# GUI desktop -> quando la finestra si chiude (e non ci sono run attivi) il
# backend si spegne da solo (vedi /api/desktop/close_cleanup). I backend
# avviati a mano (sviluppo/pytest) NON esportano la variabile e restano su.
nohup env DEVIN_DESKTOP_CLOSE_STOPS_BACKEND=1 venv/bin/python devin/ui/fast_app.py >> logs/fast_app_headless.log 2>&1 < /dev/null &
echo $! > logs/fast_app_headless.pid
echo "backend started pid=$(cat logs/fast_app_headless.pid)"
