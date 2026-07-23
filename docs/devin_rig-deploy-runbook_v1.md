# DEVIN — Runbook deploy sul rig + architettura backend (v1)

Istruzioni operative da NON dimenticare. Ricostruite dal repo (`scripts/rig/
install_devin_backend.sh`, `scripts/deploy-devin-webapp.sh`, `src-tauri/src/
main.rs`). Se un dettaglio marcato "DA CONFERMARE" e' sbagliato, correggilo qui.

---

## ⚠️ Regola d'oro

**Le modifiche al codice NON sono sul rig finche' non fai: commit → push su
GitHub → `git pull` sul rig → restart del servizio.**

Il rig NON tira in automatico. Riavviare il servizio senza aver fatto pull
ricarica lo STESSO codice di prima. (Errore gia' fatto una volta: non ripeterlo.)

---

## Architettura backend (due backend distinti)

1. **Backend sul RIG — sempre attivo.** Servizio systemd `devin-backend.service`.
   - Gira da un **clone git** del repo sul rig (`WorkingDirectory` = root del
     clone: **`/home/tillo/devin_ai_ide`** SUL RIG (macchina separata Ubuntu
     24.04 a `192.168.1.100`, via `ssh tillo@192.168.1.100`). NON confondere con
     la WSL del PC (anch'essa `/home/tillo/devin_ai_ide`, ma CONGELATA — vedi
     grounding master). La copia attiva di sviluppo e' `D:\devin_ai_ide`.
   - Comando: `.venv-rig/bin/python devin/ui/fast_app.py`.
   - Bind `0.0.0.0:5000` (raggiungibile dal PC sulla LAN a `192.168.1.100:5000`).
   - `config/settings.json`: `models.rig_self_hosted=true`, `ui.host=0.0.0.0`.
   - Parte al boot. Log: `journalctl -u devin-backend -f`.

2. **Backend LOCALE sul PC — on-demand.** Lo spawna l'app desktop Tauri quando
   il rig e' offline (`127.0.0.1:5000`). Legge i file del PC. Non e' un servizio.

L'app desktop usa il frontend bundlato e parla a uno di questi backend. L'inferenza
va al modello del rig; il backend locale e' backup.

---

## Aggiornare il rig (il metodo che usiamo: git pull)

Dal **PC** (questa cartella `D:\devin_ai_ide`):

1. Commit delle modifiche (gia' fatto man mano).
2. **Push su GitHub**: `git push origin main`
   (repo: `github.com/tillo88/devin_ai_ide`).

Sul **rig** (via SSH `tillo@192.168.1.100`), nella root del clone:

3. `git pull`
4. Restart del servizio. Due modi equivalenti:
   - rapido: `sudo systemctl restart devin-backend.service`
   - oppure lo script idempotente (aggiorna anche config, poi restart):
     `bash scripts/rig/install_devin_backend.sh`
5. Verifica: `curl -s http://127.0.0.1:5000/api/health`

### `config/settings.json` — FIX DEFINITIVO applicato (untrack + template)
Dal commit di untrack, `config/settings.json` **non e' piu' tracciato**
(gitignored): la config per-macchina non va piu' in conflitto ai pull. Il
template versionato e' `config/settings.example.json`; il codice
(`devin/core/settings_bootstrap.py::ensure_settings`, chiamato da orchestrator/
AIClient/fast_app) crea `settings.json` dal template solo **se manca** (clone
nuovo). Se esiste, non lo tocca: la config del rig resta intatta.

**Rollout ONE-TIME sul rig** (solo il pull che introduce l'untrack; il rig ha
ancora la vecchia versione tracciata + modificata). Sul rig, root del clone:
```bash
cp config/settings.json ~/settings.rig.bak    # 1. backup della config vera del rig
git checkout -- config/settings.json          # 2. scarta le mod locali -> pull pulito
git pull                                        # 3. l'untrack passa senza conflitto
cp ~/settings.rig.bak config/settings.json      # 4. ripristina la config del rig
sudo systemctl restart devin-backend.service    # 5. restart
```
Dopo questo giro, `settings.json` e' untracked sul rig e i pull futuri **non si
bloccano mai piu'** su quel file. (`git update-index --skip-worktree` NON
bastava: se upstream modificava il file il pull si bloccava comunque.)

### Quando serve toccare il venv `.venv-rig`
Solo se il commit introduce **nuove dipendenze pip**. In quel caso, sul rig:
`./.venv-rig/bin/pip install <nuova-dip>` (o rilancia `install_devin_backend.sh`,
che reinstalla la lista core). Le dipendenze core gia' installate includono:
openai, requests, numpy, scikit-learn, fastapi, uvicorn, python-multipart, pypdf,
python-docx, openpyxl, python-pptx, python-dotenv, instructor, tree-sitter,
tree-sitter-language-pack, bandit, youtube-transcript-api.

---

## Metodo alternativo (NON quello che usiamo): rsync → devin-webapp.service

Esiste `scripts/deploy-devin-webapp.sh`: fa **rsync** da PC a `/opt/devin-ai-ide`
sul rig e riavvia `devin-webapp.service`. E' un secondo percorso storico. **Non e'
quello in uso** (noi facciamo git pull su `devin-backend.service`). Annotato solo
per non confonderli: sono servizi e cartelle diversi.

---

## Frontend desktop (Tauri)

Modifiche a UI/JS/CSS: il bundle `src-tauri/frontend` va rigenerato con
`python scripts/build_frontend_bundle.py`, poi l'app Tauri va **ricompilata** per
vederle nella finestra desktop. Per test veloci in browser basta la tab servita
dal backend + Ctrl+F5 (JS live). Il backend serve la pagina Diagnostics: se giri
l'exe vecchio, serve codice vecchio.

---

## Goal Mode — come lanciarla (backend sempre attivo)

Endpoint (dopo che il rig ha il codice aggiornato):
- `POST /api/goal/run` — body: `{project_path, objective, acceptance[], mode,
  approval_policy, budget_steps, budget_seconds, role}`. `acceptance` accetta
  stringhe DSL (`tests_pass`, `file_exists:PATH`, `contains:PATH:TESTO`,
  `absence:REGEX`, `command:...`) o dict `{type, params}`. `role` =
  `scaffolder` (default) | `tester` | **`swarm`** (dispatch a 3 ruoli:
  scaffolder costruisce / debugger ripara, tester come cancello di verifica).
  Ritorna `{goal_run_id, status}`.
- `GET /api/goal/{goal_run_id}` — stato + attempts (polling).
- `GET /api/goal` — lista.

Avvio SWARM (monoriga, il caso completo):
```powershell
$body = @{ project_path="workspace/goaltest_swarm2"; objective="crea is_prime.py con is_prime(n) e test_is_prime.py; i test devono passare"; mode="scaffold"; role="swarm"; acceptance=@("file_exists:is_prime.py","tests_pass") } | ConvertTo-Json; $r = Invoke-RestMethod -Uri "http://192.168.1.100:5000/api/goal/run" -Method Post -ContentType "application/json" -Body $body; $r.goal_run_id
```
Negli attempts vedrai la strategy per step: `scaffolder` (costruisce), `tester`
(verifica adversariale), `debugger` (ripara se il tester trova un bug). Se non
avanza, ora si ferma con `blocked: nessun progresso` invece di ciclare a vuoto.

### Fatti confermati sul rig (da struttura ad albero, 2026-07-23)
- Python del venv: **3.12** (`.pyc` cpython-312 in tutto `devin/`).
- venv: `.venv-rig` (fastapi 0.139, bandit, ecc. gia' presenti).
- Goal Mode **deployata**: `devin/core/goal_mode.py|goal_runner.py|goal_executors.py`
  e `devin/ui/routers/goal.py` presenti e compilati.
- Layout repo: `config/ devin/ docs/ requirements/ scripts/ src-tauri/ tests/`
  + molti `test_*.py` a root. Esiste `devin/agents/` (planner/coder/critic):
  e' l'inner loop dell'orchestrator, vedi roadmap v2.

### ⚠️ Ambiente del PC dell'utente (Alessandro)
- **Cartella Download su `G:\Download`** (NON `$HOME\Downloads`). Usare sempre
  `G:\Download` nei comandi che salvano/scaricano file sul PC.
- Shell: PowerShell su Windows.

### ⚠️ Convenzione comandi PC: PowerShell SEMPRE monoriga
I comandi PowerShell per l'utente vanno dati **su una sola riga** (separatori `;`).
Incollare testo multi-riga nella shell fa scattare l'avviso Windows "Si sta per
incollare testo che contiene piu' righe" — fastidioso. Quindi: niente blocchi a
capo, tutto in linea.

Avvio goal (monoriga):
```powershell
$body = @{ project_path="workspace/goaltest_prime"; objective="crea is_prime.py con is_prime(n) e test_is_prime.py, test verdi"; mode="scaffold"; acceptance=@("tests_pass","file_exists:is_prime.py") } | ConvertTo-Json; $r = Invoke-RestMethod -Uri "http://192.168.1.100:5000/api/goal/run" -Method Post -ContentType "application/json" -Body $body; $r.goal_run_id
```

Watch stato (monoriga, esce a run finito):
```powershell
do { Clear-Host; $s = Invoke-RestMethod -Uri "http://192.168.1.100:5000/api/goal/$($r.goal_run_id)"; "STATO: $($s.status) - step: $($s.attempts.Count)"; $s.attempts | ForEach-Object { "  [$($_.index)] $($_.status)/$($_.strategy) sat=$($_.satisfied) - $($_.detail)" }; Start-Sleep -Seconds 3 } while ($s.status -eq 'running'); $s | ConvertTo-Json -Depth 8
```

Scarica il report JSON (monoriga):
```powershell
$id="goal_20260723_010143"; Invoke-RestMethod -Uri "http://192.168.1.100:5000/api/goal/$id" | ConvertTo-Json -Depth 8 | Out-File -Encoding utf8 "G:\Download\$id.json"
```

CLI equivalente (se mai servisse a mano): `python scripts/run_goal.py --project ...`.

---

## Stato Goal Mode (per continuita')
- Fatto: `goal_mode.py` (Goal + valutatore checklist), `goal_runner.py` (loop),
  `goal_executors.py` (ruolo Scaffolder + auto-apply in scaffold), router
  `/api/goal/*`, CLI. 43 test offline verdi.
- Decisioni bloccate: vedi `docs/devin_roadmap_skills-goalmode_v2.md` (D1 checklist
  a macchina, D2 cambia strategia sul blocco, D3 mini-swarm locale, D4 scaffold
  loop / maintenance checkpoint).
- Prossimo: provare lo Scaffolder reale sul rig; poi ruolo Tester; poi UI Goal.
