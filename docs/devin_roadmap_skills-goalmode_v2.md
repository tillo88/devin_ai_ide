# DEVIN — Roadmap: Goal Mode + Multiagent (v2)

Aggiorna la v1 con le decisioni prese dopo la review (2026-07-22) e con il modello
multiagent adattato dal documento *Kimi Agent Swarm* — ridotto a scala locale
(rig singolo, ~16 GB VRAM), non 300 agenti. La v1 resta valida per la parte
**Agent Skills** (le 10 skill, tier, mappatura). Qui si blocca il design della
Goal Mode e del mini-swarm.

---

## Decisioni bloccate

- **D1 — Criteri di accettazione = checklist verificabile a macchina.**
  Niente solo-testo-libero. Ogni criterio è un item controllabile in automatico:
  gate verde, assenza di pattern (TODO/FIXME), manifest applicato, file esistono,
  suite passa, exit code 0. L'obiettivo in linguaggio naturale viene *tradotto*
  in questa checklist prima di partire.

- **D2 — Sul blocco: prova cambi di strategia (non chiedere subito).**
  Lo scopo è anche **imparare**: più cambi → più tentativi → più memoria. Ogni
  tentativo (successo o fallimento) è un *attempt etichettato* che alimenta la
  pipeline di training Teacher. Si escala all'umano solo quando il budget
  (step/tempo) è esaurito o si ripete lo *stesso* fallimento identico N volte.

- **D3 — Multiagent = mini-swarm locale** (dettaglio sotto): orchestratore +
  pochi ruoli specializzati, autonomia circoscritta, self-healing, verifica a
  consenso. Scala **limitata dalla VRAM**, non 300 agenti.

