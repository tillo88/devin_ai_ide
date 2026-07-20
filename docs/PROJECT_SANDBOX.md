# DEVIN Project Sandbox

Obiettivo: permettere a DEVIN di lavorare su progetti veri senza toccare l'originale finché non c'è una review esplicita.

## Flusso consigliato

1. Scegli un progetto reale, per esempio DEVIN stesso o ForgeStudio.
2. Prepara una sandbox con `POST /api/sandbox/prepare`.
3. DEVIN lavora nella copia isolata: install, test, patch, esperimenti.
4. I risultati diventano log, attempt, review e lesson candidate.
5. Solo dopo review umana/Teacher/Colibrì si promuove una patch verso il progetto originale.

## Policy default

La sandbox copia i file normali ma salta automaticamente:

- `.git`, cache, build, dist, target, logs;
- `node_modules`;
- `venv`, `.venv`, `env` di default;
- `.env`, chiavi, token e file segreti;
- modelli/file pesanti come `*.gguf`, `*.safetensors`, `*.iso`, archivi;
- file oltre `max_file_size_mb`.

`include_venv=true` esiste, ma va usato solo quando serve davvero: copiare ambienti virtuali può pesare parecchio e spesso è meglio rigenerarli nella sandbox.

`link_venv=true` crea invece un collegamento simbolico al venv originale: è leggero e permette prove veloci senza copiare decine di GB. Attenzione però: un symlink Linux non è read-only. Se si fa `pip install` dentro quel venv linkato, si rischia di modificare il venv originale. Per questo il manifest lo marca come `read_only_dependency_reference` e `do_not_pip_install_into_linked_venv=true`.

## Manifest

Ogni sandbox scrive `.devin_sandbox_manifest.json` con:

- source path;
- sandbox path;
- policy usata;
- file/dir copiati;
- elementi saltati e motivo;
- promotion policy: `auto_apply_to_source=false`, `requires_diff_review=true`.

## API

```http
POST /api/sandbox/prepare
Content-Type: application/json

{
  "project_path": "/home/tillo/ForgeStudio",
  "include_venv": false,
  "link_venv": true,
  "include_secrets": false,
  "include_large_binaries": false,
  "max_file_size_mb": 50
}
```

## Flusso integrato dei run di manutenzione

Con `execution.change_application_mode=review` il ciclo
`Planner → Coder → Patcher → Runner` non copia più automaticamente il
sandbox nel progetto. Un Runner verde produce invece un
`change_manifest_v1` sotto `.devin_state/pending_changes/<run_id>/` e termina
con `status: awaiting_approval`.

Il manifest contiene l'elenco ordinato di file creati, modificati e rimossi,
hash SHA-256, dimensioni e permessi prima/dopo. `.env`, chiavi private,
runtime state, sandbox, venv, build, modelli e symlink non sono promuovibili.
La UI richiede di aprire la diff verificata prima di abilitare **Applica**.

```http
GET  /api/run/changes/{run_id}?path=<project>
POST /api/run/changes/apply
POST /api/run/changes/reject
POST /api/run/changes/rollback
```

Le decisioni POST ricevono `path`, `run_id` e, per apply, `commit`. Apply
ricontrolla integralmente gli hash del progetto e del sandbox prima della
prima scrittura: una modifica esterna rende il manifest stale e blocca tutto.
Le scritture usano replace atomico per file, mantengono backup verificati e
fanno rollback automatico se l'applicazione fallisce. Il rollback esplicito
rifiuta di sovrascrivere cambiamenti effettuati dopo l'approvazione.

`legacy_auto_apply` resta disponibile solo come modalità di migrazione; la
configurazione prodotto usa `review`.

Risposta:

```json
{
  "sandbox": {
    "schema_version": "project_sandbox_v1",
    "source_path": "...",
    "sandbox_path": "...",
    "manifest_path": "...",
    "promotion_policy": {
      "auto_apply_to_source": false,
      "requires_diff_review": true
    }
  }
}
```

## Regola d'oro

La sandbox può essere rotta. L'originale no. Qualunque ritorno verso l'originale deve passare da diff/review/test.
