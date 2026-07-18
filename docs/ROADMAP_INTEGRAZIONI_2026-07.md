# Roadmap integrazioni esterne — analisi 2026-07-15 (Claude)

Analisi di: video "Agent Loops Clearly Explained", Loop Library (Forward
Future, 85 loop + skill Loopy), repo `agent-skills-for-context-engineering`
(Koylan, 15 skill), lista MCP/tool proposta. Filtro: SOLO local-first, niente
cloud/API. Criterio: cosa dà un "più" reale a DEVIN dato quello che ESISTE GIÀ
(gate+gold, review queue, memoria a stati, sandbox, vector store, Crawl4AI,
Playwright) e la direzione (Codex-like locale + simbiosi rig).

## Cosa abbiamo GIÀ (non duplicare)

| Proposta esterna | Già coperto da |
|---|---|
| Memory MCP generici | memoria a stati anti-contaminazione (NOSTRA, migliore: non toccare) |
| Vector DB (Qdrant/LanceDB) | VectorStore locale (sklearn/sentence-transformers) + Qdrant su rig (AutoMem). Upgrade = nice-to-have, non priorità |
| Docker/Sandbox MCP | `project_sandbox` (policy anti-danno, link_venv). Docker = isolamento più forte, ma dopo |
| Playwright MCP | playwright già usato direttamente (web_search/fetch) |
| "Evaluation/harness" skills | quality gate + gold tests + review queue + statuses: è GIÀ harness engineering applicato |
| Semantic codebase search | context_retriever + files_index |

## Il "PIÙ" reale, in ordine di valore/sforzo

### 1. Critic deterministico multi-linguaggio (tree-sitter) — PICCOLO, SUBITO
Oggi il gate fa `compile()` = solo Python. `tree-sitter` (pip, locale, zero
modelli) dà AST check per JS/TS/Rust/HTML/qualunque cosa il Coder generi:
sintassi rotta → reject + errore al Critic PRIMA che l'utente veda il file.
Stesso pattern del gate attuale, si aggiunge come tier-1 validator.
Bonus collegato: **repo map stile Aider** (firme funzioni/classi via
tree-sitter) per il context_engine sui progetti grandi — contesto più denso
con meno token (il nostro ctx è 8K!).

### 2. Semgrep locale nel gate/Critic — PICCOLO-MEDIO
`pip install semgrep`, gira offline con regole pubbliche. Terzo occhio
deterministico: vulnerabilità e anti-pattern sul codice generato, output
strutturato che diventa evidenza nel gate (stesso posto dei gold). È
esattamente il "tier 1: deterministic validators" della nostra architettura
di review. Contro: pacchetto pesante, scan lento su progetti grandi → solo
sui file scritti dal run.

### 3. LOOP MODE (il concetto del video + Loop Library) — MEDIO, IL PIÙ "RIVOLUZIONARIO"
Il video dice: non promptare l'agente, progetta il loop = trigger + azione +
**stop condition misurabile**. DEVIN oggi fa run one-shot con max 3 retry.
Un "loop mode" generalizza: obiettivo oggettivo + verifica automatica (il
nostro gate È il verificatore) + stop (streak di N verdi / coverage ≥ X / max
iterazioni / budget tempo). Esempi immediati coi pezzi che abbiamo:
- "quality streak": gira il bench finché N casi consecutivi verdi (= la Loop
  Library, loop 'quality streak', 1:1 col nostro training);
- "coverage loop": aggiungi test finché coverage ≥ X% (pytest-cov locale);
- "docs sweep" notturno sul progetto.
La Loop Library in sé è un catalogo di PROMPT con stop condition: si importa
come template testuali (file loops/*.md nel repo), non serve la loro infra.
ATTENZIONE (dal video stesso): loop 24/7 senza capire = scala i bug. Ogni
loop deve avere gate bloccante + limite iterazioni + log durevole. Mai loop
che scrivono in memoria recall-safe.

### 4. Docs locali per il Coder (variante local di Context7) — MEDIO
Il problema che Context7 risolve (API allucinate/datate) l'abbiamo VISTO nel
bench (endpoint Steam inventati ×2). Versione local-first: cache di doc
ufficiali per libreria (Crawl4AI già installato) in knowledge dedicata +
iniezione mirata quando il Coder importa la lib o l'errore la riguarda
(estensione di `_maybe_web_reference` che già esiste). Niente servizio
esterno, la doc si scarica una volta e si versiona.

### 5. Skills repo (Koylan) — LINEE GUIDA, non codice
Il repo è eccellente ma è *metodologia*: progressive disclosure, context
degradation, compaction, filesystem-context, harness con novelty gate e
rollback. Uso giusto per noi: (a) checklist quando tocchiamo context_engine
(budget 8K = context engineering serio); (b) più avanti, dare a DEVIN un
sistema di SKILL.md progressive-disclosure suo (si sposa con le skill cards
ForgeStudio già in visione). Da NON fare: BDI ontology, latent-briefing
(KV cross-agent, research-y), hosted-agents (cloud).

### 6. MCP client dentro DEVIN — DOPO, quando serve davvero
Farlo bene = orchestratore MCP nel layer Rust di Tauri (consiglio corretto:
socket/processi in Rust, non nel JS). Ma oggi ogni capability proposta via
MCP ce l'abbiamo nativa (fs, browser, sandbox, git). Il momento giusto è
quando integriamo AutoMem via MCP (già in visione) — a quel punto un client
MCP unico apre anche docker-sandbox e altro. Skip totale: GitHub MCP, Linear/
Jira, Sentry/Datadog, Greptile/Sourcegraph, SonarQube (tutti cloud).

## Ordine operativo proposto

0. **ORA**: finire il ciclo MBPP — run dei 10 (dropdown fixato), review mie →
   import, batch da 30, pass-rate come baseline.
1. Tree-sitter AST gate (+ repo map light nel context_engine).
2. Semgrep nel gate (opzionale via settings, default on per i run training).
3. Loop mode nell'orchestratore + primi 3 loop template (streak, coverage,
   docs sweep). Import prompt utili dalla Loop Library come file.
4. Docs cache locale per-libreria + iniezione mirata nel Coder.
5. (Con l'integrazione AutoMem-MCP) client MCP in Rust + docker sandbox.

Regola fissa trasversale: NIENTE tocca la memoria strutturata a stati — ogni
nuovo validatore/loop produce evidenza che passa dagli stessi cancelli
(auto_* → review → verified_*).
