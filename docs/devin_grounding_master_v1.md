# DEVIN AI IDE — Grounding master (v1)

Sintesi completa del progetto, ricostruita leggendo TUTTI i doc (attivi +
archiviati) il 2026-07-23. Scopo: NON ri-derivare dal codice cose gia' decise.
Fonte primaria giorno-per-giorno restano i `CONTINUITY_*`; questo file e' l'indice
ragionato + il contesto che serve per lavorare grounded.

---

## 1. Cos'e' DEVIN
Coding agent locale, "il membro sviluppatore" della rig-family. Legge un progetto,
pianifica, genera patch (unified diff), le applica in sandbox, esegue i test e
itera fino a risoluzione. Prodotto target: **app desktop stile Codex/Claude**
(shell Tauri, backend FastAPI locale/rig dietro).

Fusione voluta (CONTINUITY 07-20):
- **ChatGPT/Codex**: disciplina d'indagine, modifiche verificabili, controllo utente.
- **Claude Desktop/Projects**: progetto persistente con chat, istruzioni, knowledge, file collegati.
- **Kimi Work**: subagent visibili e specializzati, task paralleli isolati, continuita' preventiva.

**Ordine decisionale (invariato):** 1) correttezza; 2) sicurezza + controllo utente;
3) affidabilita'/recuperabilita'; 4) chiarezza/osservabilita'; 5) manutenibilita';
6) efficienza; 7) nuove capacita'. Niente e' "completo" senza evidenza adeguata.

---

## 2. Le TRE copie del repo (non confonderle)
- **`D:\devin_ai_ide`** (Windows PC) — **unica copia di lavoro attiva** dal 2026-07-21.
- **`/home/tillo/devin_ai_ide`** in **WSL `Ubuntu`** (sul PC) — **CONGELATA**, backup finche' la suite Windows e' verde, poi si archivia. Non modificarla.
- **Rig**, macchina separata `192.168.1.100` (Ubuntu 24.04): ha il **suo** clone `/home/tillo/devin_ai_ide` da cui gira `devin-backend.service`. Ci si arriva via `ssh tillo@192.168.1.100`. Deploy = push da D:\ su GitHub -> `git pull` sul rig -> restart. Vedi `devin_rig-deploy-runbook_v1.md`.

Repo GitHub: `github.com/tillo88/devin_ai_ide`. Owner: Alessandro Tilloca (`tillo88`).
AGENTS.md dice "il repo reale e' la WSL": era vero prima del 2026-07-21, ora la
copia autorevole e' D:\ (Windows nativo).

---

## 3. Hardware e modelli
**PC primario (WSL2/Windows):** RTX 5070 Ti 16GB (Blackwell, CUDA 12.8). Ruolo: IDE
locale + backend di **fallback** + modelli locali (Qwen2.5-Coder-7B :8000, planner
MoE :8001, Ornith 9B Q8). llama.cpp b10075 CUDA in `%LOCALAPPDATA%\DEVIN\llama.cpp`.

**Rig esterno `192.168.1.100:8080`** (i9-10900X, ~51GB VRAM su 7 GPU miste; solo la
A2000 ha Tensor Core). **UN SOLO RUOLO alla volta** (boot triplo GRUB):
- **DEVIN** — coding agent operativo (modello Ornith-1.0-35B-A3B MoE).
- **TEACHER** — validatore/correttore locale principale.
- **HERMES** — assistente generale/multimodale.
Vincolo: se il rig e' in `hermes`/`teacher`, DEVIN non trova il modello e va in
fallback locale. Policy launcher **rig-first**: rig su -> niente locale; rig giu'
-> fallback locale (release manuale, non si scarica da solo).

**Colibri / GLM-Colibri (GLM-5.2 744B):** componente extra lento e profondo,
adjudicator batch delle evidenze (NON un ruolo sempre acceso, NON sostituisce
TEACHER). Cambio operativo previsto Ornith -> GLM-Colibri con canary obbligatorio.

---

## 3bis. Stack operativo del rig (dipendenza — fonte: AI_Rig_Operational_Runbook v1.2)
Il rig e' gestito dal progetto SEPARATO **ai-rig-iso-build**. Cio' che serve a DEVIN AI IDE:

