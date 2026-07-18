# Audit esterno 2026-07-10 — triage e stato

Lista di 28 punti da audit esterno. Verdetti verificati sul codice reale.
Legenda: ✅ corretto · 🟡 valido, in coda · 🔵 parzialmente coperto · ⚪ minore/da verificare

## Rig (ai-rig-iso-build)

| # | Punto | Stato |
|---|---|---|
| 1 | GRUB /boot separata | ✅ 2026-07-10 (riscritto: configfile chain per hermes/teacher, kernel versionato+hook per devin — DA VERIFICARE al primo boot) |
| 2 | Fallback clone llama.cpp | ✅ 2026-07-10 (mkdir solo nel ramo prebuilt) |
| 3 | Copia cache silenziata | ✅ 2026-07-10 (rsync + fail esplicito) |
| 4 | Seriali duplicati | ✅ 2026-07-10 (guardie in wipe, generatore, grub-stable-entries) |
| 5 | by-path incoerente | ✅ 2026-07-10 (fallback by-path nel wipe + check device risolti distinti) |
| 6 | Collisione 4° disco condiviso | ✅ 2026-07-10 (guardia in wipe + generatore) |
| 19 | trap cleanup mount ISO | ✅ 2026-07-10 |
| 20 | Artifact obbligatori fail-closed | ✅ 2026-07-10 (flag `--production` in build-iso.sh: modelli/driver mancanti → errore) |
| 21 | Checksum/versioni non bloccati | ✅ 2026-07-10 (SHA-256 di ISO/driver/CUDA/modelli via config/artifacts.sha256 + verify_sha; pin commit llama/manifest ancora aperto) |
| 22 | CHANGEME non bloccante | ✅ 2026-07-10 (exit 1 in `--production`; warning+conferma in build normale) |
| 23 | Rendering sed fragile | ✅ 2026-07-11 (_sedq in 05-generate-nocloud.sh: escape di & \| \\ applicato ai valori free-text — arg llama, path, repo, nomi file; enum/numerici safe-by-construction) |

## DEVIN (devin_ai_ide)

| # | Punto | Stato |
|---|---|---|
| 7 | 0.0.0.0 senza auth | ✅ 2026-07-10 (default 127.0.0.1; ui.host in settings per LAN esplicita; per il rig servira' anche un token) |
| 8 | Path traversal /api/explore /api/file | ✅ 2026-07-10 (_safe_under_allowed: resolve() + check sotto _ALLOWED_ROOTS = workspace/ + cartelle dal picker; gate su entrambi gli endpoint) |
| 9 | stream_log perde righe/non termina | ✅ 2026-07-10 (lettura da 0, chiusura su status/run morto, evento done) |
| 10 | Operazioni bloccanti in async | 🔵 2026-07-10: to_thread su ensure_models, provider.search, fetch_top_results, estrazione documenti, scansione file explorer. RESTA: bridge queue+thread per ai.stream (hot-path streaming — da fare con test vero, rischio regressione chat) |
| 11 | Run ID al secondo | ✅ 2026-07-10 (aggiunti microsecondi) |
| 12 | AutoMem senza gateway | 🔵 by-design TEMPORANEO (Opzione A): il gateway e' Fase 1 della roadmap harness; quando esiste si cambia solo automem_client.py. Valido: schema kind/scope/status + no raw chat |
| 13 | Export JSONL "pronto per LoRA" | 🔵 e' export RAW e va trattato come tale; workflow candidate/approved arriva con l'harness |
| 14 | Upload senza limiti | ✅ 2026-07-10 (_read_upload_limited: 15MB immagini, 25MB documenti, letti a chunk con abort; knowledge già limitata a 20MB) |
| 15 | _scan_project_files illimitata | ✅ 2026-07-10 (cap 2000 file + 50k attraversamento, no sort anticipato, chiamata via to_thread) |
| 16 | token/s in realta' chunk/s | ✅ 2026-07-11 (documentato in fast_app: token_count = chunk SSE ≈ 1 token con llama-server, quindi tps ≈ token/s; non rinominato per non toccare il frontend) |
| 17 | Injection path in onclick (index.html) | ✅ 2026-07-10 (file explorer: path in data-attribute + addEventListener, niente più path interpolati in onclick; run_id resta in onclick ma è server-generated, non user-controllabile) |
| 18 | Ramo vision morto | ✅ 2026-07-10 (rimosso) |
| 24-25 | UI legacy (Tkinter/Flask) | ⏸️ RINVIATO a fase deploy: è pulizia di codice morto (l'UI reale è fast_app.py FastAPI), nessun impatto funzionale — si fa quando si prepara il deploy sul rig, insieme a #28 |
| 26 | Bottone Salva Monaco finto | ✅ 2026-07-10 (/api/file/save reale: scrittura atomica temp+replace, backup .bak, path validato #8; frontend salva il buffer Monaco con conferma "✓ Salvato") |
| 27 | Download log restituisce JSON | ✅ 2026-07-10 (?download=1 → FileResponse con filename; view → PlainTextResponse leggibile; containment LOG_DIR) |
| 28 | CDN (Monaco/font) vs offline | ⏸️ RINVIATO a fase deploy sul rig: serve solo quando DEVIN gira SUL rig (rig_self_hosted=true) senza internet; oggi gira sulla workstation con internet. Richiede di scaricare Monaco+font in un vendor/ locale (passo con internet, una volta sulla build machine) + loader local-first con fallback CDN. Da fare quando si esegue deploy-devin-webapp.sh |

## Ordine consigliato per il prossimo giro
1. #8 sandbox path (sicurezza, e prerequisito sensato prima del deploy sul rig)
2. #10 to_thread sulle operazioni pesanti
3. #28 asset locali (prerequisito deploy rig)
4. #14 limiti upload restanti · #15 cap scansione
5. #24-27 pulizia UI legacy
6. Rig #20-23 (hardening build) — prima della ISO "definitiva"
