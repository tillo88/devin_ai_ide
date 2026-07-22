# DEVIN — Roadmap: Agent Skills + Goal Mode (v1)

Documento di studio. Non è un piano approvato: serve a mettere in fila **quali skill aggiungere**, con che priorità e dipendenze, e a **progettare la Goal Mode** multiagent per scaffold/training. Le mappature sullo stato attuale sono basate sul codice in `devin/core/` (orchestrator, change_manifest, quality gate/heal loop, training) al 2026-07-22.

Legenda stato attuale:

- **Assente** — non esiste nulla in DEVIN.
- **Parziale** — esistono mattoni riusabili ma non la skill vera e propria.
- **Presente** — c'è già, semmai va rifinita.

---

## Parte A — Agent Skills

### Quadro sintetico (ranking di partenza + mappatura DEVIN)

| # | Skill | Meglio per | Perché conta | Stato in DEVIN | Tier |
|---|-------|-----------|--------------|----------------|------|
| 1 | Browser Testing | Frontend / full-stack | Verifica flussi utente reali, non solo "compila" | Assente | 3 |
| 2 | Test-Driven Development | Feature e bugfix | Trasforma requisiti/regressioni in check eseguibili | Parziale | **1** |
| 3 | Debugging & Error Recovery | Build rotte, comportamenti imprevisti | Sostituisce edit speculativi con diagnosi strutturata | Parziale | **1** |
| 4 | Code Review & Quality | Review pre-merge | Trova correttezza, manutenibilità, problemi di scope | Parziale | **1** |
| 5 | Security & Hardening | Auth, API, dati sensibili | Threat modeling e check prima del rilascio | Assente | 2 |
| 6 | Composio App Integration | Collegare agenti ad app esterne | Gestisce tool, auth, workflow d'integrazione | Assente | 3 |
| 7 | Incremental Implementation | Feature multi-file e refactor | Cambiamenti piccoli, testabili, recuperabili | Parziale | **1** |
| 8 | API & Interface Design | API pubbliche, confini tra moduli | Evita contratti fragili ed errori incoerenti | Assente | 2 |
| 9 | Performance Optimization | App lente, regressioni | Richiede misura prima di ottimizzare | Assente | 2 |
| 10 | Documentation & ADRs | Sistemi longevi, handoff di team | Preserva decisioni che il codice da solo non spiega | Parziale | 2 |

Il "Tier" è la **mia** proposta di priorità per DEVIN (non il ranking originale): Tier 1 = costruisce sulle fondamenta che DEVIN ha già ed è ad alto ritorno; Tier 2 = valore alto ma richiede più infrastruttura nuova; Tier 3 = dipende da capacità che oggi DEVIN non ha affatto (browser, integrazioni esterne).

### Dettaglio per skill

**#2 Test-Driven Development — Tier 1**
Stato: DEVIN ha già un *quality gate* che esegue i test e un *heal loop* (`_scaffold_quality_gate`, `_scaffold_heal_loop`) che ricicla finché la suite non passa. Manca il pezzo "TDD vero": derivare i test **dai requisiti prima** di scrivere l'implementazione.
Cosa aggiungere: una skill che, dato un requisito, genera prima i test (rossi), poi implementa fino al verde. Riusa il gate esistente come oracolo.
Dipendenze: nessuna nuova infrastruttura. Si aggancia a `run_scaffold`.

**#3 Debugging & Error Recovery — Tier 1**
Stato: c'è già retry (`max_retries`) e propagazione errori nel loop. Manca la **diagnosi strutturata**: ipotesi → riproduzione → bisezione → fix mirato, invece di ritentare "a naso".
Cosa aggiungere: skill che, su build rossa, produce un report diagnostico (stack, ipotesi ordinate, minimal repro) e propone un fix isolato prima di toccare il codice.
Dipendenze: log strutturati dei run (già presenti nella timeline eventi).

