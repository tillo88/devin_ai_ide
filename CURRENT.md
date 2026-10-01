# DEVIN AI IDE — stato corrente e ripresa

**Aggiornato:** 2026-10-01

**Punto di ingresso:** questo file, poi `AGENTS.md` e
`docs/CURRENT_ARCHITECTURE.md`.

## Obiettivo attivo

Il lavoro corrente è il frontend desktop di DEVIN AI IDE v2. La PR `#33`
(`ccd7e59`) ha consegnato il flusso reale:

```text
Progetto -> Goal -> Esecuzione -> Revisione -> Evidenze
```

La barra usa gli stati Goal e l'ultimo run del progetto; apre Goal, Runs, il
change manifest verificato e il log bounded. Non inventa percentuali e non
trasforma `success` in `verified_success`.

Il redesign visivo del cockpit e' completo sul branch
`codex/frontend-visual-redesign`. Il commit `ccd2609` introduce la gerarchia
graphite/blu desaturato senza cambiare il contratto Goal/Run/review. Il secondo
pass C6.3 rialza la leggibilita' delle informazioni operative, alleggerisce i
rail e rende Chat, composer, Goal e Runs superfici distinte senza cambiare DOM
o routing. Il replay Playwright ora cattura anche la vista Chat e verifica i
breakpoint 1440/1000/390 insieme al bridge locale. Il pass C6.4 porta la stessa
scala e gerarchia su Editor, manifest Diff, Log bounded e Governance; su mobile
il diff verificato diventa una sequenza Prima/Dopo leggibile senza uscire dal
viewport. Il pass finale C6.5 rende immediata e non ambigua la selezione delle
viste, verifica semanticamente il tab attivo e aggiunge le catture Chat ai
breakpoint 1000/390. Il redesign e' completo sul branch e la cache della shell
e' `v20`.

Sul medesimo branch, come incremento separato, e' stato integrato l'agente per
le cartelle Windows locali. Tauri apre il picker nativo e conserva il path solo
nel registro locale protetto; il rig riceve un `bridge_id` opaco, mai il path e
mai una copia completa della cartella. Tree, letture e ricerca contestuale
avvengono on-demand nel processo desktop. Per un task operativo il modello puo'
chiedere altre letture e proporre un piano di file; Tauri mostra una conferma,
verifica i fingerprint, applica al massimo 20 operazioni/2 MiB atomicamente e
conserva un recovery locale. Non esiste piu' un limite alla dimensione totale
della cartella collegata. Il vecchio endpoint snapshot resta compatibilita' per
workspace gia' collegati e per il precedente export verificato.

L'agente locale usa ora un percorso one-shot: Tauri prepara prima dell'inferenza
un evidence pack bounded (albero, retrieval e letture con fingerprint), poi il
backend ammette una sola richiesta e un solo tentativo modello. La risposta e'
conclusiva (`done|plan`); un piano puo' proporre una verifica locale dopo
l'apply, sempre con conferma, senza una seconda inferenza. Rust non espone una
shell, limita programma/argomenti/cwd, ripulisce l'ambiente, chiude l'intero
albero con un Windows Job e restituisce output bounded con hash. Gli episodi
finiscono in `pending_review` senza copiare file o output grezzi sul rig;
**Utile** e **Da correggere** producono rispettivamente evidenza umana e
fallimento/correzione per il successivo export SFT. Contratto completo in
`docs/LOCAL_AGENT_EXECUTION_AND_TRAINING.md`.

Checkpoint live del 30 settembre sera:

- `/api/local-workspace/register` e il precedente
  `/api/local-workspace/agent-step` erano caricati nel backend di produzione;
- corretto e distribuito il `500` della chat con contesto locale: il router ora
  accetta sia il riepilogo diagnostico strutturato sia quello testuale delle
  copie backend precedenti. La regressione e' stata mutation-tested (torna
  rossa reintroducendo l'assegnazione su stringa); backup recuperabile in
  `/home/tillo/devin-deploy-backups/20260930-local-context-500`;
- il retrieval diretto non viene piu' monopolizzato da dump in `data/debug`:
  elimina il rumore conversazionale, penalizza artefatti generati, privilegia
  README/manifest/entrypoint/sorgenti e limita ogni estratto a 4.000 caratteri
  con un marcatore esplicito che il file continua sul disco;
