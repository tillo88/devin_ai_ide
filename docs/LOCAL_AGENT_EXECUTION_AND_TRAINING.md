# Agente locale Windows: lettura, esecuzione, modifiche e training

**Aggiornato:** 2026-10-21
**Stato:** contratto operativo dell'agente per workspace `mode=direct`.

## Obiettivo e confine

DEVIN lavora su una cartella del PC scelta con Explorer, senza caricarla sul
rig. Il cervello (il modello) gira sul rig; le mani sono l'app desktop, che
esegue sul PC gli strumenti che DEVIN chiede e gli rimanda il risultato. Il
path assoluto resta nel registro locale protetto di Tauri: backend e modello
vedono un `bridge_id` e path relativi.

Fino al 21/10 l'agente era *one-shot*: l'app sceglieva da sola al massimo 18
estratti da 80 righe e il modello dava una sola risposta senza strumenti. Su un
progetto vero (spyengine) DEVIN "non poteva analizzare tutto". Il multi-step
era stato disabilitato il 10/10 perche' le generazioni in sequenza erano
sospettate di coincidere con i riavvii duri del rig; quei riavvii sono poi
risultati hardware (alimentazione, PCIe Gen2, wake da PCIe, 18-19/10) e la chat
esegue gia' da allora decine di generazioni in sequenza senza riavvii.

## Il ciclo a passaggi

`POST /api/local-workspace/agent-step` (SSE) esegue un giro del modello:

1. primo giro: `task` (gia' salvato nella chat da `/api/chat`), piu' gli ultimi
   10 messaggi della chat come contesto;
2. se il modello chiede strumenti arriva l'evento `strumenti` con
   `session_id` e chiamate; l'app le esegue sul PC e richiama l'endpoint con
   `session_id` e `results` (`[{id, content}]`);
3. quando il modello risponde senza strumenti la risposta va nello storico della
   chat (`salvato`), arriva `done` e la sessione si chiude.

Pensiero, testo e lettura del contesto arrivano con gli stessi eventi della
chat, nella stessa bolla; ogni strumento compare come riga nel riquadro dei
passaggi ("📖 Leggo …", "▶ Eseguo …"). Le sessioni vivono nel backend
(`devin/core/agente_locale.py`), scadono dopo 2 ore e sono legate al progetto.

Chiamate con nome sconosciuto, argomenti non JSON o piano di modifiche non
valido non vanno al PC: il modello riceve subito l'errore spiegato. Un risultato
che l'app non restituisce diventa un `ERRORE` esplicito, mai un buco.

**Nessun tetto fisso di passaggi.** Il sorvegliante delle ripetizioni
(`devin/core/sorvegliante.py`, lo stesso della chat e dell'agente dei
progetti) avvisa e poi ferma solo chi gira in tondo: stessa chiamata con lo
stesso risultato, stesso errore per 3 giri. Resta un tetto di sicurezza di 60
passaggi, dichiarato al modello e all'utente.

`/api/local-workspace/agent-once` resta per i client desktop non ancora
aggiornati.

## Strumenti

| strumento | sul PC | approvazione |
|---|---|---|
| `elenca` | `local_workspace_tree` (fino a 1.500 file, profondita' 12), filtrato per cartella; 400 righe per risposta | no |
| `leggi` | `local_workspace_read` (fino a 256 KiB); l'app restituisce le righe richieste, numerate, max 800 righe / 40.000 caratteri, con SHA-256 | no |
| `cerca` | `local_workspace_search`: testo senza distinzione di maiuscole nei file di testo, max 80 risultati `path:riga: testo` | no |
| `esegui` | `run_local_workspace_command`, policy sotto | no |
| `proponi_modifiche` | `apply_local_workspace_plan` | **si'**, a ogni piano |

Lettura ed esecuzione sono libere per decisione dell'operatore (21/10): DEVIN
deve poter controllare e testare i programmi da solo. Le modifiche restano col
piano approvato; la scrittura autonoma con punto di ripristino e' il passo
successivo.

## Esecuzione locale

`run_local_workspace_command` non espone una shell. Accetta un programma da una
allowlist di tool di sviluppo (python/py, pytest, node, npm test/run, npx
`--no-install`, cargo test/check/build/clippy/fmt/metadata, git in sola lettura,
go, dotnet, java, mvn, gradle, ruff, mypy) e argomenti strutturati;
PowerShell, `cmd`, codice inline (`python -c`, `node -e`), installazioni e Git
mutante sono rifiutati. Rust:

- ricalcola l'origine Tauri e la registrazione del workspace;
- limita `cwd` e path degli argomenti alla radice autorizzata;
- azzera l'ambiente ereditato e ricostruisce una allowlist minima;
- ammette come programma anche il Python (o pytest) di un ambiente virtuale
  del progetto: `Scripts`/`bin` accanto a un `pyvenv.cfg`, dentro la cartella
  autorizzata, con le stesse regole sugli argomenti (21/10);
- disabilita stdin e limita il timeout a 1–900 secondi (default 180);
- inserisce il processo in un Windows Job con terminazione dell'intero albero;
- conserva al massimo 96 KiB per stream, con testa/coda e SHA-256 dell'output
  completo; al modello arrivano al massimo 12.000 caratteri di stdout e 8.000
  di stderr.

Il pulsante **Stop** chiama `cancel_local_workspace_command` e interrompe anche
il ciclo. Questa e' una policy applicativa, non una sandbox del sistema
operativo: un comando gira con i permessi dell'utente Windows dentro la
cartella del progetto.

## Scritture

`proponi_modifiche` propone una operazione per chiamata, con `path`, `operation`, `before`, `content` come parametri di testo di primo livello: annidati in una lista JSON il testo multi-riga del modello non arrivava (21/10, sei piani di fila "senza content"). Il backend accetta anche la vecchia forma con `operations` (max 20 operazioni, 2 MiB di payload). Ogni
file esistente richiede lo SHA-256 restituito da `leggi`; se il contenuto e'
cambiato il piano fallisce. Per file nuovi o piccoli `write`; per un file
grande `replace` con un anchor `before` esatto e univoco. Tauri verifica
l'anchor, applica atomicamente e conserva il recovery sul PC. L'esito
(applicato o rifiutato) torna al modello, che puo' verificare con `esegui`.

## Traccia di training anti-contaminazione

Ogni episodio concluso crea un caso `agent_episode` e un attempt
`pending_review`. Il rig riceve task, risposta finale, la sequenza bounded
degli strumenti usati, programma/argomenti/esito/tempi/digest delle esecuzioni.
Non riceve path Windows assoluti, recovery, stdout o stderr grezzi nella
traccia. Nessun episodio e' promosso automaticamente; **Utile** e **Da
correggere** registrano la review umana come prima.

## Verifica minima

```powershell
py -3 -m pytest -q test_agente_locale.py test_understory_hybrid.py test_sorvegliante.py
cargo test --manifest-path src-tauri/Cargo.toml
node test_agente_locale.mjs
node --check devin/ui/static/js/codex_app.js
node scripts/test-workspace-browser.mjs
```

I bench sono mutation-tested: righe e timeout senza tetto, cache dell'albero,
cartella di ricerca ignorata, modifiche applicate senza approvazione, errore
del desktop che interrompe il ciclo, fine stream silenziosa, sessione persa o
di un altro progetto, nomi e piani non validati, risposta non salvata, compito
duplicato, avviso e stop del sorvegliante devono diventare rossi se alterati.
