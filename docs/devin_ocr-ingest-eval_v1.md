# DEVIN — Unlimited-OCR: valutazione per l'ingest documenti (v1)

**Data:** 2026-07-24 · **Stato:** valutazione, nessun deploy. · **Contesto:** rig triple-boot, A2000 **6GB**, serie KVarN in corso.

Valutazione di `baidu/Unlimited-OCR` come motore OCR/parsing documenti per DEVIN, sui quattro assi richiesti: farlo girare sull'A2000, quant/performance, verifica GGUF, aggancio a DEVIN.

---

## 1. Cos'è (in breve)

VLM da **3B, BF16, MIT**, famiglia **DeepSeek-OCR**. Architettura (da fonti llama.cpp): **DeepEncoder** (vision) di DeepSeek-OCR v1 + decoder **DeepSeek-V2 MoE** con attenzione sostituita da **R-SWA** (Reference Sliding Window Attention). Prende immagine/PDF → testo strutturato (markdown). Punto di forza dichiarato: *one-shot long-horizon parsing* (documenti lunghi / PDF multipagina in un colpo, grazie alla compressione in vision-token). Usa un logit processor anti-ripetizione custom.

## 2. Il vincolo vero: la tua A2000 è 6GB

Il BF16 lo fa solo Ampere in su, quindi delle GPU del rig **solo l'A2000** è adatta (1080/1080Ti Pascal = no BF16; 1660 Turing = solo FP16). Ma l'A2000 è la **6GB**, e il BF16 pieno sono **~6GB di soli pesi** + attivazioni + vision-token + contesto → **non ci sta**. Conseguenza operativa: il BF16 nativo (Transformers) è escluso, e i flag del README ufficiale (`--attention-backend fa3`, cioè FlashAttention-3 = **Hopper/H100**) non sono per la tua scheda. Serve **quantizzare**. Ed è esattamente ciò che abilita il percorso migliore.

## 3. Runtime: llama.cpp/GGUF è la scelta giusta PER TE (novità)

Aggiornamento verificato (luglio 2026): **llama.cpp supporta Unlimited-OCR in mainline** — i GGUF caricano su build ≥ commit `4fc4ec5` (build 168, 2026-07-01), primo mainline con `deepseek2-ocr` (via PR #24969); esistono anche PR-branch stacked. Richiede un file **mmproj** (vision projector) accanto al GGUF per l'input immagine (MTMD = multimodale in llama.cpp). Gli mmproj sono stati riuppati il 2026-07-13 con `preproc_max_tiles=32` allineato al reference.

Perché questo è il percorso giusto per il tuo stack, e non vLLM/SGLang/Transformers:

- **Riusi il motore che già hai.** Ornith gira su llama.cpp sul rig: stesso engine, stesso know-how, niente CUDA 12.9/13.0 né kernel Hopper. vLLM/SGLang sarebbero un runtime nuovo e pesante per un job OCR.
- **La quantizzazione risolve i 6GB.** Q4/Q5 porta i pesi da ~6GB (BF16) a ~2GB, quindi entra nell'A2000 con margine per mmproj + tile immagine.
- **Governance rispettata.** Il tuo principio `ai-rig-ops` dice che `/opt/llama.cpp` è il motore stabile del ruolo e **non si sovrascrive con gli esperimenti**. Quindi: **build separata** di llama.cpp (recente, ≥ build 168) in un path dedicato solo per l'OCR, il motore stabile di Ornith non si tocca.

**Caveat onesti:** (a) il supporto è **recente** — serve una llama.cpp buildata dopo il 2026-07-01, non quella (probabilmente più vecchia) del motore stabile; (b) servono **due file**, GGUF pesi + mmproj vision, entrambi da repo community — vanno verificati (data mmproj ≥ 2026-07-13); (c) la performance del vision encoder su llama.cpp/Ampere è meno battuta di vLLM — per l'ingest batch va bene, per throughput alto no.

## 4. Quant e budget VRAM per A2000 6GB (stime, da verificare)

| Componente | BF16 nativo | GGUF Q5_K_M | GGUF Q4_K_M |
| --- | --- | --- | --- |
| Pesi 3B | ~6.0 GB | ~2.3 GB | ~1.9 GB |
| mmproj (vision, fp16) | incluso | ~0.5–1.5 GB | ~0.5–1.5 GB |
| KV + tile immagine (ctx ridotto) | alto | moderato | moderato |
| **Sta in 6GB?** | **No** | **Sì, con margine** | **Sì, comodo** |

Consiglio di partenza: **Q4_K_M** (o Q5_K_M se la qualità OCR cala troppo) + mmproj fp16. Se sei al limite, riduci `preproc_max_tiles` e/o il contesto. I numeri sono **stime** — la verità la dà un `nvidia-smi` one-shot sull'A2000 **prima/dopo** il load (mai durante l'inferenza, regola rig).

