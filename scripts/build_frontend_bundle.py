"""Build the static frontend bundle for the native desktop app (increment 2).

The desktop app must ship its UI as local files (a real app, not a web page
served by the backend). This renders the Jinja template `codex_app.html` into a
static `index.html` and copies the static assets into `src-tauri/frontend/`,
which Tauri bundles as `frontendDist`.

The web/rig deployment keeps using the Jinja template served by FastAPI at
`/app`; this bundle is a separate, self-contained copy for the desktop shell.

Run: python scripts/build_frontend_bundle.py
Verifiable offline; no network, no GPU.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TEMPLATE = REPO / "devin" / "ui" / "templates" / "codex_app.html"
STATIC_SRC = REPO / "devin" / "ui" / "static"
OUT_DIR = REPO / "src-tauri" / "frontend"


def build() -> Path:
    stamp = time.strftime("%Y%m%d%H%M%S")

    html = TEMPLATE.read_text(encoding="utf-8")
    if "{{ shell_version }}" not in html:
        # Not fatal, but worth surfacing: the template changed shape.
        print("[warn] '{{ shell_version }}' non trovato nel template")
    html = html.replace("{{ shell_version }}", stamp)

    # Desktop: la UI viene caricata DOPO la discovery del backend. Sostituisci
    # il caricamento diretto di codex_app.js con il bootstrap, che scopre il
    # backend (rig-first), gestisce il prompt e poi importa l'app.
    main_script = f'<script type="module" src="/static/js/codex_app.js?v={stamp}"></script>'
    boot_script = f'<script type="module" src="/static/js/desktop_bootstrap.js?v={stamp}"></script>'
    if main_script in html:
        html = html.replace(main_script, boot_script)
    else:
        print("[warn] tag di caricamento codex_app.js non trovato: bootstrap non iniettato")

    # Clean and recreate the bundle dir.
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # index.html (rendered, static).
    (OUT_DIR / "index.html").write_text(html, encoding="utf-8")

    # static assets: css/js/icons/manifest/sw served from the same relative
    # roots the absolute paths in index.html expect (/static/..., /sw.js).
    shutil.copytree(STATIC_SRC, OUT_DIR / "static")
    for extra in ("sw.js", "manifest.webmanifest"):
        src = STATIC_SRC / extra
        if src.exists():
            shutil.copy2(src, OUT_DIR / extra)

    # Report.
    files = sum(1 for _ in OUT_DIR.rglob("*") if _.is_file())
    print(f"[ok] bundle in {OUT_DIR} ({files} file, versione {stamp})")
    return OUT_DIR


if __name__ == "__main__":
    build()
