"""Docs cache: cache TTL davanti al fetch live di documentazione ufficiale.

Il problema (batch MBPP, ricorrente): il modello INVENTA endpoint/firme quando
non ha in contesto la doc ufficiale (4 host Steam di fantasia in 4 run).

Design (rivisto 2026-07-16, feedback Alessandro): NON un parco hardcoded che
diventa gigante e stantio. La strada primaria e' **internet**: quando serve, si
fa un fetch LIVE della doc ufficiale (via web_search esistente) e lo si mette
in cache con una SCADENZA (TTL), cosi' non si riscarica a ogni run e la cache
resta piccola e fresca (le voci auto scadute vengono potate). I seed manuali
(source="pinned") sono solo un **fallback offline** — utile per il rig in ruolo
devin senza internet — e restano pochi e curati.

Store: <root>/docs_cache/<slug>.md + index.json. Ogni voce:
  {slug, title, keys[], source: "pinned"|"web", fetched_at, source_url, path}
Retrieval deterministico (match per chiave, nessun embedding).
"""

import json
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

_SLUG_RE = re.compile(r"[^a-z0-9]+")
MAX_DOC_CHARS = 8000       # cap per documento salvato
MAX_INJECT_CHARS = 2500    # cap per doc iniettato nel contesto
DEFAULT_TTL_S = 7 * 24 * 3600   # voci web fresche per 7 giorni
_STOP = {"the", "and", "for", "una", "col", "che", "con", "dei", "del", "using", "only",
         "build", "write", "function", "python", "create", "make", "test", "tests"}


def _slug(text: str) -> str:
    return _SLUG_RE.sub("-", (text or "").strip().lower()).strip("-") or "doc"


def _keys_from_text(text: str, limit: int = 6) -> List[str]:
    words = [w for w in _SLUG_RE.split((text or "").lower()) if len(w) > 3 and w not in _STOP]
    seen: List[str] = []
    for w in words:
        if w not in seen:
            seen.append(w)
    return seen[:limit]


