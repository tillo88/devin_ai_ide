#!/usr/bin/env bash
# archive-legacy.sh — sposta la UI legacy (Tkinter) e il web_app Flask in
# archive/legacy/, preservando struttura e history (git mv). REVERSIBILE.
#
# Perche': l'app viva e' la dashboard FastAPI (devin/ui/fast_app.py, avviata da
# launcher.py -> run_server). I moduli qui sotto NON sono importati da nessun
# codice vivo — verificato 2026-07-17:
#   - launcher.py importa devin.ui.fast_app (non piu' devin.main/Tkinter);
#   - fast_app.py non importa nessuno di questi;
#   - gli unici riferimenti sono INTERNI al cluster legacy (main->app, main->editor).
# Toglierli snellisce il bundle .exe (via anche flask e tk dai requisiti: vedi
# requirements-rig.txt, gia' senza flask/tk/playwright/crawl4ai).
set -euo pipefail
cd "$(dirname "$0")/.."   # root del repo

LEGACY=(
  devin/main.py                 # vecchio entry Tkinter (-> devin.ui.app.start_ui)
  devin/ui/app.py               # UI Tkinter (start_ui)
  devin/ui/main.py              # UI Tkinter (window principale)
  devin/ui/editor.py            # editor Tkinter
  devin/ui/diff_viewer.py       # diff viewer Tkinter
  devin/ui/stream_console.py    # console Tkinter, non referenziata da nessuno
  devin/ui/web_app.py           # dashboard Flask legacy (sostituita da fast_app)
)

mkdir -p archive/legacy
for f in "${LEGACY[@]}"; do
  if [ ! -e "$f" ]; then echo "gia' assente: $f"; continue; fi
  dest="archive/legacy/$f"
  mkdir -p "$(dirname "$dest")"
  if git ls-files --error-unmatch "$f" >/dev/null 2>&1; then
    git mv "$f" "$dest"
  else
    mv "$f" "$dest"          # file non tracciato: mv normale
  fi
  echo "archiviato: $f -> $dest"
done

# --- Doc superati: consolidati in docs/TRAINING.md (2026-07-17) ---
OLD_DOCS=(
  docs/TRAINING_DATASETS_AND_BENCHMARKS.md   # -> fuso in docs/TRAINING.md
  docs/TRAINING_MINI_BENCH_2026-07-15.md     # snapshot storico, lezioni incorporate in docs/TRAINING.md
)
mkdir -p archive/old_docs
for f in "${OLD_DOCS[@]}"; do
  if [ ! -e "$f" ]; then echo "gia' assente: $f"; continue; fi
  dest="archive/old_docs/$(basename "$f")"
  if git ls-files --error-unmatch "$f" >/dev/null 2>&1; then git mv "$f" "$dest"; else mv "$f" "$dest"; fi
  echo "archiviato doc: $f -> $dest"
done

echo
echo "== Verifica che il backend importi ancora pulito =="
# Usa il python del venv (ha fastapi/openai/ecc.), NON quello di sistema.
PY=python; [ -x venv/bin/python ] && PY=venv/bin/python
if "$PY" -c 'import devin.ui.fast_app' 2>/tmp/devin_import_err.txt; then
  echo "OK ($PY): devin.ui.fast_app importa senza i moduli archiviati."
else
  echo "!! import fallito con $PY. Traceback:"; tail -n 25 /tmp/devin_import_err.txt
  echo "   Se e' ModuleNotFoundError su fastapi/openai/... stai usando il python sbagliato (attiva il venv)."
fi
echo
echo "Per annullare tutto prima del commit:  git checkout -- . && git clean -fd archive/legacy archive/old_docs"
