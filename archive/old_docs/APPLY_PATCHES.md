# FASE A — Robustezza: Patch Applicazione

## File da applicare

### 1. NUOVO: `devin/core/state_persistence.py`
**Copia** il file `state_persistence.py` in `devin/core/state_persistence.py`

Questo modulo gestisce il salvataggio atomico dello stato dell'orchestratore su disco.

### 2. MODIFICA: `devin/ai/client.py`
**Sostituisci** il file esistente con `client.py` fornito.

Cambiamenti:
- **Task 12**: Retry con backoff esponenziale (2s, 4s, 8s) in `local()` e `stream()`
- **Task 17**: WOL (Wake-on-LAN) + health check rig con polling fino a 90s
- Nuove configurazioni in `settings.json` per WOL (vedi sotto)

### 3. MODIFICA: `devin/core/orchestrator.py`
**Sostituisci** il file esistente con `orchestrator.py` fornito.

Cambiamenti:
- **Task 13**: Persistenza stato dopo ogni step del loop
- Resume automatico da crash: se c'è uno stato `.devin_state/run_*.json` non completato, riparte da `attempt` corrente
- Salvataggio atomico con write-then-rename
- Pulizia stato su successo

### 4. AGGIORNA: `config/settings.json`
Aggiungi queste chiavi sotto `"models"`:

```json
{
  "models": {
    "rig_host": "192.168.1.100",
    "rig_ports": [8000, 8001],
    "rig_mac": "XX:XX:XX:XX:XX:XX",
    "wol_enabled": true,
    "wol_port": 9,
    ...
  }
}
```

Sostituisci `XX:XX:XX:XX:XX:XX` con il MAC address reale del rig (ottenibile con `ip link show` sul rig).

---

## Stato salvato (`.devin_state/run_*.json`)

```json
{
  "task": "Fix the bug in calc.py",
  "attempt": 1,
  "last_error": "Execution failed: assert error...",
  "last_patch": "diff --git a/calc.py...",
  "plan": {"steps": [...], "raw_response": "..."},
  "context_length": 15000,
  "max_retries": 3,
  "step": "critic_done",
  "model_source": "local",
  "_saved_at": "2026-07-02T06:45:00",
  "_run_id": "run_20260702_064500",
  "_project_path": "/home/tillo/devin_ai_ide/workspace/test_project"
}
```

## Flusso di recovery

1. Orchestratore crasha durante attempt 1, step `critic_done`
2. Al prossimo `orch.run()`, `StatePersistence` trova lo stato
3. Riprende da `attempt=1`, con `last_error` del critic già caricato
4. Il Coder genera nuova patch con il feedback del critic
5. Loop continua normalmente

## WOL Flow

1. `refresh()` vede rig offline
2. Se `wol_enabled=true` e MAC configurato, invia magic packet
3. Attende fino a 90s con polling ogni 5s a `/v1/models`
4. Se rig risponde, usa modelli 32B; altrimenti fallback a locale
