"""Case-specific semantic validators for training eval attempts.

Why this exists (2026-07-15): the mini bench reported "ok 3" but manual
validation found 2 real failures. `auto_success` was decided only by "the
scaffold wrote files without errors", which says nothing about whether the
task was actually accomplished. These validators turn each case's
`expected_signals` into machine checks where possible, so an attempt can be
downgraded to `auto_failure` BEFORE it pollutes the review queue with fake
successes.

Policy unchanged: validators can only make the automatic verdict STRICTER
(auto_success -> auto_failure). They never promote anything to
verified_success — that stays with Teacher/human review.

Signals that cannot be machine-checked (e.g. `tests_fail_first`, which would
require observing the red->green sequence) are reported as
`not_machine_checkable` and left to the reviewer.
"""

import re
from pathlib import Path
from typing import Any, Dict, List

# URL allowlists by case tag, used when the case metadata does not carry an
# explicit `allowed_url_prefixes`. Keep prefixes, not regexes: predictable.
DEFAULT_URL_ALLOWLISTS = {
    "steam": [
        "https://api.steampowered.com/",
        "http://api.steampowered.com/",
        "https://steamcommunity.com/",
        "https://partner.steam-api.com/",
        "https://store.steampowered.com/api/",
    ],
}

# Generic prefixes never counted as "invented endpoints" (docs/examples).
NEUTRAL_URL_PREFIXES = (
    "https://example.com",
    "http://example.com",
    "http://localhost",
    "https://localhost",
    "http://127.0.0.1",
    "https://www.w3.org/",
    "http://www.w3.org/",
)

URL_RE = re.compile(r"https?://[^\s'\"<>)\]\\,;]+")

# Mock REALI contati solo in file .py (2026-07-18, tightening approvato):
# prima MOCK_HINT_RE matchava la parola "mock" in QUALUNQUE file scritto
# (README, commenti, TODO) -> completamento superficiale premiato. Ora serve
# un vero import unittest.mock/mock oppure l'uso di Mock(/patch()/monkeypatch/
# responses/respx nel codice sorgente Python.
MOCK_IMPORT_RE = re.compile(
    r"^\s*(?:from\s+unittest\s+import\s+mock\b"
    r"|from\s+unittest\.mock\s+import\b"
    r"|import\s+unittest\.mock\b"
    r"|import\s+mock\b"
    r"|from\s+mock\s+import\b)",
    re.MULTILINE,
)
MOCK_USAGE_RE = re.compile(
    r"\b(?:MagicMock|AsyncMock|Mock|PropertyMock)\s*\("
    r"|\bpatch\s*\("
    r"|\bmonkeypatch\s*[.)]"
    r"|\bresponses\s*\.\s*(?:add|activate|start|stop|RequestsMock)"
    r"|\brespx\s*\."
)

MAX_SCAN_BYTES = 200_000  # per file: enough for generated code, keeps scans cheap


def _read_written_files(project_path: str, written: List[str]) -> "tuple[Dict[str, str], List[str]]":
    """Ritorna (files leggibili, skipped). FIX (2026-07-18): prima i file
    scritti ma illeggibili/mancanti venivano saltati in silenzio — cosi'
    `file_created` riportava "N file scritti" su un sottoinsieme e
    `no_invented_endpoint` non scansionava i file saltati. Ora i saltati
    sono evidenza esplicita per i validatori."""
    root = Path(project_path).resolve()
    out: Dict[str, str] = {}
    skipped: List[str] = []
    for rel in written or []:
        try:
            path = (root / rel).resolve()
            # never follow paths escaping the sandbox (defense in depth: the
            # orchestrator already writes through _safe_project_target)
            if root not in path.parents and path != root:
                skipped.append(rel)
                continue
            if path.is_file():
                out[rel] = path.read_text(encoding="utf-8", errors="ignore")[:MAX_SCAN_BYTES]
            else:
                skipped.append(rel)
        except Exception:
            skipped.append(rel)
    return out, skipped


def _allowed_prefixes_for(case: Dict[str, Any]) -> List[str]:
    meta = case.get("metadata") or {}
    explicit = meta.get("allowed_url_prefixes")
    if isinstance(explicit, list) and explicit:
        return [str(p) for p in explicit]
    prefixes: List[str] = []
    for tag in case.get("tags") or []:
        prefixes.extend(DEFAULT_URL_ALLOWLISTS.get(str(tag).lower(), []))
    return prefixes


