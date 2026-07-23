"""Capacita' web del Coder come registro che l'orchestrator interroga.

Idea (owner): DEVIN deve poter fare "tutto" sul fronte internet, ma e' l'
ORCHESTRATOR a decidere cosa richiamare — come per i ruoli del mini-swarm e le
skill. Qui NON c'e' inferenza: solo la logica DETERMINISTICA e BOUNDED che, dato
lo stato di un run (linguaggio, import nel codice generato, errore corrente,
budget residuo), sceglie quali capacita' internet attivare.

Le capacita' vere (search / docs cache / crawl) restano nei moduli esistenti
(`devin/ai/web_search.py`, `devin/core/docs_cache.py`, `devin/ai/crawl_ingestion.py`).
Questo modulo e' il "cervello" della scelta + due helper deterministici
(estrazione import, detect linguaggio, query error-reference per-linguaggio),
tutto testabile offline.

Paletti di sicurezza (coerenti col runbook rig e con P3): scelta deterministica,
budget massimo per run, fail-soft. Niente tool-calling libero del modello.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Set

# Capacita' internet note (nomi stabili usati nei log/evidenza).
CAP_DOCS_FOR_IMPORTS = "docs_for_imports"   # doc ufficiali delle librerie importate
CAP_ERROR_REFERENCE = "error_reference"     # riferimento web per un errore cercabile
CAP_TASK_DOCS = "task_docs"                 # doc per keyword nel task (comportamento storico)

# Linguaggio -> estensioni file.
_LANG_BY_EXT = {
    ".py": "python", ".js": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".rs": "rust",
    ".go": "go", ".rb": "ruby", ".java": "java", ".php": "php",
}

# Moduli stdlib comuni da NON cercare come "libreria di terze parti".
# Non esaustivo: e' un filtro di rumore, non una whitelist di sicurezza.
_PY_STDLIB = {
    "os", "sys", "re", "json", "math", "time", "datetime", "pathlib", "typing",
    "collections", "itertools", "functools", "subprocess", "threading", "asyncio",
    "logging", "unittest", "dataclasses", "enum", "abc", "io", "random", "string",
    "hashlib", "uuid", "shutil", "tempfile", "argparse", "glob", "copy", "csv",
    "socket", "struct", "traceback", "warnings", "contextlib", "inspect", "signal",
    "queue", "base64", "textwrap", "operator", "types", "weakref", "gc", "pickle",
    "sqlite3", "http", "urllib", "email", "html", "xml", "unittest.mock", "decimal",
    "fractions", "statistics", "secrets", "zlib", "gzip", "tarfile", "zipfile",
    "platform", "getpass", "shlex", "pprint", "difflib", "bisect", "heapq",
}

_JS_BUILTINS = {"fs", "path", "http", "https", "os", "url", "crypto", "events",
                "stream", "util", "child_process", "assert", "net", "zlib"}

_PY_IMPORT_RE = re.compile(r"^\s*(?:import\s+([\w.]+)|from\s+([\w.]+)\s+import)", re.MULTILINE)
_JS_IMPORT_RE = re.compile(r"""(?:import[^'"]*from\s*['"]([^'"]+)['"]|require\(\s*['"]([^'"]+)['"]\s*\))""")


def detect_language(filenames: List[str]) -> str:
    """Linguaggio prevalente per estensione. Default 'python'."""
    counts: Dict[str, int] = {}
    for name in filenames or []:
        for ext, lang in _LANG_BY_EXT.items():
            if str(name).lower().endswith(ext):
                counts[lang] = counts.get(lang, 0) + 1
                break
    if not counts:
        return "python"
    return max(counts, key=counts.get)


def _py_top_module(mod: str) -> str:
    return (mod or "").split(".")[0]


def extract_imports(code: str, language: str = "python") -> Set[str]:
    """Estrae i moduli di TERZE PARTI importati dal codice (stdlib/builtin e
    import relativi esclusi). Ritorna i nomi radice, es. {'requests', 'fastapi'}."""
    mods: Set[str] = set()
    text = code or ""
    if language in ("javascript", "typescript"):
        for m in _JS_IMPORT_RE.finditer(text):
            spec = m.group(1) or m.group(2) or ""
            if not spec or spec.startswith((".", "/")):
                continue  # import relativo: non e' una libreria pubblica
            # pacchetto scoped @scope/name -> tienilo intero; altrimenti prima parte
            root = spec if spec.startswith("@") else spec.split("/")[0]
            if root and root not in _JS_BUILTINS:
                mods.add(root)
        return mods
    # default: python
    for m in _PY_IMPORT_RE.finditer(text):
        raw = m.group(1) or m.group(2) or ""
        root = _py_top_module(raw)
        if root and root not in _PY_STDLIB and not raw.startswith("."):
            mods.add(root)
    return mods


def error_reference_query(error: str, language: str = "python") -> str:
    """Query per il riferimento web di un errore, CONSAPEVOLE DEL LINGUAGGIO
    (fix del 'python' hardcoded in _maybe_web_reference)."""
    first_line = (error or "").strip().splitlines()[0][:140] if (error or "").strip() else ""
    lang = (language or "python").strip() or "python"
    return f"{lang} {first_line}".strip()


def select_web_capabilities(state: Dict[str, Any]) -> List[str]:
    """Il cervello del dispatch internet: date le condizioni del run, decide
    QUALI capacita' attivare. Deterministico, bounded, fail-soft.

    state attesi (tutti opzionali):
      web_enabled: bool          -> se False, nessuna capacita'
      budget_left: int           -> quante ricerche restano in questo run
      imports: iterable[str]     -> librerie di terze parti nel codice
      error: str                 -> errore corrente (per il ramo reattivo)
      error_searchable: bool     -> l'errore e' del tipo cercabile
      task_has_api_keywords: bool-> il task nomina API/doc (ramo storico)
    """
    if not state.get("web_enabled", True):
        return []
    budget = int(state.get("budget_left", 0) or 0)
    if budget <= 0:
        return []
    caps: List[str] = []
    if state.get("imports"):
        caps.append(CAP_DOCS_FOR_IMPORTS)
    if state.get("error") and state.get("error_searchable"):
        caps.append(CAP_ERROR_REFERENCE)
    if not caps and state.get("task_has_api_keywords"):
        caps.append(CAP_TASK_DOCS)
    # Mai piu' capacita' del budget residuo: una capacita' = una ricerca.
    return caps[:budget]
