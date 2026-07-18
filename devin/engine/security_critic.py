"""Security critic deterministico e OFFLINE per il codice generato (bandit).

Perche' bandit e non semgrep (roadmap punto 2, deciso 2026-07-16): semgrep
con `--config auto`/`p/python` scarica le regole dal registry online — contro
il principio local-first del progetto. Bandit e' pip-installabile, gira
completamente offline e copre il caso d'uso reale (il Coder scrive quasi
solo Python): subprocess con shell=True, eval/exec, password hardcoded,
pickle, yaml.load, SQL string-format, ecc.

Policy anti-rumore: i finding NON bocciano il gate (i security linter hanno
falsi positivi). Diventano `security_warnings` nell'evidenza del gate ->
finiscono nell'attempt -> il reviewer (umano/Claude/Teacher) decide se
escalare. Fail-open dichiarato se bandit non e' installato.

Install (opzionale): pip install bandit
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import List

_AVAILABLE: bool | None = None

# Solo MEDIUM+ di default: LOW e' quasi tutto rumore (assert, random, ecc.)
MIN_SEVERITY = {"MEDIUM", "HIGH"}
MAX_FILES = 40          # i run scaffold scrivono pochi file: cap difensivo
TIMEOUT_SECONDS = 60


def bandit_available() -> bool:
    global _AVAILABLE
    if _AVAILABLE is None:
        try:
            import bandit  # noqa: F401
            _AVAILABLE = True
        except Exception:
            _AVAILABLE = False
    return _AVAILABLE


def scan_python_files(root: str | Path, relatives: List[str]) -> List[str]:
    """Scansiona i file .py indicati (relativi a root) con bandit.

    Ritorna una lista di warning "file: [Bxxx SEVERITY] messaggio (riga N)".
    Lista vuota = nessun finding MEDIUM+ oppure bandit non disponibile
    (fail-open: e' il chiamante a sapere se bandit c'e', via bandit_available).
    """
    if not bandit_available():
        return []
    base = Path(root).resolve()
    targets = []
    for rel in relatives[:MAX_FILES]:
        # i gold test sono NOSTRI (iniettati dal training runner) e usano exec
        # di proposito: non sono codice del modello, non vanno scansionati
        # (era un falso positivo B102 visto sul campo 2026-07-16).
        if Path(rel).name.startswith("test_gold_"):
            continue
        p = (base / rel)
        try:
            p = p.resolve()
        except OSError:
            continue
        # mai uscire dal sandbox del progetto
        if (base in p.parents or p == base) and p.suffix.lower() == ".py" and p.is_file():
            targets.append(str(p))
    if not targets:
        return []

    try:
        proc = subprocess.run(
            [sys.executable, "-m", "bandit", "-q", "-f", "json", "--exit-zero", *targets],
            capture_output=True, text=True, timeout=TIMEOUT_SECONDS, cwd=str(base),
        )
        data = json.loads(proc.stdout or "{}")
    except subprocess.TimeoutExpired:
        return [f"security scan: timeout dopo {TIMEOUT_SECONDS}s (nessun verdetto)"]
    except Exception as exc:
        return [f"security scan non riuscito: {type(exc).__name__}: {exc}"]

    warnings: List[str] = []
    for result in data.get("results", []):
        severity = str(result.get("issue_severity", "")).upper()
        if severity not in MIN_SEVERITY:
            continue
        try:
            rel = str(Path(result.get("filename", "")).resolve().relative_to(base))
        except Exception:
            rel = result.get("filename", "?")
        warnings.append(
            f"{rel}: [{result.get('test_id', '?')} {severity}] "
            f"{result.get('issue_text', '')} (riga {result.get('line_number', '?')})"
        )
    return warnings[:20]