- ogni richiesta non banale su un workspace Windows diretto passa ora
  all'agente desktop `read/read_many/tree/search/web_search/run/done|plan`. L'albero e' bounded a 5.000
  caratteri/120 file con sorgenti prima degli artefatti; contesto iniziale e
  osservazioni sono bounded a 10.000 caratteri ciascuno per non superare il
  contesto del modello. Errori JSON del server non vengono piu' scambiati per
  azioni vuote;
- corretto il loop che poteva consumare tutti i passi in letture e
  terminare con "senza produrre un piano" anche per una richiesta di sola
  analisi. Il client conserva una cronologia compatta dei tool, non ripete le
  stesse azioni, mantiene testa e coda delle letture lunghe e puo' leggere fino
  a tre file indipendenti per passo. L'ottavo passo e' riservato alla
  conclusione `done|plan`; una risposta ancora strumentale viene rifiutata e
  riceve un solo retry conclusivo, senza altre letture;
- smoke live sulla domanda originale: richiesta autonoma di `main.py`, poi
  dell'albero e di `configs/spy_config_example.json`. Verificato sul disco che
  `engine.py` e `manager.py` sono completi e che i quattro moduli dichiarati
  mancanti dalla vecchia risposta esistono; nessun file del progetto e' stato
  modificato;
- smoke live del nuovo passo 8 sul progetto locale `price_check_bot`:
  `status: done` con fatti confermati, ipotesi distinte e limiti espliciti;
  nessuna nona lettura e nessun errore di budget. Router produzione SHA256
  `c0b53a7f2ae1`; backup recuperabile in
  `/home/tillo/devin-deploy-backups/20261001-local-agent-conclusion-v2`;
- smoke reale del planner: `write hello.txt`, contenuto `hello\n`, fingerprint
  nullo per file nuovo; il piano non e' stato applicato e la fixture e' stata
  spostata in `workspace/_trash`;
- release corrente:
  `%LOCALAPPDATA%\DEVIN\build-cache\cargo-target\release\devin-ai-ide-desktop.exe`,
  bundle `20261001111233`, EXE SHA256
  `0739E23458D4F7EDF4BCE807F6C80A093421BDF100825B7F80F6BED7C64184CD`;
- suite canonica Windows con `settings.json` assente durante il run: 699 pass,
  7 skip, 1 deselected (symlink senza privilegio); il file originale
  `DBC02AF75FDF` e' stato ripristinato. Inoltre: 15 test Rust, replay
  Playwright 1440/1000/390 e bundle locale verdi;
- verifica incrementale dell'agente conclusivo: 79 test Python verdi e un solo
  errore ambientale noto `WinError 1314` sul test symlink Windows; 15 test Rust,
  syntax check JS/Python, replay Playwright e smoke IPC WebView reale verdi. I
  nuovi bench sono stati mutation-tested sui limiti `read_many`, sul prompt
  conclusivo, sul flag client e sul retry backend;
- il writer locale resta separato dall'executor: quest'ultimo non espone una
  shell ed esegue test/comandi di verifica soltanto con approvazione e
  terminazione controllata.

Checkpoint finale del 1 ottobre:

- router di produzione: `chat.py` SHA256 `e5672cb9d20f`, `training.py`
  `60a93a3b9053`, `governance.py` `38510c90d19f`; backup recuperabile in
  `/home/tillo/devin-deploy-backups/20261001-local-agent-execution-training`;
- 107 test Python verdi (piu' il test symlink Windows escluso per il noto
  `WinError 1314`), 17 test Rust, syntax check JS/Python e `git diff --check`;
- le sei mutazioni intenzionali (shell consentita, output non bounded, stdout
  grezzo nel training, risposta non persistita, comando IPC errato e registro
  Governance disabilitato) hanno reso rossi i rispettivi bench e sono state
  ripristinate;
- replay Playwright offline verde a 1440/1000/390; smoke WebView reale verde
  per trasporto e IPC; smoke executor reale verde con ricevuta
  `devin_local_command_receipt_v1` su `python --version` e rifiuto PowerShell;
- l'app normale e' riaperta senza porta di debug; frontdoor/backend risultano
  `ready`.
