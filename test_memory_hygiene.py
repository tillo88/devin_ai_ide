"""
test_memory_hygiene.py — Repo hygiene per devin/memory/ (W7, 2026-07-18)

Dead artifacts rimossi: semantic_index.pkl (stale, untracked; il live usa
.devin_cache/semantic_index.json), stats.py (0 byte, mai importato),
brain.json (legacy, referenziato solo da docs archiviati / tree comment).
Questi test impediscono la reintroduzione. Path risolti dalla posizione del
test file: robusti a qualsiasi cwd.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
MEMORY_DIR = REPO_ROOT / "devin" / "memory"


def test_no_pkl_under_devin_memory():
    """Nessun *.pkl sotto devin/memory/: il pickle puo' eseguire codice in
    load e la cache live e' JSON versionato (.devin_cache/semantic_index.json)."""
    assert MEMORY_DIR.is_dir(), f"Directory mancante: {MEMORY_DIR}"
    strays = [p for p in MEMORY_DIR.rglob("*.pkl")
              if "__pycache__" not in p.parts]
    assert not strays, f"File .pkl reintrodotti sotto devin/memory/: {strays}"


def test_dead_memory_artifacts_stay_gone():
    """stats.py (vuoto, unimported) e brain.json (legacy, solo docs archiviate)
    non devono tornare."""
    for name in ("stats.py", "brain.json", "semantic_index.pkl"):
        assert not (MEMORY_DIR / name).exists(), \
            f"Dead artifact reintrodotto: devin/memory/{name}"
