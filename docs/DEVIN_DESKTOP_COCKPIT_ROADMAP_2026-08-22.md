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

## C3 — editor e diff centrali

- file tree del workspace validato sul rig;
- tab editor read-only nella prima iterazione;
- unified diff affiancata con Apply/Reject espliciti;
- terminale/log come superficie secondaria, filtrando warning noti dai fault;
- nessuna scrittura fuori dal project/work_dir autorizzato.

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
