# DEVIN Windows functional lifecycle receipt — 2026-08-22

## Identita' del collaudo

- sorgente locale `origin/main`: `9d393fbc0def212f7fcfeacc3c3e2cc1a4ace681`;
- sorgente installata sul rig: `9d393fbc0def212f7fcfeacc3c3e2cc1a4ace681`;
- applicazione Windows installata: `0.2.0`;
- profilo: Tauri/WebView2 thin client -> frontdoor autenticato sul rig;
- stato iniziale: app ferma, `READY | resident=clippy | devin=idle`;
- una sola attivazione DEVIN, una sola chat e una sola chiusura normale.

Il token non e' stato letto, stampato o copiato. Non sono stati eseguiti SHA
GGUF, probe 32K, NVML/`nvidia-smi`, test USB o riavvii del rig.

## Transizione Clippy -> DEVIN

L'eseguibile installato e' stato avviato direttamente da:

```text
C:\Users\tillo\AppData\Local\DEVIN AI IDE\devin-ai-ide-desktop.exe
```

La finestra `DEVIN AI IDE` e' rimasta responsive. Il frontdoor ha acquisito una
sessione e la pagina di preparazione ha mostrato:

- fase `loading_devin_model`;
- attesa `ai-rig-model-slot@devin.service`;
- ETA iniziale e aggiornamenti progressivi;
- `ETA=calcolo` dopo il superamento della stima, senza falso `ETA 0`.

Il model-slot DEVIN e' partito alle `22:14:38 CEST`; llama-server ha dichiarato
`model loaded` e ascolto su `127.0.0.1:18081` alle `22:23:18 CEST`. Tempo reale:
circa **8m40s**, coerente con il supporto USB attuale. I warning sui tensori MTP
non usati erano gia' noti e non hanno impedito il ready.

Stato osservato dopo l'handoff:

```text
AI-RIG DEVIN | READY | resident=devin | frontend=connected
clippy=inactive
devin-backend=active
ai-rig-model-slot@devin=active
ai-rig-devin-frontdoor=active
```

PASS: il broker e' rimasto l'unico owner della transizione; nessuno stop manuale
o secondo server di test e' stato introdotto.

## Cockpit e chat minima

Il passaggio al cockpit e' avvenuto automaticamente dopo la health reale. Sono
state verificate visivamente:

- top bar con `DEVIN ready`, modello e contesto;
- rail Projects, Knowledge, MCP Tools, Agent Swarm e Training;
- area centrale Chat/Editor/Diff/Log/Governance;
- pannello Goal & Attivita';
- General chat senza progetto selezionato.

Prompt dello smoke:

```text
Rispondi solo con DEVIN_OK.
```

La richiesta e' stata accettata alle `22:26:58 CEST` e completata alle
`22:28:13 CEST`. Il server ha impiegato circa **74.3s**: 1.183 token di prompt e
33 token di generazione. Lo stream e lo storico server-side hanno entrambi
contenuto la risposta finale `DEVIN_OK`; il cockpit e' tornato `Ready`.

PASS: trasporto SSE, inferenza, rendering e persistenza della General chat.

## Chiusura e rilascio idle

La finestra e' stata chiusa con il normale evento Windows:

```text
CloseMainWindow=True
Exited=True
StillRunning=False
```

Subito dopo la chiusura il contratto busy ha risposto:

```json
{"schema":"devin_active_operations_v1","busy":false,"operations":[],"counts":{}}
```

Il frontdoor ha quindi mantenuto DEVIN durante il timeout idle configurato,
senza interpretare la chiusura come autorizzazione a interrompere lavori remoti.

Al raggiungimento dei 600 secondi idle il backend e il model-slot DEVIN sono
diventati inattivi. Il broker ha poi avviato il solo model-slot Clippy alle
`22:41:57 CEST`: llama-server ha completato il caricamento in circa **6m22s** e
lo stato end-to-end e' diventato ready alle `22:48:47 CEST` (circa **6m50s**
dall'inizio del ripristino).

Stato finale:

```text
AI-RIG DEVIN | READY | resident=clippy | devin=idle
ai-rig-clippy-chat.service state=active restarts=0
devin-backend.service state=inactive restarts=0
ai-rig-model-slot@devin.service state=inactive restarts=0
ai-rig-model-slot@clippy.service state=active restarts=0
ai-rig-devin-frontdoor.service state=active restarts=0
ai-rig-model-slot-broker.service state=active restarts=0
```

PASS: ritorno automatico a Clippy senza reboot, stop manuale, `SIGKILL` o
restart di servizio.

## Finding UX/latency emersi

Questi finding non invalidano l'attivazione o la chat, ma restano follow-up
espliciti e non vanno persi:

1. **P1 — web search forzata.** Il cockpit invia oggi
   `use_web_search=true` per ogni messaggio e mostra “web sempre attiva”. Anche
   il prompt di smoke ha tentato fetch web (Playwright opzionale assente) senza
   alcun intento web. Il default deve diventare `web auto`, lasciando al backend
   l'attivazione conservativa per intento e mantenendo SearXNG/TinyFish come
   catena/fallback per caso d'uso.
2. **P1 — stato globale transitorio.** Durante l'inferenza il badge centrale e'
   passato brevemente a `Error`, poi e' tornato `Ready` senza fault e con
   risposta completata. `refresh()` tratta il fallimento di `/api/mind/status`
   come errore dell'intero cockpit mentre gli altri poll sono fail-soft. Va
   preservato l'ultimo snapshot buono e mostrato `busy`/`aggiornamento`, non un
   falso errore globale.
3. **P2 — identita' modello.** Il meta-evento della chat stampa il path assoluto
   completo del GGUF. La UI deve mostrare alias/profilo strutturato e lasciare il
   path solo alla diagnostica tecnica, senza hardcode del nome modello.
4. **P2 — reasoning grezzo.** Il blocco `<think>...</think>` viene mostrato nel
   corpo della risposta. Per una UX stile Codex va separato in una superficie
   Reasoning richiudibile, preservando la risposta finale pulita.

## Verdetto

- installazione e avvio release: PASS;
- attesa lifecycle osservabile: PASS;
- Clippy -> DEVIN: PASS;
- cockpit nativo: PASS;
- chat SSE + persistenza: PASS;
- chiusura processo Windows: PASS;
- DEVIN -> Clippy dopo idle: PASS;
- stato finale e zero restart: PASS.

Il percorso operativo aggiornato e' in
[`DESKTOP_VALIDATION_CHECKPOINTS.md`](DESKTOP_VALIDATION_CHECKPOINTS.md).
