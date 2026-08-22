# DEVIN Desktop Cockpit — roadmap 2026-08-22

## North star

DEVIN deve sembrare una vera AI IDE Windows, non una pagina web incorniciata.
La shell segue quattro superfici permanenti:

1. barra superiore: model-slot, modello attivo e pressione del contesto;
2. rail sinistra: Projects, Knowledge, MCP Tools, Agent Swarm e Training;
3. area centrale: conversazione, attività, editor/diff e approvazione;
4. rail destra: Goal Mode, checklist verificabile, budget e timeline.

Il mockup fornito dall'owner il 2026-08-22 è la direzione visuale. I valori
mostrati devono però provenire da contratti strutturati: nessuna finta
percentuale, nessun completamento dedotto dal testo del modello.

## Architettura bloccata

- Windows esegue soltanto Tauri/WebView2 e conserva la configurazione protetta.
- Il frontdoor autenticato sul rig è always-on e possiede la sessione DEVIN.
- Aprire il frontend può richiedere il passaggio Clippy -> DEVIN; il model-slot
  broker resta l'unico owner della transizione.
- Chiudere la finestra non interrompe un run remoto. Il rilascio avviene solo
  dopo i gate busy/idle del frontdoor.
- Workspace, run, training e memoria di DEVIN restano sul rig. Un eventuale
  accesso futuro a cartelle Windows dovrà usare un bridge/sync esplicito e
  verificabile, non una condivisione implicita.
- Il frontend non usa NVML. GPU e modello sono rappresentati tramite readiness,
  inventario `/v1/models` e stato lifecycle. La VRAM numerica resta non
  campionata finché non esiste una telemetria sicura approvata.
- Nessun nome modello o ruolo futuro viene hardcodato nella UI. Il modello
  attivo arriva da `/api/health`; ruoli/capacità arrivano dal routing profile.

## C1 — cockpit strutturato

Stato: merged in `main` con PR `#14` (`540ad4ded635992794f1a9e45f698f6dedc461e0`).

- barra superiore con GPU slot, modello attivo e Context Steward;
- Goal panel read-only da `/api/goal` con criteri, esito, step e tempo;
- rail applicativa allineata a Projects/Knowledge/MCP/Swarm/Training;
- cache PWA `v6` per impedire che una shell vecchia mascheri l'aggiornamento;
- telemetria NVML disabilitata di default e vietata nel profilo rig-primary;
- profilo Cargo dev senza debug symbol/incremental cache multi-gigabyte.

Acceptance:

- nessun `nvidia-smi` eseguito dai poll UI o dal watchdog rig-primary;
- il Goal panel espone solo dati strutturati e bounded;
- modello/slot non risultano ready senza health reale;
- bundle Tauri locale piccolo e riproducibile;
- `/`, `/chat` e `/history` restano fallback.

Validazione reale del 2026-08-22:

- thin client Tauri nativo avviato su Windows e finestra responsiva;
- frontdoor autenticato passato da Clippy a DEVIN tramite il model-slot broker;
- modello DEVIN pronto in 8m37s sul supporto USB attuale;
- health, training, run, workspace, knowledge exchange, council, routing, goal e
  mind status tutti HTTP 200;
- TinyFish presente e SearXNG, AutoMem e Understory attivi;
- nessun `nvidia-smi`, hash GGUF o probe 32K eseguito dal frontend o dallo
  smoke test;
- chiusura Windows inviata come normale evento finestra; il frontdoor mantiene
  il run remoto e applica il rilascio solo dopo i gate idle/busy.
- dopo 600 secondi idle il backend e il modello DEVIN sono diventati inattivi;
  Clippy è tornato unico residente e healthy, senza stop manuale, SIGKILL o
  processo `nvidia-smi`.

La prova ha anche evidenziato un requisito UI vincolante: una unità systemd
`active` non equivale a un modello pronto. La barra superiore deve mostrare
`loading_devin_model` finché la health del trasporto dichiarato nell'envelope
non risponde; se la stima viene superata deve mostrare “stima in aggiornamento”,
mai un falso `ETA 0`.

## C2 — Goal operativo

- form guidato objective + criteri machine-verifiable;
- scelta scaffold/maintenance e manual/auto per-goal;
- stop/resume e motivazione del blocco;
- evidenza di ogni criterio collegata a test, comando o manifest;
- stato live via stream, non solo polling.

### C2.1 — controllo locale consegnato (2026-08-22)

- form cockpit collegato a `POST /api/goal/run`, con progetto/`work_dir`
  validato, objective, criteri tipizzati, ruolo, policy e budget limitati;
- un solo goal background alla volta e stato live con polling mirato a 2 secondi;
- `POST /api/goal/{goal_run_id}/stop` cooperativo: lo stato diventa `stopping`
  e l'arresto avviene soltanto dopo la fine dello step corrente, senza kill;
- checklist, valutazione finale, motivazione e consumo budget restano visibili.

### C2.2 — stream ed evidenze consegnati (2026-08-22)

- stream SSE outer-loop dedicato con eventi bounded `started`, `attempt`,
  `stop_requested`, `finished` ed `error`;