**#4 Code Review & Quality — Tier 1**
Stato: il quality gate copre "passa/non passa", non la review qualitativa (scope creep, manutenibilità, naming, confini).
Cosa aggiungere: skill di review che gira sul `change_manifest` verificato **prima** dell'approvazione e allega un giudizio strutturato al banner di approvazione. Sinergia diretta con l'awaiting_approval già esistente.
Dipendenze: change_manifest (presente).

**#7 Incremental Implementation — Tier 1**
Stato: il `change_manifest` rende già i cambiamenti rivedibili e recuperabili; la modalità "diff prima di applicare" spinge verso diff piccole.
Cosa aggiungere: skill che spezza esplicitamente una feature multi-file in step atomici, ciascuno con la sua verifica, così ogni step è un manifest approvabile a sé.
Dipendenze: nessuna nuova; è orchestrazione sopra ciò che c'è. **È anche il ponte naturale verso la Goal Mode** (vedi Parte B).

**#10 Documentation & ADRs — Tier 2**
Stato: c'è `docs_cache` e la promozione in memoria solo di outcome verificati; manca la produzione di ADR ("perché abbiamo deciso X").
Cosa aggiungere: skill che, a fine run verificato, emette un ADR sintetico nel progetto. Si aggancia a `_remember_scaffold_outcome`.

**#5 Security & Hardening — Tier 2** · **#8 API & Interface Design — Tier 2** · **#9 Performance Optimization — Tier 2**
Assenti oggi. Sono skill "specialista" che girano come pass dedicati su un diff/modulo. Vanno bene come skill selezionabili dal menu **⚡ Skill** del composer. Performance richiede prima uno strato di **misura** (benchmark/profilo) per rispettare il principio "misura prima di ottimizzare".

**#1 Browser Testing — Tier 3** · **#6 Composio App Integration — Tier 3**
Richiedono capacità che DEVIN non ha: un browser controllabile (tipo WebBridge) e un layer di integrazione app esterne con auth. Sono progetti a sé, non "skill" da agganciare al loop attuale. Da tenere in fondo finché non serve full-stack/integrazioni.

### Come si agganciano al menu ⚡ Skill
Oggi le voci Skill del composer sono **preset di prompt**. Il percorso di maturazione:

1. **Ora**: preset di prompt (fatto).
2. **Prossimo**: ogni skill Tier 1 diventa una *procedura* con contratto chiaro (input → passi → verifica), non solo un prompt. Es. "Scrivi i test" applica il ciclo TDD col gate come oracolo.
3. **Dopo**: le skill diventano invocabili anche dalla Goal Mode come sotto-obiettivi.

---

## Parte B — Goal Mode per DEVIN (multiagent, scaffold + training)

### Cos'è (dal modello Kimi Work, adattato)
Passaggio da "completo un singolo passo per te" a "lavoro in continuo verso un obiettivo definito". L'utente fornisce **obiettivo + criteri di accettazione + vincoli**; l'agente cicla — pianifica, agisce, verifica, valuta lo stato (fatto / bloccato / cambio strategia) — senza dover ri-avviare la conversazione. L'utente rivede i progressi, corregge la direzione e lo fa proseguire.

### Perché DEVIN è già a metà strada
DEVIN ha i mattoni giusti:

- **Loop plan → act → verify → gate** in `orchestrator.run` / `run_scaffold`.
- **Heal loop** che ricicla finché il quality gate non passa.
- **Retry** con `max_retries`.
- **change_manifest + awaiting_approval**: punto di checkpoint umano.
- **Training** (seed/attempt/review con validazione Teacher): serve valutazione di outcome.

Manca lo **strato "obiettivo"** sopra il singolo run: qualcosa che tenga l'obiettivo e i criteri, decida *quando* un run è concluso, e concateni più run/step (potenzialmente su più agenti) fino al successo o al blocco.

### Architettura proposta (bozza)

