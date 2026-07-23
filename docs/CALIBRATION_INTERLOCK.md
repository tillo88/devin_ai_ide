# DEVIN calibration interlock v1

## Stato

**Codice candidato, non ancora distribuito sul backend live.**

La serie formale KVarN in corso mantiene servizi, baseline e comportamento
invariati. Questa modifica puo' essere sviluppata e testata in branch, ma non
deve essere applicata o attivata sul DEVIN finche' la serie non e' conclusa e
registrata.

## Obiettivo

Impedire che nuove chat o nuovi Goal vengano accettati mentre il controller di
calibrazione sta per fermare o riconfigurare il modello del rig.

Il backend non ferma il modello e non gestisce direttamente la calibrazione.
Espone invece una barriera di ammissione fail-closed e un segnale di drain
verificabile.

```text
controller arma interlock
-> backend rifiuta nuove chat e nuovi Goal
-> controller attende safe_to_stop_model=true
-> controller ferma/riconfigura il modello
-> calibrazione
-> controller ripristina il modello e ne verifica la health
-> controller disarma interlock
```

## File di controllo

Percorso predefinito:

```text
/run/ai-rig/calibration-interlock.json
```

Override per test:

```text
DEVIN_CALIBRATION_INTERLOCK_FILE
```

Schema v1:

```json
{
  "schema": "calibration_interlock_v1",
  "active": true,
  "reason": "formal KVarN calibration",
  "owner": "ai-rig-calibration",
  "calibration_run_id": "kvarn-formal-1",
  "created_at": "2026-07-24T00:00:00+02:00"
}
```

Il controller privilegiato deve creare il file atomicamente nella stessa
directory, con file temporaneo, `fsync`, rename e permessi dichiarati. Il
backend e' soltanto lettore.

Semantica:

- file assente: ammissione aperta;
- file valido con `active=false`: ammissione aperta;
- file valido con `active=true`: nuove richieste protette bloccate;
- file illeggibile, corrotto, sovradimensionato, symlink o schema errato:
  ammissione bloccata fail-closed, ma `safe_to_stop_model=false`.

Un file invalido non autorizza mai lo stop del modello.

## Richieste bloccate

Solo `POST` che possono consumare il modello o avviare lavoro:

```text
/api/chat
/api/chat/vision
/api/chat/document
/api/run
/api/run/resume
/api/chat/scaffold
/api/chat/generate_patch
```

Restano disponibili durante il drain:

- health e diagnostica;
- endpoint di sola lettura;
- stream/log dei run gia' avviati;
- `/api/stop` per recovery;
- `/api/calibration/interlock` per lo stato.

Una richiesta bloccata riceve HTTP `423` con codice stabile:

```text
calibration_interlock_active
calibration_interlock_invalid_fail_closed
```

## Drain

Endpoint:

```text
GET /api/calibration/interlock
```

Campi principali:

```json
{
  "schema": "calibration_interlock_status_v1",
  "interlock": {
    "status": "active",
    "valid": true,
    "active": true,
    "blocked": true
  },
  "activity": {
    "active_chat_requests": 0,
    "goal_admissions_in_progress": 0,
    "pending_goal_ids": [],
    "starting_run_ids": [],
    "active_run_ids": [],
    "runtime_snapshot_ok": true
  },
  "drained": true,
  "safe_to_stop_model": true
}
```

`safe_to_stop_model=true` richiede contemporaneamente:

1. interlock valido e attivo;
2. snapshot runtime leggibile;
3. nessuna chat SSE ancora aperta;
4. nessuna ammissione Goal in corso;
5. nessun Goal accettato privo di evidenza terminale;
6. nessun run in `starting_runs`;
7. nessun run in `active_runs`.

Le chat SSE restano contabilizzate fino all'ultimo body ASGI. I Goal accettati
restano in `pending_goal_ids` fino a un footer terminale nel relativo log,
anche quando sono gia' visibili in `starting_runs` o `active_runs`. Questo
chiude sia la finestra risposta-JSON -> thread background sia le transizioni
starting -> active -> terminale.

## Relazione con il token gate

Il calibration interlock e' composto dentro `TokenGateMiddleware`:

```text
CORS
-> autenticazione remota
-> calibration interlock
-> FastAPI/router
```

Il token gate puo' essere disabilitato per loopback o per configurazione, ma
l'interlock continua a essere applicato. I client remoti continuano invece a
dover superare l'autenticazione prima di leggere lo stato o raggiungere il
backend.

Entrambi sono middleware ASGI puri e non bufferizzano SSE.

## Test richiesti prima del deploy

```text
python -m py_compile devin/core/calibration_interlock.py devin/ui/token_gate.py
pytest -q test_calibration_interlock.py
pytest -q
```

Prima dell'attivazione live servono inoltre:

1. inventario dell'unita' `devin-backend.service` e del percorso realmente
   distribuito;
2. backup e rollback;
3. test con file assente: comportamento identico alla baseline;
4. test lock attivo: chat e Goal bloccati, `/api/stop` e status disponibili;
5. test drain con una chat SSE reale e con un Goal reale;
6. nessun riavvio o cambio modello durante la serie KVarN formale;
7. integrazione successiva col controller di calibrazione in `ai-rig-ops`.

## Limiti v1

- il file e' locale al ruolo DEVIN;
- il backend non crea e non cancella il lock;
- nessun timeout forza il drain: il controller attende o fallisce;
- nessun run attivo viene terminato automaticamente;
- il controller esterno deve verificare anche health e identita' del modello
  dopo il ripristino, prima di disarmare l'interlock.