class DocsCache:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.dir = self.root / "docs_cache"
        self.index_file = self.dir / "index.json"

    def _load_index(self) -> Dict[str, Any]:
        if not self.index_file.exists():
            return {"docs": []}
        try:
            data = json.loads(self.index_file.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) and "docs" in data else {"docs": []}
        except Exception:
            return {"docs": []}

    def _save_index(self, index: Dict[str, Any]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.index_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.index_file)

    def list_docs(self) -> List[Dict[str, Any]]:
        return self._load_index()["docs"]

    def add_doc(self, title: str, content: str, keys: Optional[List[str]] = None,
                source_url: str = "", source: str = "pinned") -> Dict[str, Any]:
        """Aggiunge/sovrascrive un documento. source="pinned" = fallback offline
        curato (non scade); source="web" = risultato di un fetch live (TTL)."""
        content = (content or "").strip()
        if not content:
            raise ValueError("docs cache: contenuto vuoto")
        keys = [k.strip().lower() for k in (keys or [title]) if k and k.strip()]
        if not keys:
            raise ValueError("docs cache: almeno una chiave richiesta")
        slug = _slug(title)
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / f"{slug}.md"
        path.write_text(content[:MAX_DOC_CHARS], encoding="utf-8")

        index = self._load_index()
        index["docs"] = [d for d in index["docs"] if d.get("slug") != slug]
        entry = {"slug": slug, "title": title, "keys": sorted(set(keys)),
                 "source": source, "fetched_at": time.time(),
                 "source_url": source_url, "path": str(path)}
        index["docs"].append(entry)
        self._save_index(index)
        return entry

    def prune_expired(self, ttl_s: float = DEFAULT_TTL_S) -> int:
        """Rimuove le voci WEB scadute (le pinned non scadono mai). Tiene la
        cache piccola/fresca. Ritorna quante ne ha potate."""
        now = time.time()
        index = self._load_index()
        keep, drop = [], []
        for d in index["docs"]:
            expired = (d.get("source") == "web"
                       and (now - float(d.get("fetched_at", 0))) > ttl_s)
            (drop if expired else keep).append(d)
        for d in drop:
            try:
                Path(d["path"]).unlink()
            except (OSError, KeyError):
                pass
        if drop:
            index["docs"] = keep
            self._save_index(index)
        return len(drop)

    def remove_doc(self, slug: str) -> bool:
        index = self._load_index()
        before = len(index["docs"])
        index["docs"] = [d for d in index["docs"] if d.get("slug") != slug]
        if len(index["docs"]) == before:
            return False
        try:
            (self.dir / f"{slug}.md").unlink()
        except FileNotFoundError:
            pass
        self._save_index(index)
        return True

    def match(self, *texts: str, limit: int = 3) -> List[Dict[str, Any]]:
        """Documenti le cui chiavi compaiono nei testi dati (task, import,
        errore). Ordinati per numero di chiavi colpite. Deterministico."""
        haystack = " \n ".join(t for t in texts if t).lower()
        if not haystack.strip():
            return []
        scored = []
        for doc in self._load_index()["docs"]:
            hits = sum(1 for k in doc.get("keys", []) if k and k in haystack)
            if hits:
                scored.append((hits, doc))
        scored.sort(key=lambda kv: kv[0], reverse=True)
        return [doc for _, doc in scored[:limit]]

    def build_context(self, *texts: str, limit: int = 3, max_chars: int = MAX_INJECT_CHARS) -> str:
        """Blocco di contesto con la doc ufficiale pertinente, pronto da
        anteporre al prompt del Coder. Vuoto se nessun match."""
        docs = self.match(*texts, limit=limit)
        return self._render(docs, max_chars)

    def _render(self, docs: List[Dict[str, Any]], max_chars: int) -> str:
        if not docs:
            return ""
        parts: List[str] = ["# DOCUMENTAZIONE UFFICIALE (usa QUESTE firme/endpoint, non inventarne altri)"]
        budget = max_chars
        for doc in docs:
            try:
                body = Path(doc["path"]).read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                continue
            snippet = body[: min(len(body), budget)]
            src = f" (fonte: {doc['source_url']})" if doc.get("source_url") else ""
            parts.append(f"## {doc.get('title', doc.get('slug'))}{src}\n{snippet}")
            budget -= len(snippet)
            if budget <= 0:
                break
        return "\n\n".join(parts) + "\n"

    def resolve_context(self, task: str, *, web_fetcher: Optional[Callable[[str], str]] = None,
                        allow_web: bool = True, ttl_s: float = DEFAULT_TTL_S,
                        max_chars: int = MAX_INJECT_CHARS) -> str:
        """Contesto doc per un task, INTERNET-FIRST.

        1. potatura voci web scadute;
        2. match su cache (pinned + web fresche): se c'e', usalo (niente
           re-fetch inutile);
        3. altrimenti, se online e c'e' un web_fetcher: FETCH LIVE, cache con
           TTL, usa il risultato;
        4. fallback: qualunque doc pinned che matcha (offline).

        web_fetcher(query) -> testo (es. search_coding_context); '' se nulla.
        """
        self.prune_expired(ttl_s)
        cached = self.match(task, limit=3)
        if cached:
            return self._render(cached, max_chars)

        if allow_web and web_fetcher:
            try:
                fetched = (web_fetcher(f"documentazione ufficiale {task}") or "").strip()
            except Exception:
                fetched = ""
            if fetched:
                keys = _keys_from_text(task)
                title = f"web: {task[:60]}"
                self.add_doc(title, fetched, keys=keys or [task[:40]], source="web")
                return self._render(self.match(task, limit=1), max_chars)

        # offline fallback: solo le pinned che matchano
        pinned = [d for d in self.match(task, limit=3) if d.get("source") == "pinned"]
        return self._render(pinned, max_chars)
