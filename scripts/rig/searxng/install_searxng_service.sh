#!/usr/bin/env bash
# install_searxng_service.sh - abilita SearXNG (shared) sul RUOLO corrente del rig.
#
# Modello: la config di SearXNG vive UNA VOLTA SOLA sul disco shared
# (/mnt/ai-rig-shared/searxng), condivisa da devin/hermes/teacher. Qui si
# installa solo l'unit systemd, da rieseguire una volta su ciascun ruolo
# (systemd e' per-OS, sul disco del ruolo). Nessuna copia della config per ruolo.
#
# Uso (sul ruolo, dopo aver popolato il disco shared — vedi README):
#   bash install_searxng_service.sh [SHARED_DIR]
# default SHARED_DIR = /mnt/ai-rig-shared/searxng
set -euo pipefail

SHARED_DIR="${1:-/mnt/ai-rig-shared/searxng}"
UNIT=/etc/systemd/system/ai-rig-searxng.service

echo ">> SearXNG shared dir: $SHARED_DIR"

# --- 1. prerequisiti: la config shared deve gia' esserci ---------------------
if [ ! -f "$SHARED_DIR/docker-compose.yml" ]; then
    echo "ERRORE: $SHARED_DIR/docker-compose.yml assente." >&2
    echo "Popola il disco shared UNA VOLTA (da un ruolo qualsiasi):" >&2
    echo "  mkdir -p $SHARED_DIR" >&2
    echo "  cp -r ~/devin_ai_ide/scripts/rig/searxng/{docker-compose.yml,config} $SHARED_DIR/" >&2
    echo "  cp $SHARED_DIR/config/settings.yml.example $SHARED_DIR/config/settings.yml" >&2
    echo "  # metti un secret in config/settings.yml (server.secret_key): openssl rand -hex 32" >&2
    exit 1
fi
if [ ! -f "$SHARED_DIR/config/settings.yml" ]; then
    echo "ERRORE: manca $SHARED_DIR/config/settings.yml." >&2
    echo "  cp $SHARED_DIR/config/settings.yml.example $SHARED_DIR/config/settings.yml" >&2
    echo "  # poi metti il secret: openssl rand -hex 32" >&2
    exit 1
fi
if grep -q "CAMBIAMI" "$SHARED_DIR/config/settings.yml"; then
    echo "ERRORE: secret non impostato in $SHARED_DIR/config/settings.yml (server.secret_key)." >&2
    echo "  openssl rand -hex 32   # poi incollalo al posto di CAMBIAMI_..." >&2
    exit 1
fi

# 'docker compose' (v2) o 'docker-compose' (v1)?
if docker compose version >/dev/null 2>&1; then
    COMPOSE="/usr/bin/docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE="$(command -v docker-compose)"
else
    echo "ERRORE: ne' 'docker compose' ne' 'docker-compose' disponibili." >&2
    exit 1
fi
echo ">> compose: $COMPOSE"

# --- 2. unit systemd (idempotente) -------------------------------------------
echo ">> Installo unit $UNIT"
sudo tee "$UNIT" > /dev/null <<UNITEOF
[Unit]
Description=SearXNG for DEVIN (shared, privacy-first web search)
After=docker.service network-online.target
Requires=docker.service
Wants=network-online.target
RequiresMountsFor=/mnt/ai-rig-shared

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=$SHARED_DIR
ExecStart=$COMPOSE up -d
ExecStop=$COMPOSE down
TimeoutStartSec=180

[Install]
WantedBy=multi-user.target
UNITEOF

sudo systemctl daemon-reload
sudo systemctl enable --now ai-rig-searxng.service
sleep 4
echo ">> Stato:"
sudo systemctl is-active ai-rig-searxng.service || true
echo ""
echo ">> Verifica JSON (deve tornare JSON, non 403):"
curl -s --max-time 8 "http://127.0.0.1:8081/search?q=test&format=json" | head -c 200 || echo "(non ancora pronto: riprova tra qualche secondo)"
echo ""
echo "Fatto su questo ruolo. Ripeti su hermes e teacher (stesso comando)."
