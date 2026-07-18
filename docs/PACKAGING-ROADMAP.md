# Roadmap — DEVIN come app Windows installabile (.exe/.msi)

Obiettivo: doppio-click → app desktop, senza far gestire WSL/backend all'utente.
Con **wizard al primo avvio**: "Hai un rig esterno?" → rig remoto (leggero) o
tutto locale sullo stesso PC.

## Decisione chiave (da fissare in FASE 0)
Il backend FastAPI e' Python. Impacchettarlo con PyInstaller e' fattibile, ma le
librerie ML pesanti (torch, ecc.) lo rendono enorme e fragile. Mitigazione:
DUE profili, decisi dal wizard.
- **Profilo RIG** (consigliato per Alessandro): il PC fa solo GUI + backend
  LEGGERO che parla al rig via HTTP (:8080). NIENTE torch/llama locali → sidecar
  piccolo e robusto. Tutta la potenza sta sul rig.
- **Profilo LOCALE**: il backend serve anche i modelli in locale (llama-server
  Windows + GGUF) → sidecar piu' pesante, dipendenze ML incluse.
La stessa UI/UX; cambia solo cosa gira sotto.

## FASE 1 — Backend come sidecar (il pezzo tecnico centrale)
- [ ] Snellire i requirements del backend per il profilo RIG (togliere torch/
      sentence-transformers/crawl4ai dal bundle; restano fastapi, uvicorn,
      requests, i client HTTP). Fallback keyword per il vector store senza ML.
- [ ] PyInstaller → un exe Windows-native del backend (`devin-backend.exe`).
- [ ] Test: l'exe parte standalone e serve `http://127.0.0.1:5000/app` SENZA WSL.

## FASE 2 — Tauri avvia il sidecar
- [ ] `src-tauri`: registrare `devin-backend.exe` come sidecar (externalBin);
      avvio all'apertura, stop alla chiusura (riusa il close_cleanup gia' fatto).
- [ ] Rimuovere la dipendenza dai launcher WSL per l'uso "installato".
- [ ] Test: `tauri dev` → il backend parte da solo, l'app funziona senza WSL.

## FASE 3 — Wizard onboarding (local vs rig)
- [ ] Schermata primo avvio: "Hai un rig esterno?"
      - Si' → IP:porta del rig (default 192.168.1.100:8080) → salva rig_host +
        rig_self_hosted=false.
      - No → setup locale (path modelli, avvio llama-server locale).
- [ ] Persistenza in settings.json sotto `%APPDATA%\DEVIN` (non nel repo).
- [ ] Bandierina "gia' configurato" → i successivi avvii saltano il wizard;
      re-editabile da Impostazioni.
- [ ] I flag esistono gia' (rig_self_hosted, rig_host): e' UI sopra logica pronta.

## FASE 4 — Build installer
- [ ] `tauri build` → `.msi` (WiX) e/o `.exe` (NSIS): icona, voce Start, uninstall.
- [ ] Includere il sidecar + asset. Verificare che parta su una macchina PULITA
      (senza Python/WSL) → doppio-click → app viva.

## FASE 5 — Rifiniture (opzionali, dopo)
- [ ] Firma del codice (evita l'avviso SmartScreen di Windows).
- [ ] Auto-update (updater Tauri).
- [ ] Vendoring Monaco/font offline (uso senza internet).

## Ordine consigliato
0 (decisione profili) → 1 (sidecar RIG, leggero) → 2 (Tauri lo avvia) →
3 (wizard) → 4 (installer .msi). Il profilo LOCALE si aggiunge dopo, quando il
RIG e' solido: cosi' il primo installer e' piccolo e affidabile.
