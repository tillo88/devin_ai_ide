"""Critico sintattico deterministico multi-linguaggio (tree-sitter).

Perche' (roadmap integrazioni 2026-07, punto 1): il quality gate compilava
solo Python — un file JS/TS/Rust/HTML rotto passava la "verifica sintassi"
senza essere guardato. Con tree-sitter l'AST si costruisce in millisecondi,
offline, senza modelli: sintassi rotta -> reject deterministico PRIMA che il
file arrivi all'utente (o al commit).

Design:
  - .py    -> compile() nativo (messaggi migliori di tree-sitter);
  - .json  -> json.loads nativo (zero dipendenze);
  - altri  -> tree-sitter via `tree-sitter-language-pack` SE installato;
  - dipendenza assente o linguaggio non mappato -> fail-open dichiarato
    (checked=False): il gate non blocca, ma sa di non aver verificato.

Install (opzionale ma consigliato):
  pip install tree-sitter tree-sitter-language-pack
"""

import json
from pathlib import Path
from typing import Any, Dict, List

# Estensione -> nome linguaggio nel language pack.
EXT_LANG = {
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "tsx",
    ".rs": "rust", ".go": "go",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".cc": "cpp", ".hpp": "cpp",
    ".java": "java", ".rb": "ruby", ".php": "php",
    ".sh": "bash", ".bash": "bash",
    ".html": "html", ".htm": "html", ".css": "css",
    ".toml": "toml", ".yaml": "yaml", ".yml": "yaml",
}

_PARSERS: Dict[str, Any] = {}
_PACK_AVAILABLE: bool | None = None


def tree_sitter_available() -> bool:
    global _PACK_AVAILABLE
    if _PACK_AVAILABLE is None:
        try:
            import tree_sitter_language_pack  # noqa: F401
            _PACK_AVAILABLE = True
        except Exception:
            _PACK_AVAILABLE = False
    return _PACK_AVAILABLE


def _get_parser(language: str):
    if language not in _PARSERS:
        from tree_sitter_language_pack import get_parser
        _PARSERS[language] = get_parser(language)
    return _PARSERS[language]


def _error_locations(root, limit: int = 3) -> List[str]:
    """Prime posizioni di ERROR/MISSING nell'AST (discesa solo nei sottoalberi
    che contengono errori: veloce anche su file grandi)."""
    found: List[str] = []
    stack = [root]
    while stack and len(found) < limit:
        node = stack.pop()
        if node.type == "ERROR" or node.is_missing:
            row, col = node.start_point
            kind = "nodo mancante" if node.is_missing else "errore di sintassi"
            found.append(f"riga {row + 1}, col {col + 1}: {kind}")
            continue
        if node.has_error:
            stack.extend(reversed(node.children))
    return found


def check_text(filename: str, source: str) -> Dict[str, Any]:
    """Verifica sintattica di un sorgente.

    Ritorna {"language": str|None, "checked": bool, "errors": [str,...]}.
    checked=False = nessuna verifica possibile (linguaggio non mappato o
    tree-sitter assente): il chiamante decide, ma di default NON blocca.
    """
    ext = Path(filename).suffix.lower()

    if ext == ".py":
        try:
            compile(source, filename, "exec")
            return {"language": "python", "checked": True, "errors": []}
        except SyntaxError as exc:
            return {"language": "python", "checked": True,
                    "errors": [f"SyntaxError: {exc.msg} (riga {exc.lineno})"]}
        except (ValueError, TypeError) as exc:
            return {"language": "python", "checked": True,
                    "errors": [f"{type(exc).__name__}: {exc}"]}

    if ext == ".json":
        try:
            json.loads(source)
            return {"language": "json", "checked": True, "errors": []}
        except json.JSONDecodeError as exc:
            return {"language": "json", "checked": True,
                    "errors": [f"JSON non valido: {exc.msg} (riga {exc.lineno})"]}

    language = EXT_LANG.get(ext)
    if not language or not tree_sitter_available():
        return {"language": language, "checked": False, "errors": []}

    try:
        parser = _get_parser(language)
        tree = parser.parse(source.encode("utf-8"))
    except Exception as exc:
        # parser rotto/linguaggio mancante nel pack: fail-open dichiarato
        return {"language": language, "checked": False,
                "errors": [], "note": f"parser {language} non disponibile: {exc}"}

    if tree.root_node.has_error:
        return {"language": language, "checked": True,
                "errors": [f"[{language}] {loc}" for loc in _error_locations(tree.root_node)]}
    return {"language": language, "checked": True, "errors": []}


def check_file(path: str | Path) -> Dict[str, Any]:
    p = Path(path)
    try:
        source = p.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"language": None, "checked": False, "errors": [f"illeggibile: {exc}"]}
    return check_text(p.name, source)
