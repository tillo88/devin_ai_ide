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
echo ">> Stato: $(sudo systemctl is-active ai-rig-searxng.service || true)"
# docker richiede root se l'utente non e' nel gruppo 'docker' -> sudo.
echo ">> docker: $(cd "$SHARED_DIR" && sudo $COMPOSE ps --format '{{.Name}} {{.State}}' 2>/dev/null | tr '\n' ' ')"

echo ">> Verifica JSON (SearXNG ci mette qualche secondo a caricare)..."
ok=0
for i in $(seq 1 10); do
    body="$(curl -s --max-time 8 "http://127.0.0.1:8081/search?q=test&format=json" 2>/dev/null || true)"
    if printf '%s' "$body" | grep -q '"results"'; then
        echo "   OK: JSON ricevuto (SearXNG risponde)."
        ok=1
        break
    fi
    if printf '%s' "$body" | grep -qi "429\|Too Many\|Forbidden\|403"; then
        echo "   ATTENZIONE: 403/429 -> manca format json o limiter attivo nel settings.yml." >&2
        break
    fi
    sleep 3
done
[ "$ok" = 1 ] || echo "   Non ancora pronto. Controlla: (cd $SHARED_DIR && sudo $COMPOSE logs --tail 40 searxng)"
echo ""
echo "Fatto su QUESTO ruolo. Per gli altri, dopo aver bootato nel ruolo:"
echo "  bash $SHARED_DIR/install_searxng_service.sh"
