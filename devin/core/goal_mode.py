"""Goal Mode — fondazione (Fase 1): oggetto Goal + valutatore di checklist.

Design di riferimento: docs/devin_roadmap_skills-goalmode_v2.md

Questa e' la base su cui poggia la Goal Mode: NON avvia run, NON usa modelli, NON
tocca la VRAM. Definisce:

- `Goal`: obiettivo + checklist di accettazione + vincoli + modalita'.
- Criteri di accettazione **verificabili a macchina** (decisione D1): ogni criterio
  ha un tipo e dei parametri; `evaluate_goal()` li verifica contro la cartella di
  un progetto e ritorna un report strutturato. Pura logica su filesystem +
  subprocess, quindi testabile offline.
- `requires_checkpoint()`: la politica di approvazione (D4) — scaffold gira in
  loop senza stop, maintenance si ferma su `awaiting_approval` salvo auto-approva.

Nessun criterio esegue codice del progetto a meno che non sia esplicitamente un
criterio `tests_pass` o `command_succeeds` (che il chiamante decide di includere).
I criteri su file sono di sola lettura.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

SCHEMA = "goal_v1"

# Modalita' del goal.
MODE_SCAFFOLD = "scaffold"
MODE_MAINTENANCE = "maintenance"
VALID_MODES = {MODE_SCAFFOLD, MODE_MAINTENANCE}

# Politica di approvazione delle modifiche.
APPROVAL_AUTO = "auto"
APPROVAL_MANUAL = "manual"
VALID_APPROVALS = {APPROVAL_AUTO, APPROVAL_MANUAL}

# Tipi di criterio supportati (D1: tutti verificabili a macchina).
CRITERION_TYPES = {
    "file_exists",         # params: {path}
    "absence_of_pattern",  # params: {pattern, globs?}
    "contains_text",       # params: {path, text}
    "command_succeeds",    # params: {argv, timeout?}
    "tests_pass",          # params: {timeout?}
}

# Directory che non vanno mai scandite per i criteri su file.
_SKIP_DIRS = {
    ".git", ".pytest_cache", "__pycache__", "node_modules", "dist", "build",
    "logs", "venv", ".venv", ".venv-win", ".venv-rig", "workspace", "archive",
    ".devin_state",
}


class GoalError(ValueError):
    """Goal malformato o criterio non valido."""


@dataclass
class Criterion:
    """Un singolo item della checklist di accettazione."""

    type: str
    params: dict[str, Any] = field(default_factory=dict)
    label: str = ""

    def validate(self) -> None:
        if self.type not in CRITERION_TYPES:
            raise GoalError(f"tipo criterio sconosciuto: {self.type!r}")
        if self.type in {"file_exists", "contains_text"} and not self.params.get("path"):
            raise GoalError(f"criterio {self.type}: manca 'path'")
        if self.type == "absence_of_pattern" and not self.params.get("pattern"):
            raise GoalError("criterio absence_of_pattern: manca 'pattern'")
        if self.type == "contains_text" and "text" not in self.params:
            raise GoalError("criterio contains_text: manca 'text'")
        if self.type == "command_succeeds" and not self.params.get("argv"):
            raise GoalError("criterio command_succeeds: manca 'argv'")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Criterion":
        return cls(
            type=data["type"],
            params=dict(data.get("params") or {}),
            label=data.get("label", ""),
        )


def _criterion_from_dsl(token: str) -> "Criterion":
    """Mini-DSL testuale per un criterio (usata da CLI e API).

    tests_pass | file_exists:PATH | contains:PATH:TESTO | absence:REGEX |
    command:PROG ARG ARG
    """
    if token == "tests_pass":
        return Criterion("tests_pass", {})
    if token.startswith("file_exists:"):
        return Criterion("file_exists", {"path": token.split(":", 1)[1]})
    if token.startswith("contains:"):
        _, path, text = token.split(":", 2)
        return Criterion("contains_text", {"path": path, "text": text})
    if token.startswith("absence:"):
        return Criterion("absence_of_pattern", {"pattern": token.split(":", 1)[1]})
    if token.startswith("command:"):
        return Criterion("command_succeeds", {"argv": token.split(":", 1)[1].split()})
    raise GoalError(f"criterio non riconosciuto: {token!r}")


def parse_acceptance(items: list) -> list["Criterion"]:
    """Normalizza una lista mista (Criterion | dict goal_v1 | stringa DSL)."""
    out: list[Criterion] = []
    for item in items or []:
        if isinstance(item, Criterion):
            out.append(item)
        elif isinstance(item, dict):
            out.append(Criterion.from_dict(item))
        elif isinstance(item, str):
            out.append(_criterion_from_dsl(item))
        else:
            raise GoalError(f"criterio di tipo non supportato: {type(item).__name__}")
    return out


@dataclass
class Goal:
    """Obiettivo autonomo con criteri di accettazione e vincoli."""

    objective: str
    acceptance: list[Criterion] = field(default_factory=list)
    mode: str = MODE_MAINTENANCE
    approval_policy: str = APPROVAL_MANUAL
    budget_steps: int = 20
    budget_seconds: int = 3600
    allow: list[str] = field(default_factory=list)  # whitelist glob (vuota = tutto)
    deny: list[str] = field(default_factory=list)   # blacklist glob
    schema: str = SCHEMA

    def validate(self) -> None:
        if not self.objective or not self.objective.strip():
            raise GoalError("objective vuoto")
        if self.mode not in VALID_MODES:
            raise GoalError(f"mode non valido: {self.mode!r}")
        if self.approval_policy not in VALID_APPROVALS:
            raise GoalError(f"approval_policy non valida: {self.approval_policy!r}")
        if not self.acceptance:
            raise GoalError("acceptance vuota: un goal senza criteri non ha condizione di successo")
        if self.budget_steps <= 0 or self.budget_seconds <= 0:
            raise GoalError("budget deve essere positivo")
        for criterion in self.acceptance:
            criterion.validate()

    def requires_checkpoint(self) -> bool:
        """Politica di approvazione (D4).

        - scaffold: mai stop, loop autonomo -> False.
        - maintenance: stop su awaiting_approval, salvo approval_policy == auto.
        """
        if self.mode == MODE_SCAFFOLD:
            return False
        return self.approval_policy != APPROVAL_AUTO

    def path_allowed(self, rel_path: str) -> bool:
        """Un percorso (relativo alla root) rispetta i vincoli allow/deny?

        deny ha precedenza; se allow e' non vuota, il path deve matchare almeno
        un pattern. Separatori normalizzati a "/" per essere OS-agnostici.
        """
        p = PurePosixPath(str(rel_path).replace("\\", "/"))
        if any(p.match(pattern.replace("\\", "/")) for pattern in self.deny):
            return False
        if self.allow and not any(p.match(pattern.replace("\\", "/")) for pattern in self.allow):
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["acceptance"] = [c.to_dict() for c in self.acceptance]
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Goal":
        goal = cls(
            objective=data.get("objective", ""),
            acceptance=[Criterion.from_dict(c) for c in (data.get("acceptance") or [])],
            mode=data.get("mode", MODE_MAINTENANCE),
            approval_policy=data.get("approval_policy", APPROVAL_MANUAL),
            budget_steps=int(data.get("budget_steps", 20)),
            budget_seconds=int(data.get("budget_seconds", 3600)),
            allow=list(data.get("allow") or []),
            deny=list(data.get("deny") or []),
            schema=data.get("schema", SCHEMA),
        )
        return goal


@dataclass
class CriterionResult:
    criterion: Criterion
    passed: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"criterion": self.criterion.to_dict(), "passed": self.passed, "detail": self.detail}


@dataclass
class GoalEvaluation:
    results: list[CriterionResult] = field(default_factory=list)

    @property
    def satisfied(self) -> bool:
        return bool(self.results) and all(r.passed for r in self.results)

    @property
    def pending(self) -> list[CriterionResult]:
        return [r for r in self.results if not r.passed]

    def to_dict(self) -> dict[str, Any]:
        return {
            "satisfied": self.satisfied,
            "results": [r.to_dict() for r in self.results],
        }


def evaluate_goal(goal: Goal, project_root: Path | str, *, execute: bool = True) -> GoalEvaluation:
    """Valuta tutti i criteri di accettazione contro `project_root`.

    `execute=False` salta i criteri che eseguono processi (`tests_pass`,
    `command_succeeds`): utile per una valutazione "dry" senza effetti.
    """
    goal.validate()
    root = Path(project_root)
    results: list[CriterionResult] = []
    for criterion in goal.acceptance:
        results.append(_evaluate_criterion(criterion, root, execute=execute))
    return GoalEvaluation(results=results)


def _evaluate_criterion(criterion: Criterion, root: Path, *, execute: bool) -> CriterionResult:
    try:
        criterion.validate()
        handler = _HANDLERS[criterion.type]
        return handler(criterion, root, execute)
    except GoalError as exc:
        return CriterionResult(criterion, False, f"criterio non valido: {exc}")
    except Exception as exc:  # difensivo: un criterio non deve mai far crashare il loop
        return CriterionResult(criterion, False, f"{type(exc).__name__}: {exc}")


def _check_file_exists(criterion: Criterion, root: Path, execute: bool) -> CriterionResult:
    target = root / criterion.params["path"]
    ok = target.exists()
    return CriterionResult(criterion, ok, "trovato" if ok else "file mancante")


def _check_contains_text(criterion: Criterion, root: Path, execute: bool) -> CriterionResult:
    target = root / criterion.params["path"]
    if not target.exists():
        return CriterionResult(criterion, False, "file mancante")
    text = target.read_text(encoding="utf-8", errors="ignore")
    needle = str(criterion.params["text"])
    ok = needle in text
    return CriterionResult(criterion, ok, "testo presente" if ok else "testo assente")


def _check_absence_of_pattern(criterion: Criterion, root: Path, execute: bool) -> CriterionResult:
    pattern = re.compile(criterion.params["pattern"])
    globs = criterion.params.get("globs") or ["*.py"]
    for path in _iter_files(root, globs):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        match = pattern.search(text)
        if match:
            rel = path.relative_to(root).as_posix()
            return CriterionResult(criterion, False, f"pattern trovato in {rel}: {match.group(0)[:40]!r}")
    return CriterionResult(criterion, True, "pattern assente")


def _check_command_succeeds(criterion: Criterion, root: Path, execute: bool) -> CriterionResult:
    if not execute:
        return CriterionResult(criterion, False, "non eseguito (execute=False)")
    argv = list(criterion.params["argv"])
    timeout = int(criterion.params.get("timeout", 180))
    proc = _run(argv, root, timeout)
    ok = proc["returncode"] == 0
    return CriterionResult(criterion, ok, f"exit {proc['returncode']}: {proc['output'][-200:]}")


def _check_tests_pass(criterion: Criterion, root: Path, execute: bool) -> CriterionResult:
    if not execute:
        return CriterionResult(criterion, False, "non eseguito (execute=False)")
    timeout = int(criterion.params.get("timeout", 180))
    # Ignora le dir rumorose (soprattutto il sandbox annidato `workspace/`, che
    # contiene COPIE dei file di test): due `test_x.py` con lo stesso nome fanno
    # fallire pytest con "import file mismatch". `--import-mode=importlib` elimina
    # anche la collisione di basename.
    ignores: list[str] = []
    for name in _SKIP_DIRS:
        d = root / name
        if d.is_dir():
            ignores += ["--ignore", str(d)]
    # Stesso approccio del quality gate dell'orchestrator: pytest, fallback unittest.
    for argv in (
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "--import-mode=importlib", "--maxfail=20", *ignores],
        [sys.executable, "-m", "unittest", "discover", "-v"],
    ):
        proc = _run(argv, root, timeout)
        if "No module named pytest" in proc["output"] or "No module named 'pytest'" in proc["output"]:
            continue
        ok = proc["returncode"] == 0
        runner = "pytest" if "pytest" in argv[2] else "unittest"
        return CriterionResult(criterion, ok, f"{runner} exit {proc['returncode']}")
    return CriterionResult(criterion, False, "nessun test runner disponibile")


_HANDLERS = {
    "file_exists": _check_file_exists,
    "contains_text": _check_contains_text,
    "absence_of_pattern": _check_absence_of_pattern,
    "command_succeeds": _check_command_succeeds,
    "tests_pass": _check_tests_pass,
}


def _iter_files(root: Path, globs: list[str]):
    for pattern in globs:
        for path in root.rglob(pattern):
            if not path.is_file():
                continue
            if any(part in _SKIP_DIRS for part in path.relative_to(root).parts):
                continue
            yield path


def _run(argv: list[str], root: Path, timeout: int) -> dict[str, Any]:
    # PYTHONDONTWRITEBYTECODE come nel gate: evita .pyc stale nei loop di heal.
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    try:
        proc = subprocess.run(
            argv, cwd=str(root), capture_output=True, text=True, timeout=timeout, env=env,
        )
    except subprocess.TimeoutExpired:
        return {"returncode": 124, "output": f"timeout dopo {timeout}s"}
    except Exception as exc:
        return {"returncode": 1, "output": f"{type(exc).__name__}: {exc}"}
    output = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    return {"returncode": proc.returncode, "output": output}
