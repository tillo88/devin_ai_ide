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

## 4. Memoria privata e federazione futura

AutoMem e Understory sono due sottosistemi distinti. Anche tra agenti i dati
grezzi restano separati per ruolo e, dove richiesto, per progetto:

- niente database raw condiviso tra DEVIN, Clippy, Hermes e Teacher;
- niente promozione automatica di ipotesi, errori non spiegati o output di
  training;
- successi/fallimenti richiamabili solo con causa, evidenza e regola di retry;
- checkpoint di conversazione separati dalla memoria lunga.

La condivisione futura usa un *knowledge exchange* separato: artefatti promossi,
immutabili o versionati, con provenienza, ruolo/progetto sorgente, livello di
evidenza, audience/ACL, stato di review, scadenza e revoca. Hermes o Teacher
potranno consultare conoscenza promossa da DEVIN senza aprire i suoi store raw e
senza “contaminarli” con i propri ricordi.

Il broker/orchestratore potrà inoltre instradare una domanda veloce verso un
agente compatibile già residente (per esempio Hermes) senza cambiare modello.
Questa è una futura decisione di routing esplicita, non una condivisione di
memoria e non una scorciatoia attiva oggi.

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

## 6. Roadmap verificabile

1. **Lifecycle P0:** registro operativo unificato, richieste in-flight,
   backoff di attivazione, timeout coerente col broker.
2. **Routing P0:** Goal Mode sullo stesso allowlist/`work_dir` degli altri run.
3. **Desktop P1:** sincronizzare il Tauri host solo da un commit testato e
   validare apertura, riconnessione, start DEVIN e ritorno a Clippy.
4. **Web P1:** mantenere entrambi i provider, portare SearXNG a bind loopback e
   verificare entrambi gli ordini della catena senza esporre chiavi nei log.
5. **Memoria P2:** definire schema e review gate del knowledge exchange prima di
   collegare Hermes/Teacher.
6. **Orchestrazione P2:** capability routing verso un ruolo già residente,
   compatibile con policy e senza accesso agli store raw altrui.

Ogni fase richiede test repository prima della PR, poi source deploy, install e
smoke live separati. I vecchi probe 32K/hash completi non fanno parte del ciclo
ordinario: si ripetono solo quando cambia modello, artefatto o relativo gate.
