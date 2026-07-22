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
     clone, es. `/home/tillo/devin_ai_ide` — DA CONFERMARE il path esatto).
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

### Intoppo ricorrente: `config/settings.json` blocca il `git pull`
`config/settings.json` e' **tracciato** in git ma sul rig viene modificato in
locale (`rig_self_hosted=true`, `ui.host=0.0.0.0` messi dall'install script).
Quando un commit in arrivo tocca lo stesso file, `git pull` si ferma con
"Your local changes would be overwritten by merge".

Via pulita, con backup (sul rig, root del clone):
```bash
cp config/settings.json ~/settings.rig.bak      # backup
git checkout -- config/settings.json            # scarta la modifica locale
git pull
bash scripts/rig/install_devin_backend.sh       # ri-mette rig_self_hosted/host + restart
diff ~/settings.rig.bak config/settings.json     # controllo: altre personalizzazioni perse?
```
Fix definitivo (da fare bene, prossimo giro): **smettere di tracciare**
`config/settings.json`, metterlo in `.gitignore` e tenere un
`config/settings.example.json` come template. Cosi' la config per-macchina non
va mai in conflitto ai pull. (`git update-index --skip-worktree` NON basta: se
upstream modifica il file, il pull si blocca comunque.)

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
  approval_policy, budget_steps, budget_seconds}`. `acceptance` accetta stringhe
  DSL (`tests_pass`, `file_exists:PATH`, `contains:PATH:TESTO`, `absence:REGEX`,
  `command:...`) o dict `{type, params}`. Ritorna `{goal_run_id, status}`.
- `GET /api/goal/{goal_run_id}` — stato + attempts (polling).
- `GET /api/goal` — lista.

### ⚠️ Ambiente del PC dell'utente (Alessandro)
- **Cartella Download su `G:\Downloads`** (NON `$HOME\Downloads`). Usare sempre
  `G:\Downloads` nei comandi che salvano/scaricano file sul PC.
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
$id="goal_20260723_010143"; Invoke-RestMethod -Uri "http://192.168.1.100:5000/api/goal/$id" | ConvertTo-Json -Depth 8 | Out-File -Encoding utf8 "G:\Downloads\$id.json"
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
