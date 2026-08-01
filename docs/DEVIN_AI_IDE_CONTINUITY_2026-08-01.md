# DEVIN AI IDE — Continuity 2026-08-01 (Council, MCP, branch aperti)

**Scopo:** far ripartire una sessione nuova senza ri-derivare nulla. Copre SOLO
il lavoro di questa sessione (Council, client MCP, calibration table builder) e
lo stato dei branch. Per il resto valgono le fonti canoniche in §7.

> ⚠️ **Leggi prima §1: il repository e' divergente.** Il lavoro descritto qui e'
> in commit locali non ancora allineati a `origin/main`.

---

## 1. STATO REPO — divergenza da sanare per prima cosa

Al 2026-08-01, nel working copy usato in questa sessione:

```text
main            9 commit avanti, 14 INDIETRO rispetto a origin/main
origin/main     aa05f83  Merge PR #6: DEVIN calibration interlock v1
```

**Fatto che cambia molte assunzioni: la PR #6 (calibration interlock) E' MERGIATA
in `origin/main`.** In tutta la sessione e' stata trattata come "Draft, non
distribuita" — non e' piu' vero. Chi riprende deve:

1. allineare `main` a `origin/main` (merge o rebase dei 9 commit locali);
2. ricontrollare le affermazioni sull'interlock nei doc di design (§5.3, §6.3);
3. rifare partire i due branch feature da `main` aggiornato.

Analogamente il repo operativo `ai-rig-ops` era ~920 commit avanti rispetto alla
copia letta qui (KVarN4 formal complete, campagna Clippy, deploy canary PR6,
runner GitHub, consolidamento runbook). **Le sue fonti valgono su quelle locali.**

---

## 2. Cosa e' stato costruito (tutto offline, nessuna chiamata al rig)

### 2.1 Federated Evidence Council — completo (fasi 1-5 + orchestrazione + ponte)

Design: `docs/devin_federated_council_design_v1.md` (aggiornato con lo stato).

| Modulo | Ruolo |
|---|---|
| `devin/core/council.py` | assi, `ReviewVerdict`, `ReviewPacket` (cieco), `LocalDeterministicReviewer`, `AXIS_LENS`, `coverage_gaps` |
| `council_router.py` | copertura assi, bounded, **no duplicati di famiglia** |
| `council_aggregator.py` | esito per asse e del Council, mappatura conservativa sullo status ladder |
| `council_arbiter.py` | esperimento **pre-registrato** + esecuzione nel gate |
| `council_budget.py` | budget, heartbeat, degrado che non blocca |
| `council_run.py` | orchestrazione end-to-end, un solo giro d'arbitrato |
| `council_model_reviewer.py` | reviewer semantico **model-agnostic** |
| `council_store.py` | ponte con il training store (packet, review append-only, batch) |

Test: `test_council*.py` — **~130 test offline**. Suite completa: **719 passed,
5 skipped** (era 561 a inizio sessione).

### 2.2 Client MCP — branch `feature/mcp-client-v1`

`devin/ai/mcp_client.py` + `mcp_registry.py`: transport HTTP generico con
`tools/list`, sessione riusata, multi-server, allowlist, budget, audit.
25 test, zero rete.

### 2.3 Calibration table builder v2.3.0 (fuori repo)

In `G:\Download\RIG CALIBRATION CURVES...`: scoring coverage-aware, telemetria
provata-vs-ignota, fairness dei campioni sottili, soglie esterne
(`calibration_gates.json`), 18 test. Backup in `_backup-pre-v2.3.0/`.

---

## 3. BRANCH APERTI (da riallineare e testare, poi merge)

| Branch | Commit | Contenuto | Manca |
|---|---|---|---|
| `feature/council-api-v1` | `34dd867` | `/api/council/review`, `/api/council/status`, sezione `council` in settings.example | test funzionale sul backend vivo; **aggancio all'interlock (§4)** |
| `feature/mcp-client-v1` | `26e56a5` | client+registro MCP, sezione `mcp` in settings.example | bridge stdio->HTTP, scelta primo server, **misura d'uso reale** |