- **Nel ruolo `devin` il rig fa girare DUE servizi:** `llama-server@devin.service`
  (il MODELLO Ornith su `:8080`, `/health`) e il nostro `devin-backend.service`
  (l'APP FastAPI su `:5000`). Il runbook del rig copre solo il primo; il secondo
  e' nostro (install_devin_backend.sh). Il backend parla al modello su `:8080`.
- **Modello DEVIN:** Ornith-1.0-35B-A3B MXFP4 MOE Q8 (ctx 32K, reasoning budget
  20480), motore BeeLlama storica `85e22ea` in `/opt/llama.cpp/build/bin`
  (=YES_DO_NOT_USE per esperimenti). Health `http://127.0.0.1:8080/health`.
- **Cambio ruolo (reale):** NON `grub-reboot` (commento obsoleto) ma
  `/usr/local/bin/ai-rig-select-role.sh <devin|hermes|teacher> [--poweroff]` che
  imposta UEFI BootOrder/BootNext (dischi per seriale). Il **bot Telegram su
  WolPi** (Raspberry) fa WOL + cold-swap (poweroff→WOL→verifica role→verifica API,
  fino a 900s): comandi `/wakeup /status /verify /devin /hermes /teacher`. Per
  lavorare su DEVIN AI IDE il rig deve essere in ruolo `devin`.
- **Ruoli distinti, fix NON si propagano** (drift noto: HERMES senza il drop-in
  `ai-rig-wait-gpus-stable.sh` che DEVIN/TEACHER hanno).
- **Regole non negoziabili che ci toccano:** niente NVML (`nvidia-smi`/`nvtop`/
  `nvitop`/`gpustat`) mentre CUDA e' attivo (solo one-shot prima/dopo); niente
  SIGKILL (D-state → recovery controllato/REISUB, marker `NEEDS_REBOOT`); dopo
  ogni test Ornith HEALTHY; fail-closed; una variabile alla volta; stop se un
  loop supera 10-15 min senza artefatto.
- **KVarN / preview v0.4.1:** percorso KV sperimentale, **quarantined** (Pascal/
  Turing sotto il requisito shared-memory per Qwen headwide). Non rilanciare.
- **Parallelo con la Goal Mode:** la sez. 22 del runbook rig ("orchestrator
  adattabile": canary prima delle curve, fail-closed, resume solo con fingerprint
  identico, **budget + heartbeat contro i loop silenziosi**) e' lo stesso spirito
  del nostro Goal loop. Domini diversi (calibrazione GPU vs coding), principi uguali.

## 4. Architettura software
**Inner loop di UNA run** (`devin/agents/` + orchestrator):
`Planner -> Coder -> Patcher -> Runner -> Critic` (max 3 retry, self-heal via Critic).
- `agents/`: planner (piano), coder (unified diff), critic (auto-correzione), prompts.
- `ai/`: client (routing rig->locale->OpenAI), stream, router, local_model_launcher (Linux-only), web_search, crawl_ingestion, structured_contracts, automem_client.
- `core/`: orchestrator, context_engine/retriever, project_space, change_manifest (review/awaiting_approval), state_persistence, context_steward + evidence_archive/retriever + steward_coordinator (CS0-CS3), run_events, docs_cache, **goal_mode/goal_runner/goal_executors (Goal Mode, nostri)**.
- `engine/`: patcher (git apply->patch->python fuzzy), runner (sandbox, Windows-aware), sandbox, shell, git_ops, syntax_critic (tree-sitter), security_critic (bandit), project_sandbox.
- `memory/`: vector_store, eval_recorder, taxonomy (recall-safe vs review-only).
- `training/`: adapters (MBPP...), benchmarks, store, validators.
- `ui/fast_app.py` (unico entry) + `ui/routers/*` (chat, runs_core/read, diff, explorer, projects, training, knowledge, pages, models_desktop, status, autocomplete, plan_terminal, **goal**).

**Outer loop = Goal Mode mini-swarm (nostro, in corso):** ogni RUOLO (Scaffolder,
Tester, Debugger) *e'* una run dell'orchestrator; il Goal loop + cancello di
verifica coordina. I due livelli si compongono, non si duplicano (vedi roadmap
skills-goalmode v2). Allineato a **P3** (subagent controllati: l'orchestratore
valida e decide, il subagent non applica da solo al progetto).

---

## 5. Training / eval — anti-contaminazione (CENTRALE)
**Regola base: eval != training.** I benchmark misurano; diventano training solo
via fallimenti/correzioni **validate** e rerun. Mai promozione automatica.

**Status ladder** (recall-safe solo gli ultimi): `runner_error`, `auto_success`
(check meccanici, NON "corretto"), `auto_failure`, **`verified_success`**,
**`verified_failure`**, **`human_confirmed`**.

**Quality gate multi-livello (implementato):** pytest reale + **gold test** iniettati
(guardia `gold_tampered`) + tree-sitter + bandit + validator semantici per-caso
(endpoint/domain allowlist, no secret in chiaro, no output finto, tests_pass).
Declassa `auto_success->auto_failure`, non promuove mai. Baseline MBPP col gate
severo ~**53%** (numero da battere post-LoRA).

**Review append-only** (`reviews.jsonl`): non sovrascrive lo stato grezzo; aggiunge
verdetto verificato + rationale + `method_trace`/`failure_mode`/`next_action`/
`lesson_candidate`. Si impara il **metodo**, non la risposta.

**Teacher packet** (JSONL evidence-heavy) per TEACHER/Colibri; output atteso con
`verdict/confidence/failure_type/evidence/correction/memory_lesson/promote_to_memory/rerun_required`.

### Federated Evidence Council (P6.3) = i "fratelloni", formalizzato
Review **cieca e manuale**: ogni reviewer riceve un **pacchetto diverso** e un
**ruolo distinto** (non conta finestre/marchi). Registra **5 assi distinti**; il
router sceglie per **copertura**, non quantita'; cambi critici -> Council esteso ma
bounded, niente duplicati di famiglia. **GLM-Colibri** = adjudicator locale +
generatore di esperimenti (non voto extra). Provider esterni (OpenAI/Claude) =
sorgente di ipotesi esterne, passaggio manuale, con **redazione + consenso** (mai
invio automatico di repo/file; log di cosa esce; risultato = `external_review`, non
verita' assoluta; promozione solo dopo validazione/rerun).

**Filosofia owner (NUOVA IDEA + P6):** validare il RAGIONAMENTO, non la soluzione
("3+3=5" = non hai capito il concetto). I 10 debiti P6 (gold test aggirabili via
conftest, detector mock permissivi, crash validator -> auto_success, runner_error
non conta come tentativo, quality gate indipendente dal codice del modello, dataset
SFT solo con provenance...) — punti 1 e 5 security-critical.

Il nostro **Tester adversariale** e' il **primo seme locale** di questa validazione
concettuale; il Council multi-modello e P6 sono l'estensione.

---

## 6. Roadmap tecnica P0-P9 (CONTINUITY 07-20, canonica)
- **P0** stabilizzare/pubblicare checkpoint.
- **P1** trust boundary reale: sandbox -> diff -> apply/reject (fail-open chiuso, default `review`).
- **P2** harness/orchestratore deterministico e bounded.
- **P3** subagent stile Kimi ma controllati (no ricorsione illimitata; l'orchestratore valida e decide; il subagent non applica al progetto).
- **P4** context engine e continuita' -> **Context Steward** CS0-CS5 (CS0-CS3 fatti: supervisore deterministico con isteresi/cooldown/loop-guard, evidence archive SHA-256, retrieval ibrido, coordinatore + `/api/steward/status`). CS4 compattazione LLM e CS5 stabilita' KV richiedono il modello vivo.
- **P5** memoria anti-contaminazione (recall-safe vs review-only).
- **P6** training + Federated Evidence Council + GLM-Colibri (+ Capacity & Context Budgeter).
- **P7** osservabilita' e UX desktop (pannello Evidence Council, selettore Ornith/Switch/Colibri).
- **P8** routing modelli e rig (profili versionati, canary, nessun auto-switch Ornith->Colibri).
- **P9** packaging Windows (.exe/.msi).

**Packaging** (obiettivo finale owner): app Windows installabile. FASE 1 fatta
(sidecar PyInstaller `devin-backend.exe`, installer .msi/.exe con solo frontend;
architettura rig-first nel `src-tauri/src/main.rs`: discovery rig, spawn backup
locale, stop solo del backend avviato dall'app).

---

## 7. Stato attuale verificato (al 2026-07-23)
- Suite: cresciuta 419 -> 421 -> 429 -> 454 -> **466 passed** (piu' i nostri test Goal Mode, 53, in sandbox). Windows nativo `.venv-win` Python 3.13; sandbox Linux Python 3.10/3.12; rig Python 3.12 (`.venv-rig`).
- Windows-native milestone raggiunta; installer .msi/.exe prodotti; exe backend ~350MB verificato.
- Context Steward CS0-CS3 in produzione.
- **Goal Mode (nostra):** goal_mode (checklist verificabile), goal_runner (loop + cancello di verifica/Red Team), goal_executors (Scaffolder + Tester), router `/api/goal/*` (ruoli scaffolder|tester|swarm). Primo run reale sul rig: scaffolder ok; swarm bloccato da collisione pytest nel sandbox annidato -> fix `tests_pass` (ignore workspace/ + import-mode=importlib). Tester (percorso `orchestrator.run`) ancora da vedere girare coi modelli.

---

## 8. Cosa e' stato archiviato e perche' (policy: archive-first, delete solo dopo review)
- **`archive/legacy/devin/ui/`**: UI **Tkinter** (app/main/editor/diff_viewer/stream_console) + **web_app Flask** — archiviati 2026-07-17: l'unico entry vivo e' `fast_app.py` (consolidamento su FastAPI/Tauri).
- **`archive/old_docs/`**: BASELINE (primo snapshot), HARDENING_STATUS (hardening sicurezza 2026-07-13, superato), AUDIT-TODO (audit esterno 28 punti, molti sul rig ISO), README_DEVIN_FASE2 (spec compatta vecchia), LEGGIMI (primo "cosa fa"), TRAINING_DATASETS_AND_BENCHMARKS + TRAINING_MINI_BENCH (fusi in `docs/TRAINING.md`), Local.txt/Rig Esterno.txt (note hardware). Motivo comune: tenere root piccola, storia preservata.
- **`src-tauri/target/` e `gen/`**: build-cache Rust (~5174 file) entrata per errore col commit esterno `c688186 "DEVIN WINDOWS"`; untrackata + gitignored; resta nella STORIA (pulizia con git-filter-repo da Windows, non urgente).

---

## 9. Fonti canoniche (leggere PRIMA di ri-derivare)
- `docs/INDEX.md` — mappa doc; i `CONTINUITY_*` sono la verita' operativa.
- `DEVIN_AI_IDE_CONTINUITY_2026-07-20.md` — **roadmap P0-P9 + Council** (canonica).
- `DEVIN_AI_IDE_CONTINUITY_2026-07-21.md` — migrazione Windows, packaging, Context Steward, architettura rig-first.
- `docs/TRAINING.md` — pipeline anti-contaminazione (canonico).
- `docs/NUOVA IDEA PER TESTING-TRAINING-E POSSIBILE DEBUG.txt` — visione owner: validazione concettuale + consenso multi-agente + P6.
- `docs/CONTEXT_STEWARD_PLAN.md`, `PROJECT_SANDBOX.md`, `RIG_TOKEN_GATE_DESIGN.md`, `CODEX_LIKE_MENTAL_MODEL.md`, `API_TAURI_SPEC.md`, `TAURI_DESKTOP.md`, `PACKAGING-ROADMAP.md`, `ROADMAP_INTEGRAZIONI_2026-07.md`, `AI_TOOL_ADAPTERS.md`, `FAST_APP_SPLIT_PLAN.md`, `DESKTOP_*`.
- `AGENTS.md` — regole operative repo (anti-contaminazione, non leggere segreti, commit dopo test verde, quoting WSL).
- Nostri: `devin_roadmap_skills-goalmode_v2.md`, `devin_rig-deploy-runbook_v1.md`.

---

## 10. Regole di lavoro (da rispettare)
- Leggere `INDEX.md` + `CONTINUITY_*` prima di ri-derivare o creare doc.
- Memoria anti-contaminazione: mai promuovere non-verificato; failure utili solo con causa+evidenza+regola di retry.
- Commit incrementali dopo un punto test verde; preservare segreti/stato runtime (mai committare `.env`, `tinyfish api.txt`, JSONL memoria viva, log, modelli, workspace runtime).
- PowerShell per l'owner **monoriga**; Download su **`G:\Download`**; l'owner esegue i comandi Windows (Claude legge gli output dai log).
- Deploy rig = push -> pull -> restart `devin-backend.service` (mai auto). Vedi runbook.
- Pulizia del vecchio: solo dopo review esplicita (archive-first).
