# Federated Evidence Council — design (v1)

Design del Council (P6.3). Documento, NON codice. Fonda sul codice esistente e
sulla visione owner. Fonti: `docs/DEVIN_AI_IDE_CONTINUITY_2026-07-20.md` (P6.3,
Council roster, Capacity & Context Budgeter), `docs/NUOVA IDEA...txt` (multi-LLM
debate, validazione concettuale), `docs/TRAINING.md` (teacher packet, status
ladder), `devin/training/store.py` (packet + reviews append-only + provenance),
`devin/ai/structured_contracts.py`. Vedi anche `docs/devin_p6_debts_status_v1.md`
(i 10 debiti P6 sono chiusi: il Council poggia su una pipeline gia' indurita).

---

## 1. Cos'e' e cosa NON e'
Il Council e' lo **strato di review multi-modello** che trasforma attempt
`auto_*` (verdetto meccanico) in `verified_*` (promuovibili) **solo con evidenza**.
Non e' un voto di popolarita' tra modelli: e' review **cieca**, per **assi
distinti**, con un **arbitro** che, sulle discordanze, genera un **esperimento
deterministico** che decide. La verita' finale poggia su una prova rieseguibile,
non su un'opinione.

**Principi non negoziabili** (ereditati da P6/anti-contaminazione):
- **Niente auto-promozione**: il Council produce review; la promozione a
  recall-safe resta gated (verified + rerun). Riusa lo status ladder esistente.
- **Cieco**: ogni reviewer non vede i verdetti degli altri (no anchoring/groupthink).
- **Copertura, non quantita'**: il router sceglie reviewer per coprire gli assi,
  non per fare numero; su cambi critici Council esteso ma **bounded**, **niente
  duplicati di famiglia** sullo stesso asse (errori correlati).
- **Ragionamento, non soluzione**: si valuta la logica astratta ("3+3=5" = non
  hai capito il conteggio), non solo l'output.
- **Bounded**: budget di token/tempo per Council e per reviewer (Capacity &
  Context Budgeter), heartbeat contro i loop silenziosi.
- **Esterni = ipotesi, non voti**: OpenAI/Claude entrano con redazione + consenso,
  risultato = `external_review`, mai verita'; promozione solo dopo validazione/rerun.

---

## 2. Dove sta nella pipeline (riuso, non riscrittura)
```
attempt (mini-swarm o run)  -> quality gate deterministico (gia' esistente)
   -> auto_success | auto_failure | runner_error
   -> export teacher_packet.jsonl (store.export_teacher_packet, esistente)
   -> [ COUNCIL ]  <-- NUOVO
        router -> pacchetti per-reviewer (ciechi, per asse)
        reviewers (locali + esterni) -> verdetti per asse
        aggregazione (5 assi) -> concordi? / discordi?
        arbiter (GLM-Colibri) -> se discorde: genera ESPERIMENTO (test)
           -> rerun deterministico -> verdetto risolto con evidenza
   -> reviews.jsonl append-only (store.add_review, esistente) con provenance
   -> verified_success | verified_failure | needs_human_review
   -> promozione controllata + rerun benchmark (misura miglioramento reale)
```
Riusa: `export_teacher_packet`, `reviews.jsonl` (append-only, non sovrascrive),
`structured_contracts.TrainingReviewDecision`, lo status ladder, i validator
deterministici (che diventano il "reviewer locale" dell'asse vincoli/correttezza).

---

## 3. I 5 assi (ruoli distinti dei reviewer)
Ogni reviewer riceve UN asse (ruolo distinto) + un pacchetto tarato su quell'asse.
Il router garantisce che tutti e 5 siano coperti.

1. **Correttezza concettuale** — la regola logica/astratta e' giusta? Chiede
   Chain-of-Thought sul CONCETTO prima di guardare il codice.
2. **Robustezza / casi limite** — regge su bordi, negativi, input degeneri, casi
   che distinguono corretto-vero da plausibile-ma-sbagliato (il seme del Tester).
3. **Aderenza ai vincoli** — endpoint/allowlist reali, niente API inventate,
   niente hardcoding/mock che bara. (Il validator semantico esistente e' il
   reviewer locale di quest'asse.)
4. **Sicurezza** — vulnerabilita', input non validati, segreti, pattern
   pericolosi. (bandit/semgrep + semantica.)
5. **Qualita' / scope / manutenibilita'** — ha fatto esattamente il richiesto
   (no scope creep, no incompleto), codice mantenibile.

Nota: gli assi 3 e 4 hanno gia' un reviewer LOCALE deterministico (validators.py,
security_critic). Il Council li affianca a reviewer modello per gli assi 1, 2, 5
(giudizio semantico) dove il deterministico non arriva.

---

## 4. Componenti (interfacce, da implementare dopo)

### 4.1 ReviewerAdapter (interfaccia comune)
Contratto unico per reviewer locali ed esterni (testabile con stub, come i ruoli
del mini-swarm):
```
review(packet, axis) -> ReviewVerdict {
    axis, verdict: pass|fail|needs_evidence,
    confidence: 0..1,
    reasoning: str,          # il ragionamento sul concetto (obbligatorio)
    violations: [str],
    proposed_experiment: str|None,   # un test che proverebbe/smentirebbe
    reviewer_id, family                # per il no-duplicati-di-famiglia
}
```
Implementazioni: `LocalDeterministicReviewer` (wrappa validators/security_critic
per assi 3-4), `LocalModelReviewer` (TEACHER sul rig), `ExternalReviewer`
(OpenAI/Claude, con redazione+consenso). Mappabile su `TrainingReviewDecision`.

### 4.2 CouncilRouter
Dato il caso + la criticita', sceglie i reviewer per **copertura degli assi**:
- ogni asse coperto da >=1 reviewer;
- cambi critici -> piu' reviewer per asse ma **bounded** e **senza duplicati di
  famiglia** (no due modelli della stessa famiglia sullo stesso asse);
- costruisce il **pacchetto per-reviewer** (cieco): evidenza condivisa + lente
  dell'asse + NIENTE verdetti altrui.

### 4.3 Aggregator
Raccoglie i verdetti per asse. Esiti:
- **concorde pass** su tutti gli assi -> candidato `verified_success` (soggetto a rerun);
- **concorde fail** su un asse -> `verified_failure` con motivo;
- **discorde** su un asse -> passa all'arbiter (non si decide a maggioranza cieca).

### 4.4 Arbiter (GLM-Colibri) — l'adjudicator
NON un voto in piu'. Sulle discordanze:
1. legge le ragioni contrastanti;
2. **genera un esperimento** (un test/prova concettuale, es. il caso "3+3");
3. l'esperimento gira nel **gate deterministico** (rerun);
4. il risultato REALE risolve la discordanza -> verdetto con evidenza.
Cosi' l'arbitrato e' basato su prova, non su autorevolezza del modello. (E'
esattamente il ruolo Tester/Red Team applicato a review-time.)

### 4.5 Capacity & Context Budgeter
- budget di token/tempo per Council e per reviewer;
- quota/capacita' per reviewer (Colibri e' lento -> unita' piccole, heartbeat);
- se un reviewer sfora o non risponde -> si degrada (coverage con gli altri),
  non si blocca il Council;
- niente promozione se il budget non ha permesso la copertura minima.

### 4.6 Redazione + consenso (per gli esterni)
- mai invio automatico di repo/file/identita' progetto;
- redazione: togli segreti, path, nomi interni; manda solo l'artefatto minimo;
- consenso esplicito per-invio, log di cosa e' uscito;
- risultato salvato come `external_review`, promozione solo dopo validazione/rerun.

---

## 5. Flusso dati e provenance
Ogni review nel `reviews.jsonl` (append-only, esistente) porta:
`axis, reviewer_id, family, verdict, confidence, reasoning, violations,
experiment (se arbiter), experiment_result, redaction_manifest (se esterno),
budget_spent`. La promozione a SFT/memoria usa SOLO verdetti `verified_*` con
questa provenance (coerente col debito P6 #10, gia' chiuso). Dopo la promozione:
**rerun degli stessi benchmark** per misurare il miglioramento reale.

---

## 6. Mapping sul codice esistente
| Serve | Esiste gia' | Nuovo |
|-------|-------------|-------|
| Pacchetto evidenza | `store.export_teacher_packet` (teacher_review_v1) | pacchetti per-reviewer/per-asse |
| Review append-only | `store.add_review` + `reviews.jsonl` + `review_queue` | assi + provenance estesa |
| Contratti review | `structured_contracts.TrainingReviewDecision`, `MethodTrace`, `LessonCandidate` | `ReviewVerdict` per-asse |
| Reviewer locale assi 3-4 | `validators.py`, `security_critic.py` | wrap in `ReviewerAdapter` |
| Arbiter esperimento | gate deterministico + gold test (`_verify_gold_tests_executed`) | generazione esperimento + rerun |
| Status/promozione | status ladder, promotion flags | logica Council -> verified_* |

---

## 7. Piano a fasi (ognuna offline-testabile con reviewer stub)
1. **Interfaccia `ReviewerAdapter` + `LocalDeterministicReviewer`** (assi 3-4 dai
   validator esistenti). Test: verdetti su casi noti.
2. **CouncilRouter + pacchetti ciechi per-asse** (deterministico, bounded,
   no-duplicati-famiglia). Test: copertura assi, no family-dup.
3. **Aggregator + rilevamento discordanza** (5 assi). Test: concorde/discorde.
4. **Arbiter**: generazione esperimento + rerun deterministico che risolve.
   Test: discordanza -> esperimento stub -> verdetto da evidenza.
5. **Capacity & Context Budgeter**: budget/quote/heartbeat, degrado su sforo.
6. **ExternalReviewer** (OpenAI/Claude) con redazione+consenso (opt-in, gated).
7. **UI Evidence Council**: pannello con pacchetti, copia guidata, import verdetti,
   stato reviewer, budget. (P7)

I ruoli-modello reali (TEACHER, GLM-Colibri, esterni) si provano sul rig; la
logica (router/aggregator/arbiter/budgeter) e' tutta testabile offline con stub,
come il mini-swarm.

---

## 8. Relazione col mini-swarm (non confondere i due lati)
- **Mini-swarm** = lato PRODUTTORE: Scaffolder/Debugger costruiscono, Tester
  verifica *durante* la generazione. Genera attempt verificati.
- **Council** = lato REVIEWER: valida gli attempt per la PROMOZIONE, multi-modello,
  cieco, con arbitro. Il Tester adversariale del mini-swarm e' il **seme locale**;
  il Council lo scala a consenso multi-modello + evidenza d'arbitrato.
Entrambi passano dagli stessi cancelli anti-contaminazione.

---

## 9. Decisioni aperte per l'owner (bloccanti solo quando ci arriviamo)
- numero massimo di reviewer concorrenti e budget di default (token/tempo);
- modello/checkpoint e parametri di GLM-Colibri (dopo benchmark);
- quali provider esterni abilitare per primi e in che formato;
- policy di redazione precisa (cosa esce, come si logga);
- criteri di "cambio critico" che fanno scattare il Council esteso;
- soglia di confidenza/consenso per saltare l'arbitro (se mai).

---

## 10. Primo passo consigliato (quando implementiamo)
Fase 1: `ReviewerAdapter` + `LocalDeterministicReviewer` (wrappa i validator gia'
esistenti come reviewer dell'asse vincoli/sicurezza). E' backend puro, offline,
zero rischio, e da' subito un Council "a un reviewer deterministico" su cui
innestare router/aggregator/arbiter. Nessun modello, nessuna VRAM.