- nessun path progetto o log grezzo nello stream; polling a 5 secondi solo come
  fallback e refresh della checklist guidato dagli eventi;
- ogni criterio nel cockpit è apribile e mostra tipo, PASS/FAIL e dettaglio
  prodotto dall'evaluator machine-verifiable.

Resta C2.3: il resume non viene esposto finché non esiste un checkpoint Goal
persistente. Riavviare da zero e chiamarlo “ripresa” violerebbe il contratto
esplicito di continuità.

## C3 — editor e diff centrali

- file tree del workspace validato sul rig;
- tab editor read-only nella prima iterazione;
- unified diff affiancata con Apply/Reject espliciti;
- terminale/log come superficie secondaria, filtrando warning noti dai fault;
- nessuna scrittura fuori dal project/work_dir autorizzato.

### C3.1 — file tree e viewer read-only consegnati (2026-08-22)

- la rail sinistra mostra un albero bounded del progetto selezionato; se esiste
  un `work_dir`, anche la lettura viene instradata a quella root dopo una
  seconda validazione allowlist;
- il browser riceve soltanto path relativi: niente root assoluta, runtime
  `.devin`, cache, virtualenv, segreti comuni, chiavi o certificati;
- la scansione non segue symlink ed è limitata a 1500 file, 30000 elementi e
  profondità 12;
- il tab centrale Editor legge soltanto testo UTF-8, con massimo 256 KiB per
  anteprima, binari disabilitati e stato di troncamento visibile;
- non esiste un endpoint project-scoped di salvataggio e la UI non richiama il
  writer legacy: le modifiche restano nel flusso diff/review/apply;
- la cache della shell passa a `v9`; l'overview destra non ripete più la
  scansione file e riusa il tree già caricato.

### C3.2 — diff verificato centrale consegnato (2026-08-22)

- il workspace centrale espone ora Chat, Editor e Diff; la vista Diff mostra
  la lista dei file del `change_manifest_v1` e una lettura Prima/Dopo con
  numeri di riga, create/modify/delete e marker binari;
- il payload resta bounded dal backend (500000 caratteri) e il DOM applica un
  secondo cap di 2500 righe per file, segnalando ogni troncamento;
- la textarea capace di applicare patch arbitrarie è stata rimossa dal cockpit:
  la superficie primaria accetta soltanto manifest verificati in stato
  `pending`;
- Apply e Reject richiedono entrambi il digest di 64 caratteri visto durante
  la review. Il backend lo ricontrolla dentro il lock decisionale prima di
  qualsiasi mutazione; stale source/sandbox continuano a fallire closed;
- la conferma usa il dialog interno Tauri e una decisione in corso disabilita
  i due pulsanti, evitando doppi submit;
- parser diff puro coperto da test Node, shell/PWA aggiornata a `v10`.

### C3.3 — log tecnico centrale consegnato (2026-08-22)

- il workspace centrale espone ora anche Log per il run selezionato; la timeline
  laterale resta una sintesi e apre la superficie completa senza duplicare il
  log grezzo;
- i fault sono derivati dall'event store strutturato (error/fatal o chiusura
  failed/timeout/stalled), mentre la coda `.log` resta contesto tecnico
  read-only con filtri Tutto/Fault/Warning/Info;
- “Nascondi warning noti” usa una allowlist conservativa per deprecazioni e
  fallback opzionali. Warning operativi come rig non raggiungibile o chiavi
  provider assenti restano sempre visibili;
- `/api/terminal/output` valida il `run_id`, rifiuta traversal/symlink escape,
  limita la richiesta a 1000 righe e legge al massimo 512 KiB dalla coda;
- nessun falso terminale interattivo: `/api/terminal/input` non è collegato
  finché il runner non possiede realmente processo, stdin e lifecycle;
- parser/filtri sono puri e coperti da test Node; shell/PWA aggiornata a `v11`.

C3 è completo. La milestone successiva è C4: rendere ruoli, strumenti e
knowledge governance superfici reali e coerenti con il routing backend.

## C4 — agenti, strumenti e conoscenza

- Agent Swarm mostra ruoli logici e stato reale del dispatch;
- MCP Tools mostra registry, allowlist e budget deny-by-default;
- Knowledge distingue raw role memory, quarantena e exchange promosso;
- Council mostra copertura per asse e `needs_evidence` senza trasformarlo in PASS;
- Hermes e Teacher restano visibili solo come future-disabled finché assenti.

## C5 — packaging Windows

- build MSI/NSIS della thin client senza Python, WSL o modelli locali;
- configurazione `%APPDATA%\\DEVIN\\desktop.json` con ACL utente/SYSTEM;
- cache Cargo unica sotto l'host nativo e comando esplicito di pulizia;
- test su avvio pulito, retry frontdoor, aggiornamento shell e disinstallazione;
- eventuale firma e auto-update solo dopo l'accettazione funzionale.

## Ordine di verifica

Ogni milestone segue: test offline -> PR/CI -> source deploy controllato -> smoke
HTTP autenticato -> smoke Tauri Windows -> esercizio lifecycle -> receipt. Non si
ripetono SHA del modello, probe 32K, NVML o test del supporto USB.
