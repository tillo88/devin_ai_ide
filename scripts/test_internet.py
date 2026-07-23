"""Prova END-TO-END della catena internet di DEVIN, con chiave reale.

Catena: TinyFish search -> fetch pagina (crawl4ai/playwright/requests) ->
docs context -> selettore import-aware (web_capabilities). Fail-soft per stadio:
stampa OK/FAIL e non si pianta.

Prerequisiti:
- `TINYFISH_API_KEY=...` in `devin/ui/.env` (lo carica questo script);
- provider `tinyfish` in config/settings.json (default);
- per il fetch ricco: crawl4ai/playwright installati nel venv (opzionale: senza,
  il fetch fa fallback a requests).

Uso (sulla macchina col backend):
    # Windows
    .venv-win\\Scripts\\python scripts\\test_internet.py
    # rig
    ./.venv-rig/bin/python scripts/test_internet.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / "devin" / "ui" / ".env")
except Exception as exc:  # dotenv assente: si usa comunque l'ambiente reale
    print(f"[warn] dotenv non caricato ({exc}); uso le env di sistema")


def section(title: str) -> None:
    print("\n" + "=" * 64 + "\n" + title + "\n" + "=" * 64)


def main() -> int:
    config = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
    ws = config.get("web_search", {})

    section("0) Config + chiave")
    print("provider   :", ws.get("provider"))
    print("fetch_engine:", ws.get("fetch_engine"))
    key = os.getenv(ws.get("tinyfish_api_key_env", "TINYFISH_API_KEY"))
    print("TINYFISH_API_KEY:", "presente" if key else "ASSENTE (metti la chiave in devin/ui/.env)")

    from devin.ai import web_search as W

    section("1) SEARCH (provider TinyFish/SearXNG)")
    results = []
    try:
        provider = W.get_web_search_provider(config)
        results = provider.search("fastapi background tasks", max_results=4)
        print(f"OK: {len(results)} risultati")
        for r in results[:3]:
            print("  -", (r.get('title') or '')[:70], "->", (r.get('url') or '')[:80])
    except Exception as exc:
        print("FAIL search:", exc)

    section("2) FETCH pagina (crawl4ai -> playwright/requests)")
    if results:
        url = results[0].get("url", "")
        try:
            page = W.fetch_page_smart(url, max_chars=1500)
            print(f"OK: {len(page)} char da {url[:80]}")
            print("   ", (page[:280] or "").replace("\n", " "), "...")
        except Exception as exc:
            print("FAIL fetch:", exc)
    else:
        print("skip (nessun risultato dalla search)")

    section("3) DOCS CONTEXT (search + fetch, come lo usa il Coder)")
    try:
        block = W.search_docs_context(
            "python requests library official documentation", config, max_chars=1500)
        print(f"OK: {len(block)} char" if block else "vuoto (nessuna doc trovata)")
        if block:
            print("   ", block[:280].replace("\n", " "))
    except Exception as exc:
        print("FAIL docs:", exc)

    section("4) IMPORT-AWARE (web_capabilities: l'orchestrator sceglie)")
    from devin.ai.web_capabilities import (
        detect_language, extract_imports, select_web_capabilities)
    code = "import requests\nfrom fastapi import FastAPI\nimport os\n"
    libs = extract_imports(code, "python")
    print("linguaggio:", detect_language(["a.py"]))
    print("librerie di terze parti estratte:", sorted(libs))
    caps = select_web_capabilities({"web_enabled": True, "budget_left": 2, "imports": libs})
    print("capacita' internet scelte:", caps)

    section("FINE")
    print("Se 1-3 sono OK con contenuto reale, la catena internet funziona end-to-end.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
