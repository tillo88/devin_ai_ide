# SearXNG sul rig — shared per tutti e 3 i ruoli (privacy-first)

Metasearch self-hosted: le query di DEVIN restano sulla LAN, niente cloud terzo,
niente API key. Serve a **tutti e tre i ruoli** (devin/hermes/teacher), quindi la
config vive **una volta sola sul disco shared** (`/mnt/ai-rig-shared/searxng`) e su
ogni ruolo si abilita solo un servizio systemd. Nessuna copia, nessun secret
duplicato. Il rig e' triple-boot: gira un ruolo alla volta, quindi un solo
container SearXNG attivo per volta, sulla stessa config condivisa.

DEVIN lo usa con `web_search.provider=searxng` e
`searxng_url=http://192.168.1.100:8081`.

## Passo 1 — popola il disco shared (UNA VOLTA SOLA, dal ruolo devin)

Il repo `~/devin_ai_ide` sta SOLO sul disco devin; lo shared invece e' montato su
tutti i ruoli. Quindi copiamo compose + config + **lo script** sullo shared, cosi'
su hermes/teacher non serve il repo:

```bash
sudo mkdir -p /mnt/ai-rig-shared/searxng
sudo cp -r ~/devin_ai_ide/scripts/rig/searxng/{docker-compose.yml,config,install_searxng_service.sh} /mnt/ai-rig-shared/searxng/
cd /mnt/ai-rig-shared/searxng
sudo cp config/settings.yml.example config/settings.yml
# metti un secret in config/settings.yml (server.secret_key):
openssl rand -hex 32
```

## Passo 2 — abilita il servizio su OGNI ruolo (devin, hermes, teacher)

systemd e' per-OS (sul disco del ruolo), quindi va abilitato una volta per ruolo.
Ma si lancia SEMPRE dallo **shared** (uguale su tutti, niente repo necessario):

```bash
bash /mnt/ai-rig-shared/searxng/install_searxng_service.sh
```

Lo script controlla la config shared, sceglie `docker compose`/`docker-compose`,
installa `ai-rig-searxng.service` (avvio al boot, `WorkingDirectory` = shared),
lo avvia e verifica il JSON con retry. Ripeti dopo aver bootato negli altri due
ruoli.

> **"Non si puo' fare tutto da devin?"** — In teoria si': i dischi sono fisicamente
> qui, si potrebbero montare le root di hermes/teacher da devin e piazzare l'unit
> a mano nel loro `/etc/systemd/system/multi-user.target.wants/`. Ma abilitare un
> servizio su un OS non-bootato e' fragile (systemctl vuole l'OS vivo; resta il
> symlink manuale). La via pulita e semplice e' il comando sopra, una volta per
> ruolo, dallo shared. Provisioning cross-ruolo da devin = possibile miglioramento
> futuro, non ora.

## Passo 3 — verifica

```bash
curl "http://127.0.0.1:8081/search?q=test&format=json" | head -c 300   # JSON, non 403
# oppure dal PC, senza toccare la config dell'app:
python scripts/test_internet.py --provider searxng --url http://192.168.1.100:8081
```

## Passo 4 — fai usare SearXNG a DEVIN (consigliato: primario + fallback)

Nel `config/settings.json` (per-macchina) del backend DEVIN — SearXNG primario,
TinyFish di riserva SE SearXNG e' giu':

```json
"web_search": { "provider": "searxng", "fallback": "tinyfish", "searxng_url": "http://192.168.1.100:8081" }
```

Il fallback scatta SOLO sull'errore del primario (container giu'), non sui
risultati vuoti: una query andata a vuoto su SearXNG NON viene rimandata in
silenzio al cloud (privacy). TinyFish serve la sua chiave in `devin/ui/.env`;
se manca, il fallback e' semplicemente saltato e resta solo SearXNG.

## I due trucchi (gia' risolti nel template `settings.yml.example`)

1. **JSON non abilitato** — SearXNG disabilita `format=json` di default -> 403.
   Risolto in `search.formats: [html, json]`.
2. **Limiter/bot-detection** — le richieste programmatiche non-browser -> 403.
   Risolto con `server.limiter: false` (servizio interno LAN).

## Tetto risorse (IMPORTANTE)

Il compose limita SearXNG a **2 core e 1 GB** (`cpus: "2.0"`, `mem_limit: "1g"`).
Serve perche' in passato il container ha generato 250+ worker saturando tutti i
core del rig (nessun errore nei log: il box si strozza, non crasha). Il tetto e'
a livello container: qualunque cosa faccia il processo, non si prende il rig.
Controlla il consumo reale:
```bash
sudo docker stats --no-stream searxng
sudo docker exec searxng sh -c 'ps aux | wc -l'   # quanti processi girano
```

## Manutenzione
- Aggiornare l'immagine (una volta, si riflette su tutti i ruoli):
  `cd /mnt/ai-rig-shared/searxng && docker compose pull && docker compose up -d`
- Log: `docker compose logs -f searxng` · Stato: `systemctl status ai-rig-searxng`
- Il container's data e la config stanno sullo shared: una sola fonte di verita'.