## 5. Verifica GGUF (fatta)

Esistono GGUF community per Unlimited-OCR: `sabafallah/Unlimited-OCR-GGUF`, `vimalnakrani/unlimited-ocr-gguf`, `sahilchachra/Unlimited-OCR-GGUF`. Riferimento autoritativo sul flusso OCR in llama.cpp: il blog ggml-org/ngxson "Using OCR models with llama.cpp". **Regole d'oro prima di scaricare:** il repo deve fornire **sia** il GGUF pesi **sia** l'mmproj (con `preproc_max_tiles=32`, data ≥ 2026-07-13); e la tua llama.cpp deve essere **≥ build 168 (commit 4fc4ec5)**. Senza mmproj aggiornato o con build vecchia → non parte o dà risultati sbagliati.

## 6. Aggancio a DEVIN (design, non implementazione)

Stessa filosofia di SearXNG: **microservizio separato**, non roba nel loop del modello.

```
PDF/immagine
  → [OCR service]  llama.cpp server (build OCR) + GGUF Q4 + mmproj, su A2000
  → markdown pulito
  → [DEVIN] tool/skill "document ingest" chiama il service via HTTP locale
  → il markdown entra nel layer knowledge / contesto
```

- **Isolamento:** il service OCR gira sull'**A2000 dedicata**, mentre il modello principale usa le altre GPU — così non interferisce con l'inferenza di Ornith né con la KVarN. Job **offline/batch** per il bulk (stile `infer.py`: cartella immagini o PDF → markdown), on-demand per il singolo doc.
- **Interfaccia:** llama.cpp server espone un endpoint OpenAI-compatibile; DEVIN lo tratta come una capability web/knowledge in più, dietro un setting per-ruolo (come il resto).
- **Gate:** introdurlo **dopo** la chiusura della serie KVarN, per non aggiungere un consumatore GPU/confondente durante le calibrazioni.

> Per il rig a VRAM condivisa, il pattern preferito **non** è il servizio sempre acceso qui sopra, ma la **Fase 0 effimera** (§6.1): evita del tutto la partizione delle GPU.

## 6.1 Fase 0: ingest effimero (pattern preferito)

**L'idea:** OCR e modello grande **non sono mai contemporaneamente in VRAM**. L'OCR è una fase iniziale, legata al ciclo di vita del progetto, che si carica → fa il suo → si smonta lasciando il rig libero. Conseguenza diretta: **niente partizione GPU, niente ricalcolo del tensor_split, nessuna tassa di ~6GB sul pool di Ornith.** Il problema di convivenza (§ precedente) sparisce perché non c'è convivenza.

**Il ciclo:**

```
[boot progetto]
  → carica OCR (3B GGUF, ~2GB)     una GPU qualsiasi da 6GB basta
  → ingerisce TUTTO               PDF/immagini → markdown (batch, in un colpo)
  → SCARICA OCR                   libera la VRAM (stable stop, nessun contesto CUDA appeso)
  → carica Ornith                 sul pool PIENO (~51GB), tensor_split invariato
  → contestualizza + coding        Ornith legge il markdown come sua prima task
  → durante il lavoro              solo internet (SearXNG + docs import-aware) per conferme/pezzi mancanti
```

**Divisione del lavoro.** L'OCR fa solo doc→markdown (ciò che sa fare). Il *"analizza, contestualizza, mette giù tutto bene"* lo fa **Ornith come prima task**, quando è già caricato per programmare comunque. Nessuno dei due deve fare entrambe le cose; nessuno dei due è mai co-residente.

**L'output vero è un artefatto di conoscenza persistente.** La Fase 0 scrive un *project-knowledge* nel progetto, per es.:

