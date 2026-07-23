# SearXNG sul rig — shared per tutti e 3 i ruoli (privacy-first)

Metasearch self-hosted: le query di DEVIN restano sulla LAN, niente cloud terzo,
niente API key. Serve a **tutti e tre i ruoli** (devin/hermes/teacher), quindi la
config vive **una volta sola sul disco shared** (`/mnt/ai-rig-shared/searxng`) e su
ogni ruolo si abilita solo un servizio systemd. Nessuna copia, nessun secret
duplicato. Il rig e' triple-boot: gira un ruolo alla volta, quindi un solo
container SearXNG attivo per volta, sulla stessa config condivisa.

DEVIN lo usa con `web_search.provider=searxng` e
`searxng_url=http://192.168.1.100:8081`.

## Passo 1 — popola il disco shared (UNA VOLTA SOLA, da un ruolo qualsiasi)

```bash
sudo mkdir -p /mnt/ai-rig-shared/searxng
sudo cp -r ~/devin_ai_ide/scripts/rig/searxng/{docker-compose.yml,config} /mnt/ai-rig-shared/searxng/
cd /mnt/ai-rig-shared/searxng
sudo cp config/settings.yml.example config/settings.yml
# metti un secret in config/settings.yml (server.secret_key):
openssl rand -hex 32
```

## Passo 2 — abilita il servizio su OGNI ruolo (devin, hermes, teacher)

Su ciascun ruolo (systemd e' per-OS), una volta:

```bash
bash ~/devin_ai_ide/scripts/rig/searxng/install_searxng_service.sh
```

Lo script controlla la config shared, sceglie `docker compose`/`docker-compose`,
installa `ai-rig-searxng.service` (avvio al boot, `WorkingDirectory` = shared) e lo
avvia. Poi verifica il JSON. Ripeti dopo aver bootato negli altri due ruoli.

## Passo 3 — verifica

```bash
curl "http://127.0.0.1:8081/search?q=test&format=json" | head -c 300   # JSON, non 403
# oppure dal PC, senza toccare la config dell'app:
python scripts/test_internet.py --provider searxng --url http://192.168.1.100:8081
```

## Passo 4 — fai usare SearXNG a DEVIN

Nel `config/settings.json` (per-macchina) del backend DEVIN:

```json
"web_search": { "provider": "searxng", "searxng_url": "http://192.168.1.100:8081" }
```

## I due trucchi (gia' risolti nel template `settings.yml.example`)

1. **JSON non abilitato** — SearXNG disabilita `format=json` di default -> 403.
   Risolto in `search.formats: [html, json]`.
2. **Limiter/bot-detection** — le richieste programmatiche non-browser -> 403.
   Risolto con `server.limiter: false` (servizio interno LAN).

## Manutenzione
- Aggiornare l'immagine (una volta, si riflette su tutti i ruoli):
  `cd /mnt/ai-rig-shared/searxng && docker compose pull && docker compose up -d`
- Log: `docker compose logs -f searxng` · Stato: `systemctl status ai-rig-searxng`
- Il container's data e la config stanno sullo shared: una sola fonte di verita'.
