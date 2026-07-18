"""Explicit public-benchmark adapters for DEVIN training mode.

Regole (da CONTINUITY/roadmap): NIENTE download silenziosi. Ogni import parte
da un'azione esplicita dell'utente (bottone + conferma in Diagnostics, o
chiamata API deliberata). I dataset scaricati finiscono in una cache locale
dentro la cartella training, mai in giro per il repo.

Primo adapter: MBPP (Mostly Basic Python Problems, ~974 task). Ogni task ha
`test_list` con assert ufficiali -> diventano GOLD TESTS iniettati nel sandbox
dal training runner (stesso meccanismo dei gold del mini bench): il quality
gate li esegue e il modello non puo' "auto-promuoversi" con test deboli.
"""

import json
import urllib.request
from pathlib import Path
from typing import Any, Dict, List

MBPP_URL = "https://raw.githubusercontent.com/google-research/google-research/master/mbpp/mbpp.jsonl"
MBPP_MIN_ROWS = 900          # il file ufficiale ha ~974 righe: meno = download rotto
MBPP_MAX_BYTES = 30_000_000  # sanity cap

_GOLD_HEADER = '''"""Gold test MBPP {task_id} — iniettato dal training runner, NON scritto dal modello."""
import pathlib


def _load_namespace():
    ns = {{}}
    here = pathlib.Path(__file__).parent
    for path in sorted(here.glob("*.py")):
        if path.name.startswith("test") or path.name.endswith("_test.py"):
            continue
        try:
            exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), ns)
        except Exception:
            continue
    return ns


def test_gold_mbpp_{task_id}():
    ns = _load_namespace()
'''


def mbpp_cache_path(base_dir: str | Path) -> Path:
    return Path(base_dir) / "benchmarks_cache" / "mbpp.jsonl"


def download_mbpp(base_dir: str | Path, force: bool = False, timeout: int = 120) -> Dict[str, Any]:
    """Download ESPLICITO del dataset MBPP nella cache locale.

    Ritorna {path, rows, downloaded}. Se la cache esiste ed e' valida (e non
    force), non tocca la rete. Qualunque anomalia (dimensione, righe, JSON)
    scarta il file scaricato e solleva ValueError.
    """
    target = mbpp_cache_path(base_dir)
    if target.exists() and not force:
        rows = _read_rows(target)
        if len(rows) >= MBPP_MIN_ROWS:
            return {"path": str(target), "rows": len(rows), "downloaded": False}

    req = urllib.request.Request(MBPP_URL, headers={"User-Agent": "devin-ai-ide-training/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read(MBPP_MAX_BYTES + 1)
    if len(raw) > MBPP_MAX_BYTES:
        raise ValueError(f"MBPP download troppo grande (> {MBPP_MAX_BYTES} byte): scartato")

    text = raw.decode("utf-8", errors="strict")
    rows = []
    for line in text.splitlines():
        if not line.strip():
            continue
        item = json.loads(line)  # una riga malformata = download corrotto: meglio fallire
        if isinstance(item, dict):
            rows.append(item)
    if len(rows) < MBPP_MIN_ROWS:
        raise ValueError(f"MBPP: solo {len(rows)} righe valide (attese >= {MBPP_MIN_ROWS}): scartato")

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return {"path": str(target), "rows": len(rows), "downloaded": True}


def _read_rows(path: Path) -> List[Dict[str, Any]]:
    rows = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except Exception:
                continue
            if isinstance(item, dict):
                rows.append(item)
    except OSError:
        return []
    return rows


def build_mbpp_gold_test(task_id: Any, test_setup_code: str, test_list: List[str]) -> str:
    """Gli assert ufficiali MBPP diventano un gold test pytest autonomo.

    I moduli del sandbox vengono exec-uiti in un namespace condiviso (nome file
    libero: il modello chiama il modulo come vuole, conta solo che la funzione
    esista col nome richiesto dagli assert)."""
    body = _GOLD_HEADER.format(task_id=task_id)
    lines = []
    setup = (test_setup_code or "").strip()
    if setup:
        lines.append(f"    exec({setup!r}, ns)")
    for assertion in test_list or []:
        assertion = str(assertion).strip()
        if assertion:
            lines.append(f"    exec({assertion!r}, ns)")
    if not lines:
        lines.append("    raise AssertionError('MBPP task senza test ufficiali')")
    return body + "\n".join(lines) + "\n"


def mbpp_rows_to_cases(rows: List[Dict[str, Any]], limit: int = 10, offset: int = 0) -> List[Dict[str, Any]]:
    """Converte righe MBPP in casi training DEVIN (formato seed_cases).

    Il prompt include i reference test (convenzione MBPP: senza, il nome della
    funzione sarebbe indovinabile solo per caso). expected_signals=[tests_pass]
    -> il validatore esige che il gate abbia eseguito test verdi."""
    cases = []
    for row in rows[offset:offset + max(1, limit)]:
        task_id = row.get("task_id")
        text = (row.get("text") or "").strip()
        test_list = [str(t) for t in (row.get("test_list") or [])]
        if task_id is None or not text or not test_list:
            continue
        prompt = (
            f"{text}\n\n"
            "Reference tests (your function name and signature MUST satisfy these):\n"
            + "\n".join(test_list)
            + "\n\nWrite a single Python module implementing the function, plus your own "
              "pytest tests. No network access, no extra dependencies."
        )
        cases.append({
            "title": f"MBPP {task_id}: {text[:70]}",
            "kind": "code_generation",
            "prompt": prompt,
            "expected_signals": ["tests_pass"],
            "tags": ["python", "mbpp"],
            "mbpp_task_id": task_id,
            "gold_tests": {
                f"test_gold_mbpp_{task_id}.py": build_mbpp_gold_test(
                    task_id, row.get("test_setup_code") or "", test_list),
            },
        })
    return cases


def import_mbpp_cases(store, limit: int = 10, offset: int = 0,
                      force_download: bool = False) -> Dict[str, Any]:
    """Pipeline completa: download (esplicito) -> conversione -> seed nello store."""
    info = download_mbpp(store.base_dir, force=force_download)
    rows = _read_rows(Path(info["path"]))
    cases = mbpp_rows_to_cases(rows, limit=limit, offset=offset)
    created = store.seed_cases(cases, source="mbpp")
    return {
        "dataset_rows": info["rows"],
        "downloaded_now": info["downloaded"],
        "converted": len(cases),
        "created": len(created),
        "skipped_existing": len(cases) - len(created),
        "source": "mbpp",
        "cache": info["path"],
    }
