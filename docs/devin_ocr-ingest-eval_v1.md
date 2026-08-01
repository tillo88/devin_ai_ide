# DEVIN — Unlimited-OCR / ingest documenti → il documento vive in `ai-rig-ops`

**Questo file è un puntatore, non la fonte.** La valutazione completa è mantenuta nel
repository operativo privato:

```text
ai-rig-ops : docs/devin/DEVIN_OCR_INGEST_EVAL_v1.md
indicizzata da: runbook/README.md
addendum:       runbook/addenda/RUNBOOK_ADDENDUM_v1.4.4-devin-model-evaluation-roadmap.md
```

**Perché lì e non qui.** La valutazione è una decisione *operativa di rig* (budget VRAM
A2000 6GB, build llama.cpp separata, teardown, model-swap, gate post-KVarN), non codice
applicativo. La regola di precedenza del Runbook mette lo stato operativo desiderato in
`ai-rig-ops`; tenerne due copie sincronizzate a mano è solo un modo per farle divergere.

## Sintesi minima (per chi legge solo questo repo)

- Unlimited-OCR = VLM 3B (famiglia DeepSeek-OCR) che converte immagini/PDF in markdown.
- Sull'A2000 **6GB** il BF16 non entra: si usa **llama.cpp + GGUF quantizzato + mmproj**
  (build **separata**, `/opt/llama.cpp` di Ornith non si tocca).
- Pattern scelto: **Fase 0 effimera** — carica OCR → ingest batch → scrive
  `.devin/knowledge/` → **scarica** OCR → carica il modello grande sul pool pieno.
  OCR e modello principale **mai co-residenti**; il markdown persistente si rilegge
  gratis per tutto il progetto.
- Possibile primo utente reale del **calibration interlock** (PR `devin_ai_ide#6`).
- **Stato: valutazione. Nessuna build, nessun deploy.** Gated dopo la chiusura KVarN.

## Lato applicativo (questo repo)

Quando la Fase 0 verrà implementata, ciò che tocca DEVIN AI IDE è: il tool/skill
"document ingest" che consuma `.devin/knowledge/` e il contratto con l'interlock. Il
design dell'arbitrato multi-modello (incluso GLM-Colibri) sta in
`docs/devin_federated_council_design_v1.md` §11.