def _check_no_invented_endpoint(case: Dict[str, Any], files: Dict[str, str]) -> Dict[str, Any]:
    allowed = _allowed_prefixes_for(case)
    offending: List[str] = []
    seen: set = set()
    for rel, text in files.items():
        for url in URL_RE.findall(text):
            url_clean = url.rstrip(".,;:'\")")
            if url_clean in seen:
                continue
            seen.add(url_clean)
            if url_clean.startswith(NEUTRAL_URL_PREFIXES):
                continue
            if allowed and any(url_clean.startswith(p) for p in allowed):
                continue
            if not allowed:
                # no allowlist available: cannot judge -> leave to reviewer
                return {"verdict": "not_machine_checkable",
                        "detail": "nessuna allowlist per questo caso: URL trovati ma non giudicabili"}
            offending.append(f"{rel}: {url_clean}")
    if offending:
        return {"verdict": "fail",
                "detail": "endpoint fuori allowlist: " + "; ".join(offending[:10])}
    return {"verdict": "pass",
            "detail": f"nessun endpoint fuori allowlist ({len(seen)} URL analizzati)"}


def _check_tests_pass(quality: Dict[str, Any]) -> Dict[str, Any]:
    if not quality:
        return {"verdict": "fail", "detail": "quality gate assente dal risultato"}
    if not quality.get("tests_run"):
        return {"verdict": "fail",
                "detail": "il caso richiede test ma nessun test e' stato trovato/eseguito"}
    if quality.get("errors"):
        return {"verdict": "fail",
                "detail": "test eseguiti ma FALLITI: " + "; ".join(quality["errors"])[:500]}
    return {"verdict": "pass", "detail": f"test eseguiti e passati ({quality.get('test_command') or 'tests'})"}


def _check_tests_or_mocks(quality: Dict[str, Any], files: Dict[str, str]) -> Dict[str, Any]:
    if quality.get("tests_run") and not quality.get("errors"):
        return {"verdict": "pass", "detail": "test eseguiti e passati"}
    # Solo evidenza macchinabile: import/uso reale di unittest.mock in file
    # .py. La parola "mock" in doc/commenti NON conta piu' (fail con motivo
    # esplicito, convenzione esistente di questo check: niente fail-soft).
    py_sources = [text for rel, text in files.items() if str(rel).endswith(".py")]
    has_real_mocks = any(
        MOCK_IMPORT_RE.search(text) or MOCK_USAGE_RE.search(text)
        for text in py_sources
    )
    if has_real_mocks:
        if quality.get("tests_run") and quality.get("errors"):
            return {"verdict": "fail", "detail": "mock presenti ma test FALLITI"}
        return {"verdict": "pass",
                "detail": "uso reale di unittest.mock nei file .py generati"}
    return {"verdict": "fail",
            "detail": ("ne' test eseguiti con successo ne' uso reale di unittest.mock "
                       "nei file .py (la parola 'mock' in doc/commenti non conta)")}


def validate_case(case: Dict[str, Any], result: Dict[str, Any], project_path: str) -> Dict[str, Any]:
    """Evaluate a finished training attempt against the case's expected_signals.

    Returns {"overall": "pass"|"fail"|"unknown", "signals": {...},
             "machine_checked": N, "not_machine_checkable": [...]}.
    "fail" means: at least one machine-checkable expectation is violated ->
    the caller should downgrade auto_success to auto_failure.
    """
    signals = [str(s) for s in (case.get("expected_signals") or [])]
    quality = result.get("quality_gate") or {}
    written = result.get("files_written") or []
    files, skipped = _read_written_files(project_path, written)

    checks: Dict[str, Dict[str, Any]] = {}
    for sig in signals:
        if sig == "file_created":
            if skipped:
                checks[sig] = {"verdict": "fail",
                               "detail": f"{len(files)} file leggibili ma {len(skipped)} "
                                         f"dichiarati scritti risultano MANCANTI/ILLEGIBILI: "
                                         + ", ".join(skipped[:5])}
            else:
                checks[sig] = ({"verdict": "pass", "detail": f"{len(files)} file scritti e leggibili"}
                               if files else {"verdict": "fail", "detail": "nessun file scritto/leggibile"})
        elif sig in {"tests_pass", "tests_pass_after_fix"}:
            checks[sig] = _check_tests_pass(quality)
        elif sig == "tests_or_mocks":
            checks[sig] = _check_tests_or_mocks(quality, files)
        elif sig == "no_invented_endpoint":
            checks[sig] = _check_no_invented_endpoint(case, files)
        else:
            checks[sig] = {"verdict": "not_machine_checkable",
                           "detail": "richiede review umana/Teacher"}

    machine = {k: v for k, v in checks.items() if v["verdict"] in {"pass", "fail"}}
    unchecked = [k for k, v in checks.items() if v["verdict"] == "not_machine_checkable"]

    if any(v["verdict"] == "fail" for v in machine.values()):
        overall = "fail"
    elif machine:
        overall = "pass"
    else:
        overall = "unknown"

    return {
        "overall": overall,
        "signals": checks,
        "machine_checked": len(machine),
        "not_machine_checkable": unchecked,
        "skipped_files": skipped,
    }


def decision_reason(validation: Dict[str, Any]) -> str:
    """Compact human-readable reason for the downgrade (goes into error_reason)."""
    fails = [f"{sig}: {info.get('detail', '')}"
             for sig, info in (validation.get("signals") or {}).items()
             if info.get("verdict") == "fail"]
    return " | ".join(fails)[:800]