Entrambi tagliati da un `main` **stale**: rifarli partire da `main` aggiornato.

I moduli core del Council sono su `main` ma **inerti**: nessun file dell'app li
importa, quindi un `git pull` sul rig non cambia il comportamento del backend.
L'endpoint (branch `council-api-v1`) e' invece la prima cosa che lo tocca.

---

## 4. AZIONE RICHIESTA: Council API dietro l'interlock

Ora che la PR #6 e' mergiata, `devin/core/calibration_interlock.py` espone
`PROTECTED_POST_PATHS`, che oggi contiene chat/autocomplete/run/scaffold/
generate_patch/`goal/run`/`training/run` ma **non** `/api/council/review`
(non esisteva quando la PR e' stata scritta).

**Regola:** finche' il Council gira col solo reviewer deterministico non consuma
modello e non serve proteggerlo. **Appena `council.model_reviewers` e' non
vuoto, `/api/council/review` diventa model-consuming** e va aggiunto a
`PROTECTED_POST_PATHS`, altrimenti una review puo' partire durante una finestra
di calibrazione e falsare la serie.

Da valutare al merge: protezione statica (sempre in lista) oppure condizionata
alla configurazione. La statica e' piu' sicura, la condizionata piu' comoda.

---

## 5. Decisioni e principi — perche' le cose sono cosi'

Sono il vero valore da non perdere: rifare il codice e' facile, ricostruire il
ragionamento no.

### 5.1 Il filo rosso: **il silenzio non e' un PASS**

Ovunque manchi evidenza, il verdetto e' `needs_evidence`, mai `pass`:
scanner non installato, esperimento inconclusivo, budget esaurito, reviewer
caduto, copertura parziale. Nasce dal fix #1 del calibration builder (assenza di
telemetria scambiata per telemetria pulita) ed e' stato applicato a tutto il
Council.

### 5.2 Anti-anchoring (conta di piu' ora che si confrontano modelli)

- Il `ReviewPacket` **scarta** `known_reviews`/`known_corrections`: un reviewer
  non vede i giudizi altrui.
- L'esito meccanico del gate e' presentato come **dato, non giudizio**, con
  avviso esplicito; `include_mechanical_status=False` lo nasconde del tutto.
- Al reviewer si dice che `needs_evidence` e' **legittimo e non penalizzato**:
  forzare una scelta binaria su evidenza insufficiente produce verdetti sicuri e
  sbagliati.

### 5.3 Nessun modello hardcodato

Il modello operativo e' un **placeholder** finche' la Model Evaluation Suite non
sceglie il candidato. Quindi: `chat` iniettata, identita' del modello registrata
in `evidence.model` a ogni review, `family` dalla config. C'e' un test che
rilegge il sorgente e fallisce se compare un nome di modello.

> **Trappola:** la `family` deve seguire il **MODELLO**, non il ruolo. Se si
> cambia modello lasciando la vecchia family, la regola "no duplicati di
> famiglia sullo stesso asse" smette di proteggere dagli errori correlati
> **senza dare alcun errore**: due reviewer che sembrano indipendenti sono lo
> stesso modello.

### 5.4 Evidenza registrata, non ri-derivata

Il ponte con lo store usa `tests.validators` e `tests.quality_gate` **salvati al
momento del run**, non li ricalcola: il workspace puo' essere cambiato dopo, e
ricontrollare oggi descriverebbe un altro attempt. Stessa regola degli archivi
di calibrazione.

### 5.5 Pre-registrazione dell'esperimento (arbitro)

`on_pass`/`on_fail` dichiarati **prima** dell'esecuzione; un esperimento che
porta allo stesso verdetto in entrambi i casi e' invalido perche' non
discrimina. Impedisce di razionalizzare il risultato dopo averlo visto.

### 5.6 MCP: deny-by-default e output non fidato

