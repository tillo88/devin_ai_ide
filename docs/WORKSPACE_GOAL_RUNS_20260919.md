# Workspace: Goal e Runs centrali

Il modulo Goal era nella colonna destra, troppo stretta per obiettivo, criteri e
budget; la lista dei run non aveva una vista accessibile nel workspace.
Ora Goal e Runs si aprono dalla navigazione e dalle schede centrali. Il pannello
destro conserva un riepilogo del Goal, contesto e timeline. La cronologia Runs
usa l'API esistente e dichiara esplicitamente il suo ambito: intero rig, ultimi
50 run. Ricerca per ID/stato e apertura di log/eventi sono di sola lettura.

Il form mantiene modalità, ruolo, approvazione e limiti già applicati dal backend.
Il diff verificato e il digest richiesto per applicarlo restano il percorso di
review. Nessuna nuova esecuzione shell è aggiunta dalla vista Runs.

## Correzione dell'ambito del Goal

`GET /api/goal?project_path=...` risolve il progetto con lo stesso allowlist gate
e linked work_dir usati dall'esecuzione, e restituisce soltanto i suoi Goal.
La query vuota rappresenta la chat generale e non restituisce obiettivi. L'API
senza parametro conserva il comportamento globale per compatibilità.
`any_active` comunica l'occupazione globale senza esporre l'obiettivo degli altri
progetti: il vincolo di un Goal alla volta resta attivo anche nell'interfaccia.

Il cambio progetto svuota obiettivo/criteri del form, selezione e riepiloghi;
le risposte Goal superate vengono scartate per progetto e sequenza richiesta.
Sono scartate anche le risposte tardive di overview, timeline e log per una
selezione ormai cambiata. Un errore della cronologia viene mostrato come errore,
non come assenza di run.

## Validazione ripetibile

La suite Python usa un worktree inizialmente privo di configurazione locale.
Il bootstrap genera `config/settings.json` dal template versionato
`config/settings.example.json` (identici byte per byte, SHA256
`51b7c19a705dd9ea08b74bc468c2dbc65ddb7d4503d2f8919ec543d1428f46c1`).
La configurazione di produzione non viene letta o copiata.
Il replay `scripts/test-workspace-browser.mjs` carica HTML/CSS/JS reali in Chromium
con tutte le API intercettate da fixture locali; nessun backend o modello reale.
Richiede Node >=20 e Playwright installati in un ambiente di test separato.
`PLAYWRIGHT_MODULE` può indicare il modulo assoluto; `WORKSPACE_SCREENSHOTS`
è una directory opzionale per le catture desktop/tablet/telefono.

Il replay verifica invio del Goal e policy, esclusione delle risposte vecchie,
ricerca dei run, log, errore HTTP, guardia Apply e layout a 1440/1000/390 pixel.
Il test Python del filtro include due progetti, linked work_dir, chat generale
e occupazione globale. Le mutazioni del filtro e della guardia delle risposte
sono verificate separatamente e ripristinate prima del commit.

## Proposte sulla memoria: prossimo contratto, non implementazione implicita

Il PDF e il mockup sono stati usati come direzione del prodotto. Non introducono
un secondo ContextEngine o uno storage raw condiviso fra DEVIN e Clippy.

| Proposta | Modulo da estendere/verificare | Prova richiesta prima di attivarla |
|---|---|---|
| Contesto con fonti e budget | `core/steward_coordinator.py`, `evidence_retriever.py` | Snapshot derivato, provenienza e budget ripetibili |
| Invalidazione indice | `memory/vector_store.py` | Cambio contenuto, rimozione file, cambio progetto e ranking |
| Promozione della memoria | `memory/taxonomy.py`, `knowledge_exchange.py` | Quarantena/revocati esclusi dal recall; scambio solo revisionato |
| Estrazione fuori dal percorso della risposta | client AutoMem/Understory/hybrid | Tempi di retrieval distinti dal consolidamento; errori espliciti |
| Replay e budget adattivo | Steward e orchestratore | Replay limitato con ricostruzione completa e costo misurato |

Il coordinator esistente deriva lo snapshot dal core, resta privo di GPU/LLM e
non promuove da solo verso AutoMem/Understory. La cache vettoriale ha già formato
JSON versionato e invalidazione dei file: prima di aggiungere stati FULL/REINDEX/
REUSE va definito cosa riusare e quando scartarlo a livello di applicazione.
La memoria persistente e la KV cache del modello restano sistemi distinti.