```
<progetto>/.devin/knowledge/
  ├── sources/            # 1 markdown per documento ingerito (OCR grezzo pulito)
  │   ├── spec-v2.md
  │   └── datasheet.md
  ├── index.json          # elenco fonti + sha + data ingest + n. pagine
  └── brief.md            # sintesi contestuale scritta da Ornith (prima task)
```

Così a metà run, se al coder serve un documento, legge il **markdown** (testo, gratis) — non ricarica mai il modello OCR. **L'OCR lo paghi una volta, il testo lo rileggi per sempre.** È il principio del context che compound dall'inizio.

**Aggancio all'interlock (PR #6).** La Fase 0 è un **model-swap controllato**: fermare l'OCR, caricarne un altro, in una finestra gestita — esattamente ciò per cui è disegnato il calibration interlock. Sequenza: `arma → drena il lavoro in corso → safe_to_stop_model=true → swap → ripristina → verifica health`. L'ingest OCR sarebbe il **primo utente reale dell'interlock**, non un meccanismo nuovo.

**Gate di sequenza (mai concorrenza).** Serve un sequencer che garantisca l'ordine, non l'esecuzione parallela:

```
OCR completamente giù → VRAM libera (nvidia-smi one-shot, MAI durante) → su Ornith
```

Implementabile con `systemd` (`Conflicts=`/`After=`) o uno script con gate esplicito sulla VRAM. Riusa la disciplina di *stable stop prima di riavviare CUDA* già presente nel rig (la stessa del manifest calibrazione).

**Due avvertenze perché regga:**

1. **Niente swap frequenti.** Caricare/scaricare il 35B costa (tempo + danza stable-stop anti D-state). Si batcha **tutto** l'ingest all'inizio, non a gocce. Documento nuovo a metà progetto → in coda per il **prossimo checkpoint**, non ricarico l'OCR al volo.
2. **Sequenza garantita.** Mai i due processi CUDA vivi insieme sulla stessa VRAM: prima l'uno scende, poi l'altro sale.

**Quando i documenti contano davvero:** scaffold-da-zero e prep-training — ed è tutto a monte, quindi la Fase 0 è il punto giusto. Durante coding/debug il fabbisogno documentale è piccolo e sporadico, coperto da internet. Il modello OCR **non serve residente** durante il lavoro dell'agente.

## 7. Prossimi passi concreti (quando la KVarN è chiusa)

1. Build separata di llama.cpp recente (≥ build 168) in path dedicato — **non** toccare `/opt/llama.cpp`.
2. Scaricare un GGUF Q4_K_M + mmproj (≥ 2026-07-13) da un repo community verificato.
3. Smoke test offline su 1 immagine e 1 PDF multipagina con `llama-mtmd-cli` (o server + mmproj); `nvidia-smi` one-shot per il budget VRAM reale sull'A2000 6GB.
4. Se la qualità regge e sta in VRAM → implementare la **Fase 0 effimera** (§6.1): sequencer OCR↓→Ornith↑ via interlock (PR #6), scrittura del project-knowledge in `.devin/knowledge/`, dietro setting per-ruolo.
5. Se non regge in 6GB o la qualità cala → valutare Q5, ridurre tile/contesto, o spostare l'OCR sul PC.
6. Prima misura di successo: su uno scaffold-da-zero con documenti, contare quante volte il coder rilegge il markdown vs quante volte gli servirebbe l'OCR residente (atteso: ~0) e quante conferme le prende da internet.

## 8. Fonti

- Model card: https://huggingface.co/baidu/Unlimited-OCR
- Repo: https://github.com/baidu/Unlimited-OCR
- llama.cpp OCR flow: https://huggingface.co/blog/ggml-org/using-ocr-models-with-llama-cpp
- GGUF community: https://huggingface.co/sabafallah/Unlimited-OCR-GGUF · https://huggingface.co/vimalnakrani/unlimited-ocr-gguf · https://huggingface.co/sahilchachra/Unlimited-OCR-GGUF
- DeepSeek-OCR GGUF/llama.cpp PR context: https://github.com/coredevorg/DeepSeekOCR

> Note: dettagli architetturali (DeepEncoder + DeepSeek-V2 MoE + R-SWA) e stato del supporto llama.cpp provengono dalle fonti sopra, verificate a luglio 2026. I budget VRAM sono stime, da confermare sul rig.
