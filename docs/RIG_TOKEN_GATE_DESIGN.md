# RIG TOKEN GATE — Design Note (pre-rig-work)

Stato: **DESIGN ONLY** — nessuna riga di codice qui. Decisione owner gia'
registrata ("the right one" anche lato rig, `docs/CONTINUITY_2026-07-18.md`);
questa nota fissa il design PRIMA di toccare il rig, il cui ambiente vive nel
repo separato `ai-rig-iso-build` (WSL `Ubuntu-24.04`, non in questo repo).

## 1. Threat model — perche' serve

Config supportata: `rig_self_hosted=false` — backend in WSL sul PC, rig = solo
server (modelli / AutoMem / web search), tutto il traffico backend→rig via LAN.

Oggi, sul rig (fonti: `rig-roles/devin/scripts/start-llama-devin.sh`,
`config/settings.json`):

- **llama-server gira con `--host 0.0.0.0` e NESSUN `--api-key`**: chiunque
  raggiunga `192.168.1.100:8080` dalla LAN ha un endpoint OpenAI-compatible
  completo (chat completions su un 35B — costo compute, prompt injection nei
  contesti DEVIN, esfiltrazione dei contesti di coding).
- **AutoMem (:8001) e SearXNG (:8081, ruolo hermes) non hanno alcuna auth**:
  lettura/scrittura della memoria condivisa cross-ruolo aperta.
- Understory (:3810) e' raggiungibile dalla workstation SOLO via tunnel SSH —
  e' gia' l'unico servizio di fatto protetto, ed e' il precedente da seguire.

Simmetrico al gate gia' implementato su fast_app (`devin/ui/token_gate.py`):
li' proteggiamo il backend dalla LAN, qui proteggiamo il rig dalla LAN.
Stessa filosofia: UN segreto condiviso, loopback esente, fail-closed.

## 2. Communication map (backend → rig, stato attuale)

| Consumer | Endpoint rig | Protocollo | Config key | Auth oggi |
|---|---|---|---|---|
| `devin/ai/client.py` `AIClient` (`local/stream/complete`, + health `refresh()`) | `http://{rig_host}:{rig_port}/v1/chat/completions` (POST/SSE), `/v1/models` (GET, ping 2s) | OpenAI-compatible HTTP | `models.rig_host` (env override `DEVIN_REMOTE_HOST`), `models.rig_port` | **nessuna** |
| `devin/ai/autocomplete.py`, `devin/ai/stream.py` | come sopra (wrapper su `AIClient`) | come sopra | come sopra | nessuna |
| `devin/ai/automem_client.py` | `GET /recall`, `POST /memory`, `GET /health` su :8001 | REST custom AutoMem | `automem.url` | nessuna |
| `devin/ai/understory_client.py` | `POST /mcp` (MCP Streamable HTTP), `GET /health` su :3810 | MCP/JSON-RPC | `understory.url` (loopback = tunnel SSH verso il rig) | nessuna (ma gia' dietro SSH) |
| `devin/ai/web_search.py` `SearXNGProvider` | `GET /search?q=&format=json` su :8081 | SearXNG JSON | `web_search.searxng_url` | nessuna |
| `AIClient._send_wol()` | magic packet UDP broadcast :9 | layer-2, non HTTP | `models.rig_mac`, `models.wol_port` | n/a |

Nota: NESSUN header `Authorization`/`Bearer`/api-key verso il rig in tutto il
codebase. Gli unici auth header esistenti sono TinyFish (cloud) e OpenAI.

## 3. Design raccomandato — deciso per leg

### 3a. Model server (:8080) — llama.cpp nativo `--api-key` + Bearer da `AIClient`

- **Rig-side**: zero codice nuovo. llama-server supporta gia' `--api-key
  <secret>` (enforce `Authorization: Bearer` su tutti gli endpoint
  OpenAI-compatible). Basta aggiungere il flag in
  `rig-roles/devin/scripts/start-llama-devin.sh` (da `/etc/ai-rig/devin.env`).
- **Backend-side**: `AIClient` e' il single choke point — tutte le chiamate
  modello passano da li' (5 call sites, un file). Aggiungere
  `Authorization: Bearer <token>` a `local()`, `stream()`, `complete()` E al
  health ping `_health_check()` (`/v1/models`).
- **TRAPPOLA WOL/health-check (da evitare)**: oggi `refresh()` legge "rig
  online" = `GET /v1/models` -> 200. Se il gate risponde 401 a un ping senza
  token, un rig RAGGIUNGIBILE-ma-non-autenticato verrebbe letto come OFFLINE
  → WOL spedito a un rig gia' acceso, circuit breaker che si apre, fallback
  silenzioso sui modelli locali deboli. Regola: il health check DEVE mandare
  il token; un 401 col token configurato = "config sbagliata", NON "rig
  spento" (vedi §6).

### 3b. AutoMem / Understory / SearXNG — RACCOMANDAZIONE PRIMARIA: loopback-binding + tunnel SSH (precedente Understory)

**Scelta: niente auth nativa su questi servizi. Si legano a `127.0.0.1` sul
rig; la workstation ci arriva via tunnel SSH, esattamente come Understory fa
OGGI** (`ssh -L 3810:localhost:3810 tillo@192.168.1.100`, vedi nota in
`settings.json` sezione understory).

Rationale:

- **Zero codice nuovo rig-side E backend-side**: Understory funziona gia'
  cosi' in produzione; per AutoMem basta cambiare `automem.url` in
  `http://127.0.0.1:<porta-tunnel>` + un `-L` in piu' sullo stesso tunnel.
- AutoMem e SearXNG **non hanno auth nativa**: l'alternativa "vera" richiede
  codice nel repo `ai-rig-iso-build` che oggi non esiste e andrebbe scritto,
  testato e mantenuto.
- La memoria condivisa resta cross-ruolo sul rig via loopback (gli altri
  ruoli la consumano in locale — esenzione loopback, vedi §5).
- Coerente con la decisione di direzione gia' approvata per l'accesso
  esterno: Tailscale/SSH, niente porte aperte.

**Alternativa (registrata, NON scelta)**: reverse proxy rig-side
(nginx/Caddy) che fa enforce di `Authorization: Bearer` e proxa a
`127.0.0.1:{8001,8081}`. Pro: un solo punto di enforce per N servizi, i
client restano su URL LAN diretti. Contro: nuovo servizio da installare/
configurare/mantenere sul rig, nuovo failure point, e va comunque scritto il
wiring header nei client Python (che col tunnel SSH invece restano identici).

### 3c. SearXNG (:8081)

Ruolo-condizionale (gira solo in ruolo hermes, non in devin). Stessa regola
di 3b se/quando serve: bind loopback + tunnel. **Out of scope per il ruolo
devin** (vedi §8).

## 4. Config surface (backend)

- **Env-first**: `DEVIN_RIG_API_TOKEN` (precedenza), poi
  `settings.json -> models.rig_api_token`. Stesso pattern del gate fast_app
  (`DEVIN_API_TOKEN` > `ui.api_token`) e del precedente TinyFish
  (`tinyfish_api_key_env`: la key arriva da env, in settings c'e' solo il
  NOME della variabile).
- **`settings.json` e' COMMITTED nel repo → MAI committare il segreto.**
  Se si usa la chiave settings, va in un settings locale non tracciato; la
  via preferita e' env var (o `.env` in `devin/ui/`, gia' escluso dai deploy
  e dai commit).