Scoprire un tool **non** autorizza a usarlo (`allowed_tools` vuota = niente).
MCP spento di default. L'output di un tool e' incapsulato da
`as_untrusted_context()` come **dato, mai istruzione**: un server compromesso
che risponde "ignora le istruzioni precedenti" appare per quello che e'.

---

## 6. TRAPPOLE TROVATE (non ripeterle)

1. **`pending_review` non chiude la coda dello store.** Il Council lo emette
   quasi sempre, quindi senza guardia ogni batch rigiudicherebbe gli stessi
   attempt in eterno. Risolto saltando quelli gia' visti dal Council
   (`force=True` per rigiudicare).
2. **`local_model_launcher.start_vram_watchdog` fa polling di `nvidia-smi` ogni
   30s.** Sul PC va bene; **sul rig violerebbe la regola "niente NVML durante
   CUDA"** (rischio D-state). Quel modulo ha primitive di swap riusabili
   (`swap_model`, `kill_server_on_port`, `wait_for_model_loaded`) importate in
   `orchestrator.py` e **mai chiamate**: si puo' riusarle per la Fase 0 OCR /
   Colibri, ma disattivando il watchdog.
3. **Copertura parziale spacciata per pass.** Corretto sia nel calibration
   builder (fix #3) sia nel Council (`strict_coverage`).
4. **Regex greedy `{.*}` sul JSON dei modelli**: con prosa + piu' oggetti
   falliva. Sostituita da scansione a graffe bilanciate.

---

## 7. Fonti canoniche (leggere PRIMA di ri-derivare)

**Repo operativo `ai-rig-ops` — ha la precedenza su tutto cio' che riguarda il
rig** (runbook, addenda, evidence, snapshot). E' molto piu' aggiornato di
qualunque copia locale.

In questo repo:

- `docs/devin_grounding_master_v1.md` — architettura, tre copie del repo, regole
  (⚠️ §7 "stato attuale" e' fermo al 2026-07-23: vale questo doc);
- `docs/devin_federated_council_design_v1.md` — design Council, §11 runtime
  GLM-Colibri;
- `docs/devin_mcp-integration-notes_v1.md` — decisione bridge stdio->HTTP,
  shortlist server, cosa evitare;
- `docs/devin_ocr-ingest-eval_v1.md` — **puntatore**: la fonte e' in `ai-rig-ops`
  (`docs/devin/DEVIN_OCR_INGEST_EVAL_v1.md`);
- `docs/CALIBRATION_INTERLOCK.md` (da `origin/main`) — contratto dell'interlock;
- `docs/TRAINING.md`, `DEVIN_AI_IDE_CONTINUITY_2026-07-20.md` (roadmap P0-P9).

---

## 8. Prossimi passi, in ordine

1. **Allineare `main` a `origin/main`** e rifare partire i due branch feature.
2. **Council API dietro l'interlock** (§4), poi test funzionale e merge.
3. **MCP**: bridge stdio->HTTP, primo server (`container-use` o
   `mcp-run-python`), e **misurare se il modello li usa davvero** invece di
   ignorarli — stessa regola posta per CodeGraph: il beneficio si misura, non si
   assume.
4. **Fase 6 Council (ExternalReviewer)**: richiede decisioni dell'owner
   (provider, policy di redazione, formato del consenso) — vedi §9 del design.
5. A modello scelto: accendere i reviewer semantici configurando
   `council.model_reviewers` (nessuna modifica al codice).

---

## 9. Regole di lavoro confermate in questa sessione

- Niente chiamate al modello del rig durante serie formali o benchmark.
- Niente NVML durante CUDA; `nvidia-smi` solo one-shot prima/dopo.
- Commit incrementali dopo test verde; branch dedicato per tutto cio' che tocca
  il backend (`main` solo per moduli inerti).
- `/opt/llama.cpp` e' il motore stabile del ruolo: build sperimentali separate.
- Il Council **non promuove**: la promozione resta gated dal rerun.
