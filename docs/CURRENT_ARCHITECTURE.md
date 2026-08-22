# DEVIN — architettura corrente e confini operativi

**Aggiornato:** 2026-08-22
**Stato:** fonte canonica per il collegamento Desktop ↔ rig. I continuity log
datati restano prove storiche, non configurazione corrente.

## 1. Un solo backend logico, due superfici

Il frontend Tauri/Codex-like sul PC è il client. Il backend FastAPI e il modello
DEVIN vivono sul rig. La copia Tauri in `%LOCALAPPDATA%\DEVIN\desktop-host` è un
artefatto generato dal repository, non va modificata come fonte primaria.

Sul rig il frontdoor è sempre attivo e leggero. Quando nessun frontend è
collegato, Clippy può restare residente. Alla prima richiesta DEVIN il frontdoor
chiede al broker una sessione DEVIN; il broker scarica Clippy, carica il modello
DEVIN e avvia il backend. Quando non esistono richieste o operazioni attive per
il periodo idle configurato, la sessione viene rilasciata e Clippy torna
residente.

```text
Tauri / browser
      │ HTTP, token
      ▼
frontdoor :5000 (sempre attivo)
      │ sessione on-demand
      ▼
backend FastAPI :5001 ──► model slot DEVIN
      │
      └── release idle ──► model slot Clippy + terminal chat `clippy`
```

Il rilascio è fail-closed: richieste HTTP in corso e lavori background sono
entrambi considerati attività. Il contratto backend è
`GET /api/operations/active` (`devin_active_operations_v1`) e include run,
training e Goal Mode. Payload assente o malformato significa “occupato/non
verificato”, mai autorizzazione a spegnere.

## 2. Progetti e Goal Mode

Ogni operazione che riceve un progetto deve attraversare lo stesso allowlist
gate. Se il progetto ha un `work_dir` collegato, chat operativa, run, scaffold,
resume, patch e Goal Mode lavorano sul `work_dir` validato; metadati, memoria e
cronologia restano associati al progetto DEVIN.

Goal Mode non può creare directory arbitrarie fuori dal perimetro validato. Un
goal in `starting`, `running` o `stopping` compare nel registro operativo e
impedisce il rilascio idle.

## 3. Ricerca web: SearXNG e TinyFish

I provider formano una catena esplicita e ordinata. Profilo locale/privacy:

```json
"providers": ["searxng", "tinyfish"]
```

Per un caso d'uso in cui TinyFish è più adatto si inverte l'ordine. Il fallback
scatta su errore/rete/provider non disponibile, non su una risposta valida ma
vuota: una ricerca locale senza risultati non viene inoltrata silenziosamente a
un servizio cloud. SearXNG resta supportato e non è un componente legacy da
rimuovere; sul rig deve essere raggiungibile soltanto dalle superfici che ne
hanno bisogno, preferibilmente su loopback.

## 4. Memoria privata e knowledge exchange

AutoMem e Understory sono due sottosistemi distinti. Anche tra agenti i dati
grezzi restano separati per ruolo e, dove richiesto, per progetto:

- niente database raw condiviso tra DEVIN, Clippy, Hermes e Teacher;
- niente promozione automatica di ipotesi, errori non spiegati o output di
  training;
- successi/fallimenti richiamabili solo con causa, evidenza e regola di retry;
- checkpoint di conversazione separati dalla memoria lunga.

La condivisione tra ruoli usa un *knowledge exchange* separato: artefatti promossi,
immutabili o versionati, con provenienza, ruolo/progetto sorgente, livello di
evidenza, audience/ACL, stato di review, scadenza e revoca. Hermes o Teacher
potranno consultare conoscenza promossa da DEVIN senza aprire i suoi store raw e
senza “contaminarli” con i propri ricordi.

Il backend implementa ora lo store e il relativo review gate; Hermes e Teacher
restano ruoli futuri disabilitati e non sono ancora collegati come consumer.

Il capability router può pianificare una domanda veloce verso un agente
compatibile già dichiarato residente, senza cambiare modello. Se serve un altro
ruolo restituisce `activation_required`: non esegue lo switch. Questa è una
decisione esplicita distinta dalla condivisione di memoria.

## 5. Confini di lifecycle e deploy

- GitHub e i due repository sono la fonte del codice; source deploy e mutazione
  dei servizi restano checkpoint separati.
- Il model-slot broker è l'unico proprietario delle transizioni GPU.
- Il gate source-deploy `DISABLED_NEUTRAL` resta intenzionalmente rigido. Se
  Clippy è residente, l'orchestrazione deve neutralizzarlo tramite il broker,
  eseguire il deploy e ripristinarlo; il deploy non deve aggirare il gate.
- Arresti normali usano solo `SIGTERM` con timeout e receipt; nessun `SIGKILL`.
- SearXNG è un fallback supportato. Timer/stack storici che scrivono nei vecchi
  store condivisi vanno disabilitati solo tramite una migrazione revisionata,
  senza fermare accidentalmente le memorie private correnti.

## 6. Roadmap canonica verificabile

La numerazione P0–P8 è quella di `devin_grounding_master_v1.md`, distinta dalle
priorità P0/P1/P2 usate nei piccoli piani operativi:

1. **P0/P1:** lifecycle e trust boundary Desktop↔rig, con source deploy e service
   install separati.
2. **P2:** orchestratore deterministico bounded.
3. **P3:** subagenti controllati e verificatore.
4. **P4:** Context Steward, checkpoint validati e prompt layout osservabile.
5. **P5:** memoria anti-contaminazione e knowledge exchange revisionato.
6. **P6:** training + Federated Evidence Council senza auto-promozione.
7. **P7:** UX/observability nel frontend desktop.
8. **P8:** profili di capability routing versionati, canary e nessuno switch
   automatico.

Stato, limiti e receipt sono in
`docs/P2_P8_ACCEPTANCE_2026-08-22.md`.

Ogni fase richiede test repository prima della PR, poi source deploy, install e
smoke live separati. I vecchi probe 32K/hash completi non fanno parte del ciclo
ordinario: si ripetono solo quando cambia modello, artefatto o relativo gate.