- Token assente/vuoto → comportamento attuale invariato (gate rig
  DISABILITATO lato client: nessun header aggiunto). Fail-open lato client,
  fail-closed lato rig: e' il rig che decide quando iniziare a rifiutare.
- Risoluzione token ad ogni richiesta (come `token_gate.resolve_api_token`):
  cambio env/file senza restart, test monkeypatchabili.

## 5. Esenzione loopback rig-side

Sul rig i servizi si chiamano tra loro via loopback (AutoMem consumato dagli
altri ruoli in locale, dashboard/bot quando self-hosted, Hermes via MCP).
Il gate rig-side DEVE esentare `127.0.0.1`/`::1` — stessa regola del gate
fast_app. Con la raccomandazione 3b questo e' automatico (i servizi non-LLM
non vedono proprio la LAN). Per llama-server, `--api-key` si applica a tutte
le connessioni: il consumo loopback del modello sul rig (se esiste) deve
mandare il token — da verificare nel repo ai-rig-iso-build.

Quando `rig_self_hosted=true` (backend SUL rig): tutti gli URL diventano
loopback (`automem.url -> http://localhost:8001`, modello su localhost:8080)
→ il token non serve, zero cambi config. Il gate conta solo nel path LAN
`rig_self_hosted=false`, che e' quello supportato.

## 6. Fail-soft: 401 ≠ connection-refused

Tutte le chiamate rig oggi degradano in silenzio (chat senza memoria/search,
fallback modelli locali). Con il gate, un token sbagliato/mancante produce
401 — che NON deve essere confuso con rig spento:

- `AIClient`: distinguere 401 da ConnectionError/Timeout nei log
  (`[RIG] 401 unauthorized — token errato/mancante, NON e' un rig offline:
  niente WOL, niente circuit-breaker trip`). I 401 non devono alimentare
  `_circuit_breaker_record_failure` ne' triggerare `_send_wol`.
- AutoMem/Understory/SearXNG: col tunnel SSH (3b) il 401 non esiste; col
  proxy (alternativa) vale la stessa regola di logging.
- Un solo log distintivo per servizio (throttled, stile
  `_log_unreachable` di AutoMem): il fail-soft resta, ma la CAU