- **D4 — Politica di approvazione dipende dalla modalità:**
  - **Scaffold (progetto nuovo/vuoto): loop autonomo, nessuno stop per manifest.**
    È il punto: costruire senza sorveglianza fino ai criteri. Rispetta comunque
    budget e condizioni di stop.
  - **Maintenance / progetto già avviato: checkpoint `awaiting_approval` attivo**,
    con **toggle per-goal "auto-approva"** (come quando lavori con me e scegli
    l'automatico). Default = approvazione manuale; l'utente può passare ad auto.

---

## Goal Mode — design

### Struttura dell'obiettivo
```
Goal
 ├─ objective: testo ("scaffolda un parser CSV con test verdi")
 ├─ acceptance: checklist verificabile a macchina        (D1)
 │     - suite test: PASS
 │     - nessun TODO/FIXME nel codice generato
 │     - entrypoint esegue con exit 0
 ├─ constraints: budget_step, budget_tempo, whitelist/blacklist file,
 │               approval_policy = auto | manual   (D4, dipende da modalità)
 └─ mode: scaffold | maintenance
```

### Loop
```
1. TRANSLATE  -> obiettivo NL => checklist verificabile         (D1)
2. PLAN       -> scomposizione in step atomici (skill Incremental)
3. DISPATCH   -> assegna lo step a un RUOLO (mini-swarm)         (D3)
4. RUN        -> orchestrator.run(step)  [loop plan/act/verify/gate esistente]
5. EVALUATE   -> checklist soddisfatta? bloccato? -> cambia strategia (D2)
6. CHECKPOINT -> scaffold: prosegui senza stop
                 maintenance: change_manifest -> awaiting_approval
                              (salta lo stop se approval_policy = auto)   (D4)
7. RECORD     -> ogni tentativo = attempt etichettato -> training Teacher (D2)
8. ripeti finché acceptance tutta verde OR budget esaurito OR blocco ripetuto
```

---

## Mini-swarm locale (adattato da Kimi Agent Swarm)

Prendo i **principi** dello swarm e li scalo al rig singolo.

### Cosa tengo del modello Kimi
- **Orchestratore centrale ("CEO")**: scompone, alloca, valuta, aggrega. In DEVIN
  è il direttore della Goal Mode.
- **Ruoli specializzati al volo**: come i "Ricercatori/Analisti/Programmatori/
  Verificatori" di Kimi, ma declinati sul codice.
- **Autonomia circoscritta**: i ruoli **rispondono solo all'orchestratore**, non
  chiacchierano tra loro. Fondamentale in locale: niente loop, niente spreco di
  token/VRAM.
- **Self-healing**: ruolo fallisce → l'orchestratore riassegna o cambia strategia
  (= D2 + heal loop già esistente).
- **Verifica a consenso** (Red Team / terzo arbitro): per gli step critici di
  correttezza, un ruolo Verificatore arbitra esiti discordanti.

### Cosa cambio (scala locale)
- **Niente 300 agenti.** La scala è **limitata dalla VRAM**: i ruoli girano in
  **sequenza** o in **parallelismo minimo** (1–2) a seconda di quanto entra nel
  rig. "Adattivo" qui significa: pochi ruoli per goal semplici, qualcuno in più
  per goal complessi, con un **tetto** esplicito.
- **Ruoli logici, non processi/modelli separati** (per iniziare): stesso modello,
  prompt + strumenti + skill diversi per ruolo. Si può evolvere a modelli distinti
  se il rig lo permette.

### Ruoli (mappati alle skill Tier 1 della v1)
| Ruolo | Skill Tier 1 | Compito |
|-------|-------------|---------|
| Scaffolder | Incremental Implementation | Costruisce da zero, step atomici |
| Tester | Test-Driven Development | Scrive/esegue test, oracolo del gate |
| Debugger | Debugging & Error Recovery | Diagnosi strutturata su build rossa |
| Reviewer | Code Review & Quality | Giudizio qualitativo sul manifest |
| Verificatore | (trasversale) | Consenso/arbitro su correttezza |

### Flusso (adattato)
```
[ Goal ]
     │
     ▼
┌───────────────────────────┐
│ Orchestratore (Goal dir.) │◄──── EVALUATE + QA (checklist, consenso)
└──────────┬────────────────┘              ▲
           │ DISPATCH (1 step, 1 ruolo)    │ report (solo all'orchestratore)
           ▼                               │
   [ Ruolo: scaffolder | tester | ... ] ───┘
   (sequenziale o parallelismo minimo, cap da VRAM)
```

### Aggancio a training e memoria
Ogni EVALUATE produce un attempt con esito reale (verde/rosso, criterio
soddisfatto o no). Questi attempt:
- alimentano la pipeline **Teacher** (seed/attempt/review) già esistente;
- diventano **memoria** solo se verificati (riusa `_remember_scaffold_outcome`,
  che già promuove in memoria solo outcome testati).
Così il mini-swarm è anche un **generatore di dati di training etichettati** —
coerente con D2 (più prove = più memoria).

---

## Piano di implementazione (fasi, rivisto)

1. **Goal object + valutatore checklist** (backend, offline-testable). Traduce
   obiettivo → checklist e la valuta contro lo stato del progetto. (D1)
2. **Loop mono-ruolo**: orchestratore concatena run di un solo ruolo fino ai
   criteri. Scaffold = nessuno stop; maintenance = checkpoint + toggle auto. (D4)
3. **DISPATCH multi-ruolo** + autonomia circoscritta + self-healing con cambi di
   strategia. (D2, D3)
4. **Verifica a consenso** sugli step critici di correttezza.
5. **UI Goal**: pannello obiettivo + checklist + vincoli + progressi live +
   toggle politica approvazione, agganciato a 🎯 Goal e al command center in alto.
6. **Loop training**: attempt etichettati → Teacher.

### Primo passo consigliato (basso rischio)
Fase 1: **oggetto Goal + valutatore di checklist**, puro backend, testabile
offline senza modelli né VRAM. È la fondazione su cui poggia tutto il resto e
non tocca la UI né l'orchestrator esistente.

---

## Vincoli operativi da non perdere di vista
- Tutto gira su rig locale: la **VRAM è il vincolo duro**; il cap di parallelismo
  dei ruoli è una scelta di progetto, non un dettaglio.
- La modalità sicurezza "diff prima di applicare" resta valida in maintenance; in
  scaffold il loop autonomo è esplicitamente voluto (D4), ma resta soggetto a
  budget e stop.
- Nessun ruolo scrive in memoria "buona" senza verifica (policy anti-contaminazione
  già in DEVIN).
