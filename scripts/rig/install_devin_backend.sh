#!/usr/bin/env bash
# install_devin_backend.sh - Deploy del backend DEVIN sul rig (Ubuntu 24.04).
#
# Visione finale (2026-07-21): il backend principale gira SUL rig, sempre
# attivo al boot col ruolo DEVIN. Server + web app online quando il rig e'
# online; il PC principale usa solo il frontend desktop.
#
# Uso (sul rig, dalla root del repo clonato, es. /home/tillo/devin_ai_ide):
#   bash scripts/rig/install_devin_backend.sh
#
# Cosa fa (idempotente):
#   1. crea/riusa il venv in .venv-rig e installa i requirements core
#   2. imposta models.rig_self_hosted=true in config/settings.json
#      (il modello e' su localhost:8080, niente WOL, la GUI remota non
#      spegne il backend alla chiusura)
#   3. installa e abilita l'unit systemd devin-backend (avvio al boot)
#
# Richiede sudo solo per il passo systemd.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"
echo ">> Repo: $REPO_ROOT"

# --- 1. venv + requirements core -------------------------------------------
if [ ! -x .venv-rig/bin/python ]; then
    echo ">> Creo venv .venv-rig"
    python3 -m venv .venv-rig
fi
./.venv-rig/bin/pip install -q -U pip wheel
echo ">> Installo requirements core (playwright/crawl4ai opzionali, dopo)"
./.venv-rig/bin/pip install -q \
    "openai>=1.0.0" requests numpy scikit-learn \
    fastapi uvicorn python-multipart \
    pypdf python-docx openpyxl python-pptx \
    python-dotenv instructor \
    tree-sitter "tree-sitter-language-pack==0.13.0" \
    bandit youtube-transcript-api

# --- 2. config: rig_self_hosted=true + bind LAN -----------------------------
echo ">> Imposto models.rig_self_hosted=true e ui.host=0.0.0.0 in config/settings.json"
./.venv-rig/bin/python - <<'PYEOF'
import json
from pathlib import Path

path = Path("config/settings.json")
config = json.loads(path.read_text(encoding="utf-8"))
changed = False

models = config.setdefault("models", {})
if models.get("rig_self_hosted") is not True:
    models["rig_self_hosted"] = True
    changed = True
    print("   rig_self_hosted: true (aggiornato)")
else:
    print("   rig_self_hosted: gia' true")

# Il backend sul rig deve essere raggiungibile dal PC (desktop app) sulla LAN,
# non solo da localhost: bind 0.0.0.0. NB: LAN aperta senza token; per limitare
# l'accesso impostare DEVIN_API_TOKEN (vedi devin/ui/token_gate.py).
ui = config.setdefault("ui", {})
if ui.get("host") != "0.0.0.0":
    ui["host"] = "0.0.0.0"
    changed = True
    print("   ui.host: 0.0.0.0 (backend esposto sulla LAN per il PC)")
else:
    print("   ui.host: gia' 0.0.0.0")

if changed:
    path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
PYEOF

# --- 3. unit systemd ---------------------------------------------------------
SERVICE_USER="${SUDO_USER:-$USER}"
UNIT_PATH=/etc/systemd/system/devin-backend.service
echo ">> Installo unit systemd ($UNIT_PATH, user=$SERVICE_USER)"
sudo tee "$UNIT_PATH" > /dev/null <<UNITEOF
[Unit]
Description=DEVIN AI IDE backend (fast_app)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$REPO_ROOT
Environment=DEVIN_NO_BROWSER=1
ExecStart=$REPO_ROOT/.venv-rig/bin/python devin/ui/fast_app.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
UNITEOF

sudo systemctl daemon-reload
sudo systemctl enable --now devin-backend.service
sleep 3
echo ">> Stato:"
sudo systemctl status devin-backend.service --no-pager -l | head -12 || true
echo ""
echo ">> Verifica: curl -s http://127.0.0.1:5000/api/health"
curl -s --max-time 5 http://127.0.0.1:5000/api/health || echo "(non ancora pronto: riprova tra qualche secondo)"
echo ""
echo "Fatto. Il backend parte a ogni boot. Log: journalctl -u devin-backend -f"
