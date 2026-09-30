# DEVIN AI IDE — stato corrente e ripresa

**Aggiornato:** 2026-09-30

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

Il redesign visivo del cockpit e' in corso sul branch
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

Sul medesimo branch, come incremento separato, e' stato integrato il bridge per
le cartelle Windows locali: Tauri apre il picker nativo e conserva il path solo
nel registro locale protetto; il rig riceve uno snapshot filtrato e bounded in
un mirror gestito e versionato. `Applica` e `Rollback` esportano esclusivamente
il manifest approvato e scrivono sul PC solo se i fingerprint locali
coincidono, conservando una copia di recupero. Il browser normale continua ad
accettare soltanto path che esistono sulla macchina del backend.

Playwright e Chromium sono installati fuori dal repository sotto
`%LOCALAPPDATA%\DEVIN\test-tools\playwright`; il replay cockpit si avvia con
`scripts/test-workspace-browser.mjs` impostando `PLAYWRIGHT_MODULE` e
`PLAYWRIGHT_BROWSERS_PATH`. Nessuna dipendenza Playwright e' stata aggiunta al
progetto.

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
