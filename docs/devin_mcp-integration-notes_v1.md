# DEVIN — integrazione MCP: decisione e shortlist (v1)

**Data:** 2026-08-01 · **Stato:** appunto/decisione tecnica. Nessuna implementazione.
**Gate:** dopo KVarN e dopo i lavori gia' in corso (Council, interlock, Clippy).

Promemoria di cosa abbiamo deciso sugli MCP server, per non ri-derivarlo.

---

## 1. Punto di partenza: DEVIN parla gia' MCP, ma a meta'

`devin/ai/understory_client.py` implementa **MCP Streamable HTTP** a mano:
`initialize` -> `notifications/initialized` -> `tools/call`, con `Mcp-Session-Id`
e parsing di risposte SSE.

Limiti attuali (verificati sul codice):

```text
transport      solo HTTP        (niente stdio)
server         uno solo, hardcoded (Understory)
discovery      assente          (niente tools/list)
sessione       ri-inizializzata a ogni chiamata
```

Conseguenza: **la stragrande maggioranza dei server MCP pubblici gira stdio**
(`npx`/`uvx`), quindi oggi **non si attacca**. Ogni server nuovo, senza lavoro
strutturale, diventerebbe un altro client hand-rolled come `understory_client.py`.

## 2. Decisione: bridge stdio -> HTTP (non riscrivere il transport)

**Scelta: mettere davanti un bridge che espone i server stdio su HTTP**
(es. `mcpo` / `supergateway`), invece di implementare il transport stdio dentro
DEVIN.

Perche':

- **riusa il transport HTTP che gia' funziona** (quello di Understory), che e'
  la parte gia' provata;
- il bridge e' un **processo esterno**, coerente col pattern che usiamo per
  SearXNG/OCR/Colibri: servizio separato, non roba nel loop del modello;
- si puo' spegnere/limitare per-ruolo senza toccare il backend;
- niente gestione di sottoprocessi, pipe e lifecycle dentro `devin-backend`.

Resta comunque da fare, lato DEVIN, il minimo indispensabile:

1. **`tools/list`** (discovery) — oggi assente;
2. **config multi-server** in `settings.json` (per-macchina, per-ruolo), con
   **allowlist esplicita**;
3. riuso della sessione MCP invece di re-`initialize` a ogni chiamata;
4. **budget per-tool** e fail-soft (un server giu' non blocca il run).

## 3. Shortlist — da adottare (tutti locali, self-hosted)

| Server | Perche' |
| --- | --- |
| [`dagger/container-use`](https://github.com/dagger/container-use) | Ambienti containerizzati per agenti, isolati in container + git branch freschi. Calco esatto del mini-swarm e della worktree-isolation di Clippy. Chiude il debito "Docker sandbox = roadmap". |
| [`pydantic/pydantic-ai/mcp-run-python`](https://github.com/pydantic/pydantic-ai/tree/main/mcp-run-python) | Esecuzione Python sandboxed dal team Pydantic (gia' in stack). Per il Runner. |
| [`capsulerun/bash`](https://github.com/capsulerun/bash/tree/main/packages/bash-mcp) · [`mavdol/capsule`](https://github.com/mavdol/capsule/tree/main/integrations/mcp-server) | Sandbox **WebAssembly** per comandi/codice non fidati. Piu' leggero di Docker e senza il rischio "Docker si prende tutti i core" gia' vissuto sul rig. |
| [`oraios/serena`](https://github.com/oraios/serena) | Code intelligence via **language server** (operazioni simboliche invece di leggere file). Alternativa a CodeGraph. **Da misurare con Ornith, non assumere.** |

## 4. Shortlist — da studiare, non installare (prior art)

| Progetto | Cosa rubare |
| --- | --- |
| [`elhamid/llm-council`](https://github.com/elhamid/llm-council) | Deliberazione multi-LLM con **peer review anonimizzata** in 3 stadi (risposte parallele -> ranking anonimo -> sintesi). E' il principio "cieco" del nostro Council implementato da altri: leggerlo **prima** di scrivere l'Aggregator. |
| [`avansaber/tailtest-cline`](https://github.com/avansaber/tailtest-cline) | Test avversariali che classificano i fallimenti in `real_bug / environment / test_bug` — stessa distinzione della nostra status ladder (`runner_error` vs `auto_failure`). Conferma indipendente della tassonomia. |
| [`lacs-project/sysknife`](https://github.com/lacs-project/sysknife) | Admin Linux via **azioni tipizzate** invece di stringhe shell + audit log hash-chained Ed25519 + ricevute di approvazione TTL + rollback. Idee per il **controller dell'interlock**. |

## 5. Da evitare (e perche')

- **MCP con SSH** (`ssh-mcp`, `cygnus-ssh-mcp`, `mcp-remote-ssh`, sysknife in
  modalita' attiva...): una shell sul rig in mano a un agente e' il vettore che
  puo' far partire un comando **durante una serie formale**. Il Runbook vieta
  persino una chat in calibrazione. Se mai: **read-only** e **mai** con KVarN
  aperta.
- **Orchestratori multi-agente** (`bernstein`, `roundtable`, `Agent-MCP`,
  `llm-bus`, `trinity-lite`, `kagan`, `veto`): duplicano il mini-swarm. Due
  sistemi che si contendono lo stesso ruolo = confusione, non capacita'.
- **`foldwork-dev/mcp-injector`**: gemello di CodeGraph (stessa claim di
  riduzione token, benchmarkato sugli stessi repo), ma *"free for codebases
  under 100K lines"* -> non pienamente libero. Scartato per principio
  ownership/privacy.
- **Tutto cio' che e' cloud-only** (☁️) o basato su micropagamenti: contro il
  principio privacy-first dello stack.

## 6. Sicurezza: la porta che si apre

Quasi tutti questi server **eseguono codice o restituiscono output esterni**,
cioe' allargano la superficie di **prompt-injection** dentro un loop oggi
fail-closed. Prima di abilitarli servono:

```text
allowlist esplicita dei server        (nessun auto-discovery di rete)
budget per-tool (token/tempo/chiamate)
output trattato come DATO, mai come istruzione
audit di cosa e' stato chiamato e con quali argomenti
degrado fail-soft se un server non risponde
```

## 7. Ordine consigliato

```text
KVarN chiusa
-> Council Fase 1..N
-> interlock live
-> [qui] bridge stdio->HTTP + tools/list + allowlist
-> primo server: container-use o mcp-run-python (sandbox del Runner)
-> misurare se Ornith li usa davvero (stesso test proposto per CodeGraph)
```

**Nota di metodo:** vale per gli MCP la stessa regola di CodeGraph — il beneficio
dipende dal fatto che *il modello locale li scelga davvero* invece di ignorarli o
usarli in aggiunta agli strumenti che gia' ha. Va **misurato**, non assunto.
