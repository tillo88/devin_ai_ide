# Context Steward — audit e piano di realizzazione

Data: 2026-07-21. Fonte dell'idea: `docs/Context Steward.txt` (conversazione di
design owner). Questo doc converte l'idea in un piano stratificato, testabile e
ancorato al codice esistente.

## 1. Verdetto dell'audit

Context Steward NON e' un componente nuovo da zero: e' la formalizzazione ed
espansione di `devin/core/chat_continuity.py`, che gia' implementa il ~60% del
nucleo deterministico (stesso schema `chat_continuity_v1`, `estimate_tokens`,
`history_fingerprint`, `should_checkpoint`, `checkpoint_needs_refresh`,
`build_checkpoint` bounded/fingerprintato, `context_from_checkpoint` con regola
anti-promozione, fallback verbatim). Si colloca dentro P4 (context engine) e
P5 (memoria anti-contaminazione) della roadmap 2026-07-20.

Da tenere (forte): "mai sostituire l'evidenza col riassunto"; deterministico
sempre attivo + LLM solo a soglia; NVMe come archivio evidenze con retrieval
selettivo (link != contenuto in KV); separazione netta Steward/AutoMem/
Understory (Steward = operativa effimera, alimenta le altre solo via promozione
validata dall'orchestratore).

Da rifinire: soglie/isteresi/cooldown come CONFIG calibrata, non hardcoded;
niente livello "long-term" nuovo (e' AutoMem); pannello derivato dal core, non
seconda macchina a stati; token reali dal backend (llama.cpp `n_past`) quando
disponibili, stima come fallback. Da scartare: nulla di sostanziale.

Rischio principale: scope creep. Mitigazione: le fasi sotto, ognuna con DoD e
suite verde, nessuna dipende dalla successiva per dare valore.

## 2. Confini invariabili (non negoziabili)

1. Lo Steward NON scrive nel progetto e NON prende decisioni tecniche.
2. Lo Steward NON promuove nulla in AutoMem/Understory da solo: propone, e
   l'orchestratore valida.
3. Mai sostituire evidenza verificabile con un riassunto: il checkpoint tiene
   riferimenti content-addressed, non la prosa al posto della prova.
4. Ogni checkpoint: bounded, versionato, fingerprintato, incrementale,
   collegato al precedente (`supersedes`), reversibile.
5. Determinismo prima di tutto: il core gira senza GPU e senza LLM; il modello
   leggero interviene solo a soglia/confine task, mai in continuo.

## 3. Le memorie (chiarite, senza duplicare)

- Evidence archive (NVMe, immutabile): log, diff, risultati test, manifest,
  checksum, comandi, output grezzi. Nel contesto entrano SOLO riferimenti.
- Active working set (in-prompt): obiettivo, vincoli, stato, ultima evidenza,
  prossimo passo, rischi aperti.
- Rolling continuity checkpoint: `chat_continuity_v1` esteso (gia' esistente).
- Long-term: NON un componente Steward nuovo -> e' AutoMem (semantica promossa)
  + Understory (federata). Lo Steward vi arriva solo via promozione validata.

## 4. Fasi (ognuna con DoD; nessun "success" senza prova)

### CS0 — Nucleo deterministico esteso (quasi gratuito, no GPU)
Estendere `chat_continuity.py` (o nuovo `context_steward.py` che lo avvolge)
con: macchina a stati di pressione con ISTERESI e COOLDOWN da config; loop
guard a fingerprint (stesso task/proposta/errore/comando senza nuova evidenza
-> niente nuovo checkpoint/task); contatore max-compattazioni-per-task.
- Config sotto `chat.continuity` / nuovo `context_steward` in settings.json,
  con default conservativi documentati come "da calibrare".
- DoD: unit test su transizioni di stato, isteresi (niente flapping), cooldown,
  loop guard (fingerprint uguale -> no-op), max compattazioni. Suite verde.

### CS1 — Evidence archive con riferimenti (NVMe-ready)
Store append-only su disco (JSONL + indice), evidenze content-addressed
(SHA-256). Il checkpoint referenzia `evidence_id`/path, mai il contenuto.
- Path base configurabile (default repo `logs/steward/`; sul rig
  `/mnt/.../operational/devin/sessions/<id>/`).
- DoD: scrittura/lettura idempotente, ricostruzione byte-for-byte via hash,
  test che il checkpoint non inglobi mai il corpo dell'evidenza.

### CS2 — Retrieval ibrido
Tre vie: lookup esatto (run id/hash/file), query strutturata (stato=verified,
model=…, test=…), semantica (riuso del VectorStore esistente). Restituisce
frammenti piccoli, non file interi.
- DoD: test che una query mirata restituisce O(centinaia) di token, non O(10^4);
  precedenza al lookup esatto per id/hash.

### CS3 — Superficie osservabile (pannello) + SSE
Stato derivato dal core (IDLE/WATCHING/PREPARING/COMPACTING/VERIFYING),
findings/evidence-preserved/actions/risks come da documento. Read-only.
- DoD: il pannello non ha stato proprio (deriva dal core); e2e via TestClient.

### CS4 — Compattazione LLM a confine (solo qui entra il modello leggero)
Il modello economico interviene solo a: soglia, fine sottotask, cambio
obiettivo, output tool grande, pre-shift/reset, richiesta orchestratore.
Produce checkpoint proposto -> orchestratore valida -> eventuale promozione.
- DoD: nessuna compattazione senza trigger esplicito; il checkpoint proposto e'
  bounded e supera il gate di validazione; niente promozione automatica.

### CS5 — Stabilita' KV-cache
Struttura di prompt stabile: [system][regole progetto][checkpoint corrente]
[retrieval del turno][ultimi messaggi]. Retrieval in zona prevedibile, rimosso
al turno successivo se non serve.
- DoD: test/telemetria che il prefisso stabile non cambia tra turni senza
  motivo; il retrieval non viene rimescolato in cima.

## 5. Integrazione con AutoMem/Understory (risposta al dubbio owner)

Flusso: `chat/tool output -> Context Steward -> checkpoint operativo ->
orchestratore valida -> promozione (AutoMem | Understory)`. Vietato:
`Steward compatta -> salva tutto in AutoMem`. Cosi' memorie temporanee e
ipotesi non contaminano la memoria permanente. AutoMem/Understory restano
fail-soft e indipendenti: se il rig e' giu', lo Steward locale continua a
lavorare sul checkpoint operativo senza toccarle.

## 6. Priorita' e ordine di build

CS0 -> CS1 -> CS2 -> CS3 -> CS4 -> CS5. CS0 e CS1 danno gia' valore da soli
(sessioni lunghe senza trascinare token grezzi, con evidenza su disco). CS4
(l'unico pezzo che consuma inferenza) arriva quando il resto e' solido.

## 7. Stato realizzazione (2026-07-22)

- CS0 FATTO: `devin/core/context_steward.py` (11 test).
- CS1 FATTO: `devin/core/evidence_archive.py` (7 test).
- CS2 FATTO: `devin/core/evidence_retriever.py` (5 test).
- Coordinatore FATTO: `devin/core/steward_coordinator.py` - snapshot derivato
  (6 test).
- CS3 FATTO: `GET /api/steward/status` (read-only, derivato dal core) + badge
  pannello fail-soft "contesto NN% · stato" (e2e test).
- CS4 (compattazione LLM a confine) e CS5 (stabilita' prefisso KV): DA FARE,
  richiedono il modello vivo (rig o llama locale) e osservazione dell'owner.
  Vanno wired nel chat loop dove oggi gira `chat_continuity` (chat.py ~L435).
  Suite complessiva: 466 passed.
