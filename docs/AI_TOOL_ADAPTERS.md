# DEVIN AI Tool Adapters

Questa nota traccia l'integrazione ordinata dei tool selezionati dal video/link:

- Instructor: structured output + validation/retry.
- Crawl4AI: web/docs ingestion LLM-ready.
- DSPy: ottimizzazione pipeline su benchmark, dopo raccolta dati.
- Outlines: constrained generation locale, opzionale dopo test.

## Stato attuale

### Instructor-style structured contracts

Aggiunto `devin.ai.structured_contracts` con modelli Pydantic:

- `TrainingReviewDecision`
- `MethodTrace`
- `LessonCandidate`
- `CrawlKnowledgeRecord`

Sono usabili subito senza installare Instructor. Quando Instructor sarà installato, questi stessi modelli possono diventare `response_model` per estrazione/review con retry automatico.

Uso target:

- normalizzare review Teacher/Colibrì;
- classificare failure mode;
- salvare method trace verificabile;
- generare lesson candidate senza auto-promozione.

### Crawl4AI optional adapter

Aggiunto `devin.ai.crawl_ingestion` e API:

- `GET /api/project/knowledge/crawl/status`
- `POST /api/project/knowledge/crawl`

Modalità:

- `auto`: prova Crawl4AI, fallback al fetch base;
- `crawl4ai`: richiede Crawl4AI;
- `basic`: usa il fetch leggero esistente.

La validazione anti-SSRF resta obbligatoria nell'API: solo `http/https`, hostname risolvibile, niente IP privati/loopback/link-local/reserved.

## Installazione futura

Non obbligatoria per ora:

```bash
pip install instructor
pip install -U crawl4ai
crawl4ai-setup
```

Per WSL/Windows, Crawl4AI potrebbe richiedere Playwright/Chromium:

```bash
python -m playwright install chromium
```

## Perché non DSPy/Outlines subito

DSPy rende molto quando hai benchmark e metriche: prima raccogliamo attempt/review/teacher packet, poi ottimizziamo pipeline.

Outlines è utile per constrained generation locale, ma è più invasivo nel path di inferenza. Lo valuteremo dopo test concreti su modelli locali che producono JSON/schema sporchi.

## Stato installazione locale 2026-07-15

Installati nel `venv` del progetto:

- `instructor==1.15.4`
- `crawl4ai==0.9.2`

Verifiche eseguite:

```bash
venv/bin/python -m pip check
venv/bin/crawl4ai-doctor
```

Risultato: nessun requisito rotto; Crawl4AI doctor ha completato un crawl reale di `https://crawl4ai.com`.

## Flusso operativo aggiunto

- `/api/training/reviews/structured` accetta una `TrainingReviewDecision` validata via Pydantic e la salva come review append-only. È il punto di ingresso per output Teacher/Colibrì generati con Instructor.
- La Command Palette della `/app` include “Crawl URL nella knowledge”, che usa `/api/project/knowledge/crawl` in modalità `auto` sul progetto selezionato.