```
Goal
 ├─ objective: testo ("trova e correggi tutti i bug nei test, verde pieno")
 ├─ acceptance_criteria: lista verificabile (suite verde, nessun TODO, diff applicata)
 ├─ constraints: vincoli (non toccare file X, budget step, diff-prima-di-applicare)
 └─ loop:
      1. PLAN     -> scompone in step (usa skill "Incremental Implementation")
      2. DISPATCH -> assegna lo step a un agente/ruolo (scaffold | debug | review | test)
      3. RUN      -> orchestrator.run(step)  (loop plan/act/verify/gate esistente)
      4. EVALUATE -> criteri soddisfatti? bloccato? cambio strategia?
      5. CHECKPOINT -> se lo step produce modifiche: change_manifest -> awaiting_approval
      6. ripeti finché acceptance_criteria tutti veri OR budget esaurito OR blocco umano
```

### Aggancio al multiagent (scaffold + training)
- **Ruoli** come specializzazioni della stessa orchestrazione: `scaffolder`, `debugger`, `reviewer`, `tester`. Ognuno è un run con la skill Tier 1 corrispondente. Il DISPATCH sceglie il ruolo in base allo step.
- **Scaffold**: la Goal Mode è il direttore d'orchestra che porta un progetto nuovo da vuoto a "suite verde + manifest approvato", concatenando scaffolder → tester → reviewer.
- **Training**: ogni EVALUATE produce un *attempt* valutabile; gli esiti verificati alimentano la pipeline di training esistente (seed/attempt/review con Teacher). La Goal Mode diventa così anche un **generatore di dati di training** con etichette di outcome reali.

### Criteri, vincoli, stop
- **Acceptance criteria**: devono essere **verificabili a macchina** (gate verde, assenza di pattern, manifest applicato). Criteri vaghi = loop infinito.
- **Vincoli**: budget di step/tempo, whitelist/blacklist file, obbligo di approvazione umana sui manifest (riusa awaiting_approval).
- **Stop**: successo (tutti i criteri veri) · blocco (nessun progresso dopo N step → chiede all'umano) · budget esaurito. **Mai** auto-applicare modifiche saltando il checkpoint, se la modalità sicurezza è attiva.

### Piano di implementazione (fasi)
1. **Goal object + valutatore di criteri** (solo backend): struttura dati + funzione che valuta i criteri contro lo stato del progetto. Testabile offline.
2. **Loop mono-agente**: Goal che concatena run dello stesso tipo fino ai criteri, con checkpoint awaiting_approval. Nessun nuovo modello, riusa l'orchestrator.
3. **DISPATCH multi-ruolo**: scelta del ruolo/skill per step (Tier 1 skill come esecutori).
4. **UI**: pannello Goal (obiettivo + criteri + vincoli + progressi), agganciato alla voce 🎯 Goal del composer e al command center in alto.
5. **Loop training**: EVALUATE → attempt etichettati nella pipeline Teacher.

### Domande aperte (da decidere insieme)
- I criteri di accettazione: linguaggio libero interpretato dal modello, o DSL/checklist strutturata? (propendo per checklist strutturata + verifica a macchina).
- Budget di default (step/tempo) e comportamento al blocco: chiede sempre o prova un cambio strategia prima?
- Multiagent: agenti come processi/modelli distinti o come ruoli logici sullo stesso modello con prompt/strumenti diversi? (per iniziare: ruoli logici).
- Rapporto con la modalità "diff prima di applicare": la Goal Mode si ferma a ogni manifest o accumula e chiede un'approvazione unica a fine obiettivo?

---

## Sequenziamento consigliato
1. Skill **Tier 1** (TDD, Debugging, Code Review, Incremental) — costruiscono sulle fondamenta e sono i futuri esecutori della Goal Mode.
2. **Goal Mode fasi 1–2** (backend + loop mono-agente) — riusa orchestrator e checkpoint.
3. **Goal Mode fasi 3–5** (multi-ruolo + UI + training).
4. Skill **Tier 2** come pass specialisti selezionabili.
5. Skill **Tier 3** (Browser, Composio) solo quando servono full-stack/integrazioni: sono progetti a sé.
