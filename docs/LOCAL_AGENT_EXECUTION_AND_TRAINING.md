# Agente locale Windows: esecuzione, review e training

**Aggiornato:** 2026-10-01  
**Stato:** contratto operativo dell'agente per workspace `mode=direct`.

## Obiettivo e confine

DEVIN puo' lavorare su una cartella scelta con Explorer senza caricarla sul
rig. Il path assoluto resta nel registro locale protetto di Tauri; backend e
modello vedono un `bridge_id`, path relativi e soltanto le evidenze richieste.

Il desktop prepara prima dell'inferenza un evidence pack bounded e
deterministico:

- albero fino a 120 righe / 5.000 caratteri, con sorgenti prima degli artefatti;
- retrieval locale correlato al task;
- fino a quattro letture selezionate, con path relativo, SHA-256 e marker di
  troncamento;
- opzionalmente evidenza web reale, soltanto col toggle web attivo.

Il backend invia questo pacchetto al modello una sola volta e accetta soltanto
`done` o `plan`. Per questo endpoint `AIClient` usa `max_attempts=1`: timeout,
connessione interrotta o risposta strumentale terminano il turno senza retry.
La stessa richiesta imposta `response_format=json_object` con uno schema
bounded per `done|plan`; BeeLlama converte lo schema in un vincolo di
generazione. Il parser applicativo resta il secondo gate e rifiuta testo
libero, status strumentali, path non sicuri, fingerprint errati e piani fuori
budget. Non esiste un fallback che trasformi una risposta libera in un falso
`done`.
Il vecchio endpoint `agent-step` fallisce esplicitamente, cosi' un bundle
desktop rimasto aperto non puo' riattivare il loop multi-inferenza.

Questa e' una protezione frontend/backend, non un contratto di processo del
broker: garantisce una richiesta e un tentativo per task, ma non crea da sola
una nuova istanza modello. Il multi-step resta disabilitato finche' il broker
non espone e prova un lifecycle di istanza fresca.

## Esecuzione locale

`run_local_workspace_command` non espone una shell. Accetta un programma da
una allowlist di tool di sviluppo e argomenti strutturati; PowerShell, `cmd`,
interpreti interattivi/inline, installazioni e Git mutante sono rifiutati.
Python viene risolto tramite il launcher Windows `py.exe`; `npx` richiede
`--no-install`.

In modalita' one-shot il modello puo' allegare a un `plan` un solo comando
`verification`. Il comando parte soltanto dopo che le modifiche sono state
approvate e applicate e dopo una seconda conferma esplicita; l'esito viene
mostrato e registrato senza inviare un'altra inferenza al modello. Prima di
partire il cockpit mostra programma, argomenti, directory relativa, timeout e
motivazione. Dopo la conferma, Rust:

- ricalcola l'origine Tauri e la registrazione del workspace;
- limita `cwd` e path degli argomenti alla radice autorizzata;
- azzera l'ambiente ereditato e ricostruisce una allowlist minima;
- disabilita stdin e limita il timeout a 1–900 secondi;
- inserisce il processo in un Windows Job con terminazione dell'intero albero;
- conserva al massimo 96 KiB per stream, mantenendo testa/coda e SHA-256
  dell'output completo;
- produce una ricevuta `devin_local_command_receipt_v1`.

Il pulsante **Stop** chiama `cancel_local_workspace_command`; chiudere il Job
termina anche gli eventuali figli. Questa e' una policy applicativa, non una
sandbox del sistema operativo: un test approvato gira con i permessi
dell'utente Windows e puo' usare rete o file normalmente accessibili. Per
questo l'approvazione e' obbligatoria a ogni comando.

## Scritture

Le modifiche non passano dai comandi. `plan` contiene al massimo 20 operazioni
e 2 MiB complessivi. Ogni file esistente richiede lo SHA-256 osservato durante
la lettura; se il contenuto e' cambiato il piano fallisce. Le sostituzioni sono
atomiche e il recovery rimane sul PC.

## Traccia di training anti-contaminazione

Ogni episodio concluso crea un caso `agent_episode` e un attempt
`pending_review`. Il rig riceve:

- task, risposta finale e sequenza bounded delle etichette tool;
- programma, argomenti relativi, esito, tempi e dimensioni delle esecuzioni;
- digest di comando/stdout/stderr e flag timeout/cancellazione/troncamento.

Non riceve file letti, path Windows assoluti, recovery, stdout o stderr grezzi.
Nessun episodio e' promosso automaticamente.

Nel messaggio conclusivo:

- **Utile** registra una review umana `human_confirmed`;
- **Da correggere** registra `verified_failure` e puo' aggiungere la risposta
  corretta;
- le correzioni validate alimentano l'export SFT esistente;
- Teacher packet e review queue restano il passaggio per le verifiche batch.

Eval e training restano distinti: un test verde e' evidenza dell'esecuzione,
non autorizzazione automatica ad addestrare. Il repository prepara e filtra i
dati; un job LoRA/QLoRA sul rig e' una fase successiva separata, con dataset
versionato, modello base fissato, eval held-out e approvazione operatore.

## Verifica minima

```powershell
py -3.10 -m pytest -q test_understory_hybrid.py test_training_endpoints_exports.py test_governance_api.py
cargo test --manifest-path src-tauri/Cargo.toml
node --check devin/ui/static/js/codex_app.js
node scripts/test-workspace-browser.mjs
```

I bench di policy devono essere mutation-tested: almeno rifiuto shell, bounding
output, esclusione output grezzo dal training, persistenza della risposta,
wiring IPC e registro Governance devono diventare rossi se alterati.