- la prima prova utente ha esposto un `read_many` fuori schema al passo 2. Un
  primo fix concedeva un'altra inferenza immediata nello stesso passo; due
  prove successive sono coincise con reboot non puliti del rig proprio a quel
  punto. Il retry aggiuntivo e il budget a 12 passi sono quindi stati rimossi
  in via conservativa. Il loop e' tornato a 8 passi e `read_many` viene
  normalizzato localmente: alias `files`/stringa singola, deduplica e cap ai
  primi tre path, senza una seconda chiamata modello nello stesso endpoint.
  Backup pre-fix in
  `/home/tillo/devin-deploy-backups/20261001-local-agent-schema-retry`.
- durante la prima riprova il rig ha effettuato un reboot non pulito alle
  10:26, senza shutdown registrato. Non esiste ancora evidenza che l'inferenza
  ne sia stata la causa. Al ritorno SSH il broker era attivo ma frontdoor,
  backend e slot modelli erano tutti inattivi e nessuna unita' risultava
  `failed`; il boot aveva inoltre atteso 90 secondi il device Wi-Fi USB
  `wlx9418655e0d59`. Il frontdoor, pur `enabled`, ha richiesto uno start manuale
  con sudo. Dopo lo start: frontdoor/backend/modello `ready`, router produzione
  SHA256 `ebaffc4c47a5`. La resilienza boot appartiene ad `ai-rig-ops`; la
  ripresa automatica del passo agente dopo indisponibilita' transitoria resta
  il prossimo incremento frontend. Un secondo reboot non pulito si e'
  verificato ripetendo lo stesso task; la correlazione piu' stretta e' con la
  doppia inferenza del retry al passo 2, non con il valore visuale 12 in se'.
- l'owner ha confermato che la stessa failure mode era gia' emersa durante la
  selezione del modello: i test erano stati isolati creando una nuova istanza
  per ogni caso. Questo rende il tool loop multi-inferenza sulla stessa istanza
  un design non sicuro anche con il limite ripristinato a 8. Prima di altre
  prove l'agente locale deve passare a una sola inferenza senza retry HTTP
  impliciti, alimentata da evidence raccolta deterministicamente sul PC; il
  multi-step potra' tornare solo dopo un contratto broker di istanza/slot fresco
  verificato sul rig.
- una terza riprova e' terminata con un altro reboot non pulito, questa volta al
  passo 3/8 dopo la rimozione del retry correttivo. Il dato falsifica l'ipotesi
  che bastasse tornare da 12 a 8 passi: il rischio e' la sequenza di inferenze
  sulla stessa istanza. Nel sorgente locale il loop e' quindi stato eliminato:
  il bundle costruisce `devin_local_one_shot_evidence_v1`, chiama soltanto
  `/api/local-workspace/agent-once`, mentre `/agent-step` fallisce chiuso. Il
  nuovo endpoint passa `max_attempts=1` ad `AIClient`; una risposta strumentale
  o una disconnessione non provocano una seconda generazione. I bench di client,
  backend e wiring sono stati mutation-tested e diventano rossi se si
  reintroducono retry o vecchio endpoint. Nessuno smoke modello e' autorizzato
  prima di un contratto broker verificato per istanza fresca.
