# SearXNG sul rig (per DEVIN, privacy-first)

Metasearch self-hosted: le query di DEVIN restano sulla LAN, niente cloud terzo,
niente API key. Gira in Docker sul rig (ruolo devin), sempre attivo col rig.
DEVIN lo usa con `web_search.provider=searxng` e
`searxng_url=http://192.168.1.100:8081`.

## Setup (sul rig, in questa cartella)

```bash
# 1. config per-macchina dal template
cp config/settings.yml.example config/settings.yml

# 2. genera un secret e mettilo in config/settings.yml (server.secret_key)
openssl rand -hex 32

# 3. avvia (restart unless-stopped -> riparte da solo al boot in ruolo devin)
docker compose up -d

# 4. verifica il JSON: DEVE tornare JSON, non 403
curl "http://127.0.0.1:8081/search?q=test&format=json" | head -c 300
```

## Usare SearXNG in DEVIN

Nel `config/settings.json` (per-macchina) del backend che vuoi:

```json
"web_search": { "provider": "searxng", "searxng_url": "http://192.168.1.100:8081" }
```

Prova SENZA toccare la config dell'app (dal PC o dal rig):

```
python scripts/test_internet.py --provider searxng --url http://192.168.1.100:8081
```

Se lo stadio 1 (SEARCH) torna risultati, SearXNG e' a posto.

## I due trucchi (gia' risolti nel template)

1. **JSON non abilitato** — SearXNG disabilita `format=json` di default -> 403.
   Il template lo abilita in `search.formats`.
2. **Limiter/bot-detection** — le richieste programmatiche non-browser vengono
   bloccate 403. Il template mette `server.limiter: false` (servizio interno LAN).

## Manutenzione

- Aggiornare l'immagine: `docker compose pull && docker compose up -d`
- Log: `docker compose logs -f searxng`
- Nota rig: gira sul disco/ruolo **devin** (come il backend DEVIN e i container
  understory/automem) — up quando serve, cioe' quando il rig e' in ruolo devin.
