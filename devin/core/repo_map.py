"""Repo map compatta stile Aider (roadmap integrazioni, 2026-07-16).

Con ctx 8K il context_engine puo' includere pochi file interi: tutti gli
altri per il modello NON ESISTONO — e li reinventa (visto nei run: import di
moduli fantasma, firme sbagliate). La repo map da' al modello la mappa di
TUTTO il progetto al costo di poche centinaia di token: un file per riga,
solo firme di classi/funzioni top-level.

Versione Python-only via `ast` (zero dipendenze): copre il 95% dei progetti
DEVIN. I non-.py compaiono solo per nome. Estensione tree-sitter multi-lang
possibile in futuro (il parser c'e' gia' in engine/syntax_critic).
"""

import ast
from typing import Any, Dict, List


def _signature(node) -> str:
    try:
        args = ast.unparse(node.args)
    except Exception:
        args = "..."
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    return f"{prefix} {node.name}({args})"


def _summarize_python(rel_path: str, source: str) -> str:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return f"{rel_path}: (sintassi non parsabile)"
    parts: List[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            parts.append(_signature(node))
        elif isinstance(node, ast.ClassDef):
            methods = [child.name for child in node.body
                       if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))][:8]
            parts.append(f"class {node.name}({', '.join(methods)})" if methods
                         else f"class {node.name}")
    if not parts:
        return f"{rel_path}: (nessun simbolo top-level)"
    return f"{rel_path}: " + "; ".join(parts[:12])


def build_repo_map_from_files(files: List[Dict[str, Any]], max_chars: int = 4000,
                              max_files: int = 200) -> str:
    """files = [{"rel_path": ..., "content": ...}] (output di
    collect_project_files: esclusioni gia' applicate)."""
    if not files:
        return ""
    lines: List[str] = []
    others: List[str] = []
    for f in files[:max_files]:
        rel = f.get("rel_path", "?")
        if rel.endswith(".py"):
            lines.append(_summarize_python(rel, f.get("content", "")))
        else:
            others.append(rel)
    if others:
        lines.append("altri file: " + ", ".join(others[:30])
                     + (f" (+{len(others) - 30})" if len(others) > 30 else ""))
    body = "\n".join(lines)
    if len(body) > max_chars:
        body = body[:max_chars] + "\n# [...repo map troncata...]"
    return f"# REPO MAP ({len(files)} file — firme, non contenuti)\n{body}\n"