- release one-shot Windows costruita nel target esterno previsto, 12,67 MiB,
  bundle `20261001111233`, SHA256 `0739E23458D4`. Installer puliti dal commit
  `3689913`: NSIS 3,11 MiB SHA256 `7e6262c2b352`, MSI 4,45 MiB SHA256
  `2c2480895a89`, manifest `source_dirty=false`. La build Cargo diretta aveva
  creato per errore 2,953 GiB rigenerabili in `src-tauri/target`: la release e'
  stata ricostruita in `%LOCALAPPDATA%\DEVIN\build-cache\cargo-target` e il
  target interno e' stato rimosso con `manage-desktop-build-cache.ps1
  -Action clean-legacy`; checkout e desktop-host non contengono piu' target
  legacy.
- verifica finale locale: 706 test Python tracciati verdi, 7 skip e 1
  deselected per il noto privilegio symlink Windows; il run canonico e' stato
  eseguito con `config/settings.json` assente. Il bootstrap ne ha generato uno
  di default durante i test, quindi quella copia e' stata quarantinata in
  `%TEMP%` e l'originale e' stato ripristinato con SHA256 `DBC02AF75FDF`.
  Inoltre: 17 test Rust, replay Playwright 1440/1000/390, smoke bundle locale,
  syntax check Python/JS e `git diff --check` verdi. L'app resta chiusa finche'
  il backend di produzione non espone il contratto one-shot.
- il discriminatore tensor-split del 1 ottobre ha completato 6/6 inferenze
  fresche senza reboot, tutte con 1.118 prompt token e split esplicito. C0
  (ordine PCI esplicito, 1660S@03 prima e A2000 ultima) e' il candidato per il
  lifecycle fresco: 51,32 s medi end-to-end. C1 (1080 Ti prima, 1660 Ti
  ultima) resta il candidato residente per throughput: 73,62 prompt tok/s.
  Il vecchio profilo dei tre hard reset non dichiarava ne' UUID order ne'
  `--tensor-split`; la correlazione e' forte ma non prova ancora la root cause.
  Receipt in `/home/tillo/ai-rig-experiments/tensor-split-20261001` e copia
  locale ignorata in `_bridge/tensor-split-20261001`.
- BeeLlama 0.4.6 supporta `response_format=json_object` con schema. Il client
  one-shot inoltra ora uno schema `done|plan` nella stessa unica richiesta e
  conserva il parser fail-closed come secondo gate; non converte testo libero
  in successo. Il probe isolato C0 del 1 ottobre ha prodotto al primo tentativo
  un `done` JSON valido, distinguendo fatti/ipotesi/limiti, con boot invariato e
  Clippy ripristinato. Il bench e' stato mutation-tested sia sul forwarding
  dello schema sia sul rifiuto degli status strumentali.

Playwright e Chromium sono installati fuori dal repository sotto
`%LOCALAPPDATA%\DEVIN\test-tools\playwright`; il replay cockpit si avvia con
`scripts/test-workspace-browser.mjs` impostando `PLAYWRIGHT_MODULE` e
`PLAYWRIGHT_BROWSERS_PATH`. Nessuna dipendenza Playwright e' stata aggiunta al
progetto.

Il 2026-09-30 il confine desktop e' stato corretto: la WebView non naviga piu'
verso `/app` sul rig. Il cockpit resta il bundle locale incorporato nell'EXE e
usa il frontdoor soltanto come API/backend. Rust valida l'endpoint, esegue le
richieste verso la LAN fidata e inoltra status/chunk alla UI via Channel
Tauri. Le capability
del picker/sync/apply valgono soltanto per l'origine Tauri locale. Lo smoke
Playwright del bundle e' stato mutation-tested sul comando di trasporto nativo.

Checkpoint locale dello stesso giorno:

- replay visuale verde a 1440/1000/390 e smoke bundle/IPC release verde;
- `cargo test`: 15 pass;
- suite Python canonica con `config/settings.json` temporaneamente assente:
  695 pass, 7 skip, 1 deselected (symlink Windows senza privilegio); il file
  locale originale, fingerprint `DBC02AF75FDF`, e' stato ripristinato;
- installer di prova generati in `dist/windows` (NSIS 3,08 MiB, MSI 4,39 MiB),
  con manifest `source_dirty=true` perche' il lavoro non e' ancora committato;
- il frontdoor reale su `192.168.1.100:5000` e' raggiungibile e il trasporto
  nativo funziona. Il token desktop e' stato rimosso: i client della LAN
  `192.168.1.0/24` sono autorizzati direttamente dal frontdoor.

La pulizia locale ha rimosso cinque `.gguf` ignorati, la `.venv-win` rotta e i
vecchi bundle backend `build/devin-backend` e `dist/devin-backend`, recuperando
circa 25 GiB. Il cache Cargo condiviso sotto `%LOCALAPPDATA%\DEVIN\build-cache`
resta intenzionalmente fuori dal repository per rendere rapidi i rebuild.

## Workspace da usare

La sorgente primaria per il lavoro quotidiano è il progetto Codex **locale**:

```text
F:\devin_ai_ide
```

Stato al passaggio di consegne:

- `main` riallineato a `origin/main` su `ccd7e59` prima di questo refresh doc;
- vecchi commit Windows conservati nel ramo locale
  `backup/windows-main-diverged-20260929`;
- lock Git stale a zero byte conservato come
  `.git/index.lock.stale-20260910T045045`;
- `Claude outputs/`, `_backup/` e `_bridge/` sono dati locali untracked: non
  cancellarli con `git clean` e non committarli.

I collegamenti SSHFS visibili dal PC sono superfici operative separate:

| Superficie Windows | Destinazione | Uso |
|---|---|---|
| `Z:\` | home di `tillo@192.168.1.100` | lettura/trasferimento sul rig; include `Z:\devin_ai_ide` |
| rete `ai-rig-shared` | `/mnt/ai-rig-shared` sul rig | artefatti, evidence, recovery e modelli secondo i relativi contratti |
| `R:\` | home di `tillo@192.168.1.86` | Raspberry watcher e diagnostica |

Il nome o la lettera del collegamento shared può cambiare: risolverlo dalla
voce `ai-rig-shared`, senza hardcodare una lettera non verificata.

SSHFS espone file, non trasferisce ownership operativa. Non usare `Z:` per
modificare checkout remoti o unità systemd dal progetto locale; per comandi,
test Linux, deploy e servizi aprire una chat Codex sul relativo host SSH.

Il progetto Codex locale deve avere come radice `F:\devin_ai_ide`. Non serve
aggiungere l'intero `Z:`, `R:` o `ai-rig-shared` alle cartelle del progetto:
restano accessibili da Explorer e PowerShell quando occorre una consultazione
mirata, senza far indicizzare modelli, log, recovery o dati live. `ai-rig-ops`
resta un repository separato; se servirà lavorarci da Windows avrà un proprio
clone, branch, PR e progetto Codex.

## Copie e runtime

| Percorso | Ruolo |
|---|---|
| `F:\devin_ai_ide` | sorgente primaria, branch, PR, bundle Tauri |
| `origin/main` | verità condivisa del repository |
| `/home/tillo/devin_ai_ide` | checkout Linux per test/diagnostica, non produzione |
| `/opt/devin-ai-ide-frontend` | codice eseguito da `devin-backend.service` |
| `%LOCALAPPDATA%\DEVIN\desktop-host` | mirror Tauri generato, mai sorgente |
| `%LOCALAPPDATA%\DEVIN AI IDE\devin-ai-ide-desktop.exe` | release installata `0.2.0` del 22/08/2026; contiene il vecchio bundle |

Durante l'iterazione frontend avviare il runtime di sviluppo:

```powershell
& "$env:LOCALAPPDATA\DEVIN\DEVIN Desktop.cmd"
```

L'eseguibile installato cambia soltanto dopo una nuova build e reinstallazione
NSIS/MSI.

## Ciclo di lavoro locale

1. Verificare `git status --short --branch` in `F:\devin_ai_ide`.
2. Creare un branch; mai modificare direttamente `main`.
3. Modificare e testare sul PC ciò che è Windows/Tauri/frontend.
4. Generare il bundle:

   ```powershell
   powershell.exe -NoProfile -ExecutionPolicy Bypass -File '.\scripts\build-frontend-bundle.ps1' -SourceRepo 'F:\devin_ai_ide'
   ```

5. Sincronizzare il runtime nativo:

   ```powershell
   powershell.exe -NoProfile -ExecutionPolicy Bypass -File '.\scripts\prepare-windows-desktop-host.ps1' -SourceRepo 'F:\devin_ai_ide' -SkipNpmInstall
   ```

6. Aprire il launcher di sviluppo e fare il controllo visuale.
7. Commit, push e PR. Dopo il merge, il deploy del backend resta un checkpoint
   separato sul rig.

I test completi Linux usano
`/home/tillo/devin_ai_ide/.venv-rig/bin/python3`. Ogni risultato deve indicare
quale `config/settings.json` è stato usato. Non eseguire NVML mentre un modello
è attivo.

## Riferimenti correnti

- `AGENTS.md`: regole di lavoro e confini delle copie;
- `docs/LOCAL_WINDOWS_WORKSPACE_20260929.md`: setup dettagliato del progetto locale;
- `docs/CURRENT_ARCHITECTURE.md`: runtime Desktop/rig e lifecycle;
- `docs/WORKSPACE_GOAL_RUNS_20260919.md`: contratto Goal/Runs/project flow;
- `docs/DEVIN_DESKTOP_COCKPIT_ROADMAP_2026-08-22.md`: roadmap visuale e milestone;
- `docs/DESKTOP_VALIDATION_CHECKPOINTS.md`: smoke della release installata.

I file `CONTINUITY_*` e `docs/devin_rig-deploy-runbook_v1.md` sono storici:
non usarli per scegliere percorsi o servizi correnti.
