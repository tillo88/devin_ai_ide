"""
DEVIN AI IDE - Orchestrator
Loop principale: Planner -> Coder -> Patcher -> Runner -> Critic
Con auto-start modelli locali via LocalModelLauncher.

TASK 13: Persistenza stato per recovery da crash.
FASE 1: Serializzazione VRAM (swap Planner/Coder), integrazione VectorStore semantic search
FASE 3: Self-Healing (Critic su errori di tool, non solo di runner) + Zero-Shot Scaffolding
"""

import os
import json
import hashlib
import re
import subprocess
import sys
import time
import shutil
import traceback
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime

from devin.ai.client import AIClient
from devin.ai.hybrid_memory_client import HybridMemoryClient, project_tags
from devin.ai.local_model_launcher import (
    LocalModelLauncher, LauncherStatus,
    swap_model, get_vram_status, start_vram_watchdog
)
from devin.core.context_engine import ContextEngine
from devin.core.context_retriever import ContextRetriever
from devin.core.state_persistence import StatePersistence
from devin.agents.planner import Planner
from devin.agents.coder import Coder
from devin.agents.critic import Critic
from devin.engine.patcher import Patcher
from devin.engine.runner import Runner
from devin.engine.syntax_critic import check_text as syntax_check_text
from devin.engine.security_critic import bandit_available, scan_python_files
from devin.core.loop_runner import run_loop, VerifyResult
from devin.core.docs_cache import DocsCache

# Docs cache CONDIVISA tra progetti (doc ufficiali per libreria/API), rooted
# alla workspace del repo cosi' orchestrator e API vedono lo stesso store.
_REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS_CACHE_ROOT = _REPO_ROOT / "workspace"


def _is_test_filename(rel_path: str) -> bool:
    name = Path(rel_path).name.lower()
    return name == "tests.py" or name.startswith("test_") or name.endswith("_test.py")
from devin.engine.git_ops import GitOps
from devin.memory.vector_store import VectorStore
from devin.memory.taxonomy import build_memory_tags
from devin.memory.eval_recorder import _existing_memory_keys, _memory_key

LOG_DIR = Path(__file__).resolve().parents[2] / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# Stesso principio di client.py: default ancorato alla posizione del file, non alla CWD.
_DEFAULT_CONFIG_PATH = str(Path(__file__).resolve().parents[2] / "config" / "settings.json")


def _safe_project_target(project_path: str, relative_path: str) -> Path:
    """Return a project-local target, rejecting absolute paths and traversal."""
    root = Path(project_path).expanduser().resolve()
    raw = str(relative_path or "").strip()
    if not raw or Path(raw).is_absolute():
        raise ValueError(f"Percorso file non sicuro: {relative_path!r}")
    target = (root / raw).resolve()
    if target == root or root not in target.parents:
        raise ValueError(f"Percorso fuori dal progetto: {relative_path!r}")
    return target


class Orchestrator:
    MAX_RETRIES = 3

    def __init__(
        self,
        config_path: str = _DEFAULT_CONFIG_PATH,
        project_path: str = None,
        sse_callback=None
    ):
        self.config_path = config_path
        self.project_path = project_path or os.getcwd()
        self.sse_callback = sse_callback
        self._should_stop = False
        self._state_persistence = None
        self._degraded_mode = False  # True se il rig (primario) è down e giriamo su locale

        with open(config_path, "r") as f:
            self.config = json.load(f)

        # FASE 1: Configurazione serializzazione VRAM
        models_config = self.config.get("models", {})
        self.local_test_mode = bool(models_config.get("local_test_mode", False))
        self.serialize_vram = bool(
            models_config.get("serialize_vram_heavy_models", False)
            or self.local_test_mode
        )
        self.vram_swap_threshold_mb = models_config.get("vram_swap_threshold_mb", 2048)

        self.model_launcher = None
        self.model_status = None
        try:
            self.model_launcher = LocalModelLauncher.from_config(
                config_path,
                sse_callback=sse_callback
            )
            self._log("LocalModelLauncher initialized", "info")
        except Exception as e:
            self._log(f"LocalModelLauncher init warning: {e}", "warning")

        self.context_engine = ContextEngine(
            max_chars=self.config.get("context", {}).get("max_chars", 100000)
        )
        # UN SOLO VectorStore: run() indicizza self.vector_store e il
        # retriever cerca sullo stesso — prima erano due istanze distinte
        # e il contesto semantico risultava SEMPRE vuoto (silenzioso).
        self.vector_store = VectorStore()
        self.context_retriever = ContextRetriever(
            enabled=self.config.get("context", {}).get("semantic_search_enabled", True),
            store=self.vector_store
        )

        self.ai_client = AIClient()
        self.planner = Planner(self.ai_client, self.config)
        self.coder = Coder(self.ai_client)
        self.critic = Critic(self.ai_client)

        self.patcher = Patcher()
        self.runner = Runner()
        self.git_ops = GitOps(self.project_path)
        self.memory_client = HybridMemoryClient(self.config)

        self._log("Orchestrator initialized", "info")

    def _log(self, message: str, level: str = "info"):
        if self.sse_callback:
            try:
                self.sse_callback(message, level)
            except Exception:
                pass
        print(f"[{level.upper()}] {message}")

    def stop(self):
        """Richiede l'arresto del run in corso."""
        self._should_stop = True
        try:
            self.runner.stop()
        except Exception as exc:
            self._log(f"Runner stop warning: {exc}", "warning")
        self._log("Stop requested by user", "warning")

    def _sync_sandbox_to_project(self, sandbox_path: str, project_path: str):
        """Copy all generated/modified source files back, regardless of language."""
        sandbox = Path(sandbox_path).resolve()
        project = Path(project_path).resolve()
        excluded_parts = {
            "workspace", "venv", ".venv", "env", ".git", "__pycache__",
            ".pytest_cache", "node_modules", "dist", "build", "logs",
            ".devin", ".devin_chat", ".devin_cache", ".devin_state",
        }
        excluded_suffixes = {".pyc", ".pyo", ".rej", ".orig", ".tmp", ".bak", ".gguf"}

        for src_file in sandbox.rglob("*"):
            if not src_file.is_file():
                continue
            rel_parts = src_file.relative_to(sandbox).parts
            if any(part in excluded_parts for part in rel_parts):
                continue
            if src_file.suffix.lower() in excluded_suffixes:
                continue

            rel_path = src_file.relative_to(sandbox)
            dest_file = _safe_project_target(str(project), str(rel_path))
            if dest_file.exists() and dest_file.read_bytes() == src_file.read_bytes():
                continue

            dest_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src_file), str(dest_file))

    # ============================================================
    # SELF-HEALING (Regola di sviluppo #2)
    # Il Critic tenta l'auto-correzione degli errori di TOOL (Coder/Patcher/
    # GitOps/scaffold write) PRIMA che l'errore raw venga propagato al retry
    # cieco o notificato all'utente. Se il Critic stesso è irraggiungibile
    # (es. rig down durante il fallimento), fallback silenzioso all'errore raw:
    # non deve mai far crashare l'orchestratore.
    # ============================================================

    def _self_heal(
        self,
        stage: str,
        error: str,
        patch: str = "",
        context: str = "",
        sandbox_files: Optional[Dict[str, str]] = None
    ) -> str:
        self._log(f"[SELF-HEAL] {stage} failed: {error} — invio al Critic per auto-correzione", "warning")
        context = context + self._maybe_web_reference(error)
        swapped_for_critic = False
        try:
            if self._degraded_mode and self.serialize_vram:
                swapped_for_critic = self._check_vram_and_swap("planner", "coder")
                if not swapped_for_critic:
                    raise RuntimeError("planner locale non disponibile per il Critic")
            critique = self.critic.analyze(error, patch, context, sandbox_files=sandbox_files)
            self._log(f"[SELF-HEAL] Critic feedback: {critique.feedback[:200]}", "info")
            return critique.feedback
        except Exception as e:
            self._log(f"[SELF-HEAL] Critic offline ({e}), fallback a errore raw", "error")
            return error
        finally:
            if swapped_for_critic:
                self._check_vram_and_swap("coder", "planner")

    def _reset_web_search_budget(self) -> None:
        """Budget ricerche web PER RUN (2026-07-18, decisione owner): il
        contatore _web_searches_done era per-lifetime — un secondo run sullo
        stesso Orchestrator ereditava il cap gia' esaurito, contraddicendo il
        config key `web_search.agent_search.max_per_run`. Chiamato all'avvio
        di run() e run_scaffold()."""
        self._web_searches_done = 0

    def _maybe_web_reference(self, error: str) -> str:
        """Ricerca web AL SERVIZIO DEL CODING (2026-07-10): se l'errore e'
        "cercabile" (modulo mancante, API cambiata, versioni incompatibili —
        vedi SEARCHABLE_ERROR_PATTERNS in devin/ai/web_search.py), cerca la
        prima riga dell'errore e ritorna un blocco di riferimento REALE da
        accodare al contesto del Critic — invece di farlo ragionare a memoria
        su API che magari sono cambiate. Max N ricerche per run (config
        web_search.agent_search.max_per_run) per non trasformare il debug in
        navigazione. Fail-soft: stringa vuota su qualsiasi problema."""
        try:
            from devin.ai.web_search import is_searchable_error, search_coding_context
            config = getattr(self.ai_client, "config", {}) if hasattr(self, "ai_client") else {}
            agent_cfg = config.get("web_search", {}).get("agent_search", {})
            if not agent_cfg.get("enabled", True):
                return ""
            max_per_run = int(agent_cfg.get("max_per_run", 2))
            done = getattr(self, "_web_searches_done", 0)
            if done >= max_per_run or not is_searchable_error(error):
                return ""
            first_line = (error or "").strip().splitlines()[0][:140]
            self._log(f"[WEB-REF] Errore cercabile, consulto il web: {first_line}", "info")
            block = search_coding_context(f"python {first_line}", config)
            self._web_searches_done = done + 1
            if not block:
                return ""
            return ("\n\nWEB REFERENCE (cercato ora per questo errore — usalo come "
                    f"fonte, non inventare API):\n{block}")
        except Exception as e:
            self._log(f"[WEB-REF] ricerca fallita (proseguo senza): {e}", "warning")
            return ""

    # ============================================================
    # FASE 1: SERIALIZZAZIONE VRAM
    # ============================================================

    def _check_vram_and_swap(self, needed_alias: str, release_alias: str = None):
        """Ensure the needed local model is loaded, releasing its peer when VRAM is low."""
        if not self.serialize_vram or not self._degraded_mode:
            return True

        vram = get_vram_status()
        free_mb = vram.get("free_mb")
        if release_alias is None:
            release_alias = "planner" if needed_alias == "coder" else "coder"

        try:
            should_release = bool(release_alias and release_alias != needed_alias)
            if should_release:
                pressure = (
                    f"{free_mb}MB free < {self.vram_swap_threshold_mb}MB"
                    if free_mb is not None and free_mb < self.vram_swap_threshold_mb
                    else "serializzazione locale attiva"
                )
                self._log(
                    f"VRAM swap ({pressure}): '{release_alias}' -> '{needed_alias}'",
                    "warning"
                )
                if self.model_launcher:
                    self.model_launcher.release_alias(release_alias)
                else:
                    from devin.ai.local_model_launcher import kill_server_on_port, MODELS
                    kill_server_on_port(MODELS[release_alias]["port"])
                self._log(f"Released '{release_alias}' from VRAM", "info")
                time.sleep(3)

            if self.model_launcher:
                ok = self.model_launcher.ensure_alias(needed_alias)
            else:
                from devin.ai.local_model_launcher import ensure_model_running, MODELS
                ok = ensure_model_running(needed_alias, MODELS[needed_alias])
            if not ok:
                self._log(f"Impossibile caricare '{needed_alias}'", "error")
            return bool(ok)
        except Exception as e:
            self._log(f"Swap failed: {e}", "error")
            return False

    def _restore_local_test_models(self):
        """Restore planner after a serialized run so normal local chat still works."""
        if not (self.local_test_mode and self.model_launcher):
            return
        try:
            if not self.model_launcher.ensure_alias("planner"):
                self._log("Planner locale non ripristinato dopo lo scaffolding", "warning")
        except Exception as exc:
            self._log(f"Ripristino planner locale fallito: {exc}", "warning")

    @staticmethod
    def _planner_diagnostic_excerpt(raw: str, max_chars: int = 1200) -> str:
        """Return a short, single-line and conservatively redacted model excerpt."""
        text = str(raw or "")
        text = re.sub(
            r"(?i)((?:api[_ -]?key|token|secret|password)\s*[:=]\s*)[^\s,;\"']+",
            r"\1<redacted>",
            text,
        )
        return text[:max_chars].replace("\r", "").replace("\n", "\\n")

    def ensure_models(self) -> LauncherStatus:
        if not self.model_launcher:
            self._log("No model launcher, skipping", "warning")
            return LauncherStatus(
                rig_available=False, rig_host="", rig_ports=[],
                local_running={}, model_source="unavailable",
                errors=["Launcher not initialized"]
            )

        self._log("Checking model availability...", "info")
        status = self.model_launcher.ensure_models()
        self.model_status = status
        self.ai_client.refresh()

        # Rig = hardware primario. Se non risponde, siamo in degraded mode:
        # il locale (16GB, backup/chat) resta disponibile ma va segnalato.
        rig_up = bool(getattr(self.ai_client, "remote_coder_ok", False) and
                      getattr(self.ai_client, "remote_reasoning_ok", False))
        self._degraded_mode = not rig_up

        if rig_up:
            self._log(f"Rig primario OK ({self.ai_client.remote_host}) — uso modelli rig", "success")
        elif status.model_source == "local":
            self._log(
                "⚠️ Rig esterno (primario) non disponibile. Uso modelli locali di backup "
                "(16GB VRAM) — sconsigliato per scaffolding/debug pesante, ok per chat.",
                "warning"
            )
        else:
            self._log("No models available!", "error")

        return status

    def get_model_status(self) -> LauncherStatus:
        if self.model_launcher:
            return self.model_launcher.get_status()
        return LauncherStatus(
            rig_available=False, rig_host="", rig_ports=[],
            local_running={}, model_source="unavailable", errors=[]
        )

    def shutdown_models(self):
        if self.model_launcher:
            self.model_launcher.shutdown_all()
            self._log("Models shutdown complete", "info")

    def _discover_test_files(self, root: Path) -> List[str]:
        """Find test files the way a human would: not only a literal tests.py.

        Perche' (2026-07-15): nel mini bench il modello scriveva test pytest
        (test_*.py) ma il gate cercava SOLO tests.py -> i test non venivano mai
        eseguiti e lo scaffold risultava 'ok' anche con suite rossa.
        """
        found: List[str] = []
        patterns = ("tests.py", "test_*.py", "*_test.py")
        for pattern in patterns:
            found.extend(str(p.relative_to(root)) for p in root.glob(pattern) if p.is_file())
        tests_dir = root / "tests"
        if tests_dir.is_dir():
            for pattern in ("test_*.py", "*_test.py", "*.py"):
                found.extend(str(p.relative_to(root)) for p in tests_dir.glob(pattern) if p.is_file())
        # dedup preservando l'ordine
        seen = set()
        return [f for f in found if not (f in seen or seen.add(f))]

    def _run_pytest_gate(self, root: Path, timeout: int = 180) -> Dict[str, Any]:
        """Run the discovered tests with pytest (fallback: unittest discover).

        Uses the same interpreter of the app (venv) via sys.executable, cwd nel
        sandbox del progetto. Ritorna dict con success/command/output.
        """
        commands = [
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--maxfail=20"],
            [sys.executable, "-m", "unittest", "discover", "-v"],
        ]
        # PYTHONDONTWRITEBYTECODE: nel self-heal loop un file corretto puo'
        # avere STESSA dimensione e mtime (stesso secondo) della versione
        # buggata -> Python riuserebbe il .pyc stale e i test resterebbero
        # rossi a codice ormai giusto. Niente bytecode = niente staleness.
        gate_env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        last: Dict[str, Any] = {"success": False, "command": "", "output": "no test runner available"}
        for argv in commands:
            try:
                proc = subprocess.run(
                    argv, cwd=str(root), capture_output=True, text=True, timeout=timeout,
                    env=gate_env,
                )
            except subprocess.TimeoutExpired:
                return {"success": False, "command": " ".join(argv[2:3]) or argv[-1],
                        "output": f"timeout dopo {timeout}s"}
            except Exception as exc:
                last = {"success": False, "command": argv[2] if len(argv) > 2 else argv[0],
                        "output": f"{type(exc).__name__}: {exc}"}
                continue
            output = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
            runner_missing = "No module named pytest" in output or "No module named 'pytest'" in output
            if runner_missing:
                last = {"success": False, "command": "pytest", "output": "pytest non installato, provo unittest"}
                continue
            return {
                "success": proc.returncode == 0,
                "command": "pytest" if argv[2] == "pytest" else "unittest",
                "output": output[-2000:],
            }
        return last

    def _scaffold_quality_gate(self, written: List[str]) -> Dict[str, Any]:
        """Syntax-check generated Python and run the project's tests for real.

        Estensione 2026-07-15 (fix del falso 'ok 3' nel mini bench): oltre al
        tests.py letterale ora vengono scoperti ed eseguiti test pytest-style
        (test_*.py, *_test.py, tests/) con `python -m pytest`, fallback
        unittest, fallback legacy tests.py via runner.
        """
        root = Path(self.project_path).resolve()
        errors = []
        checked = []

        # Sintassi MULTI-LINGUAGGIO (2026-07-16, tree-sitter): prima veniva
        # compilato solo Python — un .js/.rs/.html rotto passava il gate senza
        # essere guardato. Ora ogni file scritto passa dal syntax critic
        # (py/json nativi, il resto via tree-sitter se installato; linguaggi
        # non verificabili restano dichiaratamente fuori, non bloccano).
        for relative in written:
            path = _safe_project_target(str(root), relative)
            if not path.is_file():
                continue
            try:
                source = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                errors.append(f"{relative}: illeggibile: {exc}")
                continue
            verdict = syntax_check_text(path.name, source)
            if verdict["errors"]:
                errors.extend(f"{relative}: {msg}" for msg in verdict["errors"])
            elif verdict["checked"]:
                checked.append(relative)

        test_files = self._discover_test_files(root)
        # tests.py e' spesso uno SCRIPT (assert + exit code), non un modulo
        # pytest: pytest non ci colleziona nulla ("no tests ran"). Quindi:
        # pytest solo per i file pytest-style, tests.py sempre col runner
        # legacy. Se esistono entrambi si eseguono entrambi (gate piu' severo).
        pytest_style = [f for f in test_files if Path(f).name != "tests.py"]
        tests_run = False
        test_command = ""
        test_output = ""
        if not errors and test_files:
            if pytest_style:
                gate = self._run_pytest_gate(root)
                test_command = gate.get("command", "")
                test_output = gate.get("output", "")
                if test_command in {"pytest", "unittest"} and "no tests ran" not in test_output.lower():
                    tests_run = True
                    if not gate.get("success"):
                        errors.append(f"{test_command} failed: {test_output[:2000]}")
            if (root / "tests.py").is_file():
                tests_run = True
                test_command = (test_command + "+tests.py").lstrip("+")
                test_result = self.runner.run(str(root), entrypoint="tests.py", timeout=120)
                if not test_result.success:
                    errors.append(f"tests.py failed: {(test_result.error or 'exit non-zero')[:2000]}")

        if errors:
            status = "verified_failure"
        elif tests_run:
            status = "verified_success"
        else:
            status = "syntax_only"

        # SECURITY CRITIC (2026-07-16, bandit offline): i finding MEDIUM+ NON
        # bocciano (falsi positivi possibili) ma diventano evidenza allegata
        # all'attempt: e' il reviewer a decidere se escalare.
        security_warnings: List[str] = []
        if written and bandit_available():
            security_warnings = scan_python_files(root, written)

        return {
            "status": status,
            "syntax_checked": checked,
            "tests_run": tests_run,
            "test_files": test_files,
            "test_command": test_command,
            "test_output": test_output[-2000:] if test_output else "",
            "errors": errors,
            "security_warnings": security_warnings,
            "security_scanner": "bandit" if bandit_available() else "assente",
        }

    def _scaffold_heal_loop(self, quality: Dict[str, Any], impl_files: List[str],
                            spec_by_name: Dict[str, str], running_context: str,
                            max_iterations: int = 2) -> Dict[str, Any]:
        """Rigenera i file di implementazione col feedback dei test falliti e
        ri-verifica, finche' il gate e' verde o esaurite le iterazioni.
        Usa il LoopRunner generico (goal+azione+verifica+stop)."""
        if not impl_files:
            return quality
        root = Path(self.project_path).resolve()

        def action(iteration: int, last: VerifyResult | None) -> Dict[str, Any]:
            gate = (last.evidence if last and last.evidence else quality)
            feedback = (
                "Il codice precedente NON passa i test. Correggilo perche' passino.\n"
                f"Errori/gate:\n{'; '.join(gate.get('errors') or [])[:1200]}\n"
                f"Output test:\n{(gate.get('test_output') or '')[:1200]}"
            )
            self._log(f"🔁 Self-heal iterazione {iteration}: rigenero {len(impl_files)} file", "info")
            for fname in impl_files:
                if self._should_stop:
                    break
                spec = spec_by_name.get(fname, "")
                try:
                    content = self.coder.generate_file(
                        fname, spec + "\n\n[FEEDBACK]\n" + feedback,
                        project_context=running_context)
                    if not content.strip():
                        continue
                    if syntax_check_text(fname, content)["errors"]:
                        continue  # sintassi rotta: tieni la versione precedente
                    target = _safe_project_target(self.project_path, fname)
                    target.write_text(content, encoding="utf-8")
                except Exception as exc:
                    self._log(f"  self-heal: {fname} non rigenerato ({exc})", "warning")
            return self._scaffold_quality_gate(
                [str(p.relative_to(root)) for p in root.rglob("*.py")
                 if p.is_file()])

        def verifier(gate: Dict[str, Any]) -> VerifyResult:
            ok = gate.get("status") == "verified_success"
            return VerifyResult(ok, "; ".join(gate.get("errors") or [])[:200], evidence=gate)

        outcome = run_loop(
            action, verifier,
            max_iterations=max_iterations, success_streak=1,
            should_stop=lambda: self._should_stop,
        )
        self._log(
            f"Self-heal loop: {outcome.reason} dopo {outcome.iterations} iterazioni "
            f"({'VERDE' if outcome.success else 'ancora rosso'})",
            "success" if outcome.success else "warning")
        return outcome.last_result if outcome.last_result else quality

    def _remember_scaffold_outcome(self, task: str, quality: Dict[str, Any],
                                   written: List[str]) -> str:
        """Publish only verified outcomes; untested scaffolds never become knowledge."""
        status = quality.get("status")
        if status not in {"verified_success", "verified_failure"}:
            return "not_recorded"
        # DEDUP (2026-07-18): same memory_key discipline as record_eval_result —
        # repeated identical scaffold outcomes (same project+task+status) must
        # not append a fresh record per run: repeat failures flood
        # local_memories.jsonl and crowd diverse lessons out of top-3 recall.
        # "duplicate" is a no-op; run_scaffold only logs the outcome string.
        key = _memory_key(
            self.project_path, "scaffold_quality_gate", task,
            "scaffold_quality_gate" if status == "verified_failure" else status,
        )
        local = getattr(self.memory_client, "local", None)
        if local is not None and key in _existing_memory_keys(Path(local.path)):
            return "duplicate"
        polarity = "negative" if status == "verified_failure" else "positive"
        evidence = "tests.py_exit_nonzero" if polarity == "negative" else "tests.py_exit_zero"
        # EVIDENCE FIDELITY (2026-07-18): a green gate with MEDIUM+ security
        # findings is NOT a clean success — the memory must read as
        # success-with-warnings, otherwise recall later imitates the pattern
        # without its caveats.
        warnings = [str(w) for w in (quality.get("security_warnings") or [])]
        lesson = (
            "a failed approach; do not repeat it unchanged"
            if polarity == "negative"
            else "a tested successful approach"
        )
        if warnings and polarity == "positive":
            lesson = (
                "a tested successful approach WITH security warnings "
                "(review the findings before imitating it)"
            )
        content = (
            f"Scaffold outcome for project '{Path(self.project_path).name}'.\n"
            f"Status: {status}. This is {lesson}.\n"
            f"Task summary: {task[:1200]}\n"
            f"Files generated: {len(written)}.\n"
            f"Quality evidence: {quality.get('errors') or 'tests.py passed and Python syntax compiled'}"
        )
        if warnings:
            content += (
                f"\nSecurity warnings ({len(warnings)} finding MEDIUM+, scanner "
                f"{quality.get('security_scanner') or 'bandit'}): "
                + "; ".join(w[:160] for w in warnings[:5])
            )
        return self.memory_client.store_local(
            content,
            tags=build_memory_tags(
                project=Path(self.project_path).name,
                kind="eval_result" if status == "verified_success" else "failure_lesson",
                status=status,
                polarity=polarity,
                evidence=evidence,
                failure_type="scaffold_quality_gate" if status == "verified_failure" else None,
                memory_key=key,
            ) + project_tags(self.project_path) + [
                "source:devin", "eval:scaffold_quality_gate", "topic:scaffold",
            ],
            importance=0.85,
        )

    # ============================================================
    # MODALITÀ 2 — ZERO-SHOT SCAFFOLDING
    # Creazione progetto da zero ESCLUSIVAMENTE via tool (scrittura file diretta),
    # nessuna diff pipeline. Feedback SSE continuo file-per-file (Chat First).
    # ============================================================

    def run_scaffold(self, task: str, project_path: str = None, run_id: str = None) -> Dict[str, Any]:
        self._reset_web_search_budget()
        if project_path:
            self.project_path = project_path
            self.git_ops.project_path = project_path
        start_time = time.time()

        Path(self.project_path).mkdir(parents=True, exist_ok=True)
        self._log("Zero-Shot Scaffolding avviato", "info")

        model_status = self.ensure_models()
        if model_status.model_source == "unavailable":
            self._log("Nessun modello disponibile, impossibile procedere", "error")
            return {"success": False, "status": "failed", "error": "No models available", "duration": time.time() - start_time}

        if self._degraded_mode:
            self._log(
                "⚠️ Scaffolding in degraded mode (rig down, uso locale). "
                "Qualità/velocità ridotte su progetti multi-file.",
                "warning"
            )

        serialized_local = self._degraded_mode and self.serialize_vram
        if serialized_local and not self._check_vram_and_swap("planner", "coder"):
            return {
                "success": False,
                "status": "failed",
                "error": "local planner unavailable",
                "duration": time.time() - start_time,
            }

        try:
            file_plan = self.planner.plan_scaffold(task)
        finally:
            if serialized_local and not self._check_vram_and_swap("coder", "planner"):
                self._log("Coder locale non disponibile dopo il piano", "error")

        if not file_plan:
            for attempt in getattr(self.planner, "last_scaffold_attempts", []):
                self._log(
                    "Planner output non valido "
                    f"(tentativo={attempt.get('attempt')}, errore={attempt.get('error')}, "
                    f"chars={len(attempt.get('raw') or '')}): "
                    f"{self._planner_diagnostic_excerpt(attempt.get('raw', ''))}",
                    "warning",
                )
            self._log("Planner non ha prodotto un piano file valido (JSON non parsabile o task ambiguo)", "error")
            self._restore_local_test_models()
            return {"success": False, "status": "failed", "error": "empty file plan", "duration": time.time() - start_time}

        self._log(f"Piano: {len(file_plan)} file da creare", "info")

        written = []
        failed = []
        running_context = ""

        # DOCS (2026-07-16): documentazione ufficiale nel contesto del Coder,
        # INTERNET-FIRST — antidoto agli endpoint/firme inventati (host Steam di
        # fantasia nel batch). resolve_context: cache TTL fresca -> altrimenti
        # FETCH LIVE via web_search -> altrimenti fallback pinned offline. La
        # cache resta piccola (voci web scadono e vengono potate). Vuoto se
        # nessuna doc utile: zero rumore.
        try:
            ws_cfg = (self.config or {}).get("web_search", {}) if hasattr(self, "config") else {}
            # Il FETCH LIVE ha senso solo se il task tocca una API/libreria
            # esterna (endpoint/SDK): per algoritmi puri (ordina, conta, ...)
            # sarebbe latenza sprecata. Le doc gia' in cache/pinned si usano
            # comunque (match offline), a prescindere da questo gate.
            _api_kw = ("api", "endpoint", "http", "url", "sdk", "library", "libreria",
                       "request", "client", "token", "oauth", "webhook", "rest", "json api")
            needs_web = any(k in task.lower() for k in _api_kw)
            allow_web = bool(ws_cfg.get("enabled", True)) and needs_web

            def _fetch(q: str) -> str:
                # Crawl4AI-first per le DOC (miglior estrazione su siti JS),
                # requests/Playwright come fallback (vedi fetch_page_smart).
                from devin.ai.web_search import search_docs_context
                return search_docs_context(q, self.config or {}, max_chars=2200)

            docs_ctx = DocsCache(DOCS_CACHE_ROOT).resolve_context(
                task, web_fetcher=_fetch, allow_web=allow_web)
            if docs_ctx:
                running_context = docs_ctx + "\n"
                src = "web/cache" if allow_web else "cache offline"
                self._log(f"📚 Docs ({src}): documentazione ufficiale iniettata nel contesto", "info")
        except Exception as exc:
            self._log(f"Docs non disponibili: {exc}", "warning")

        for i, item in enumerate(file_plan, 1):
            if self._should_stop:
                self._log("Scaffolding interrotto dall'utente", "warning")
                break

            fname, spec = item["filename"], item["spec"]
            self._log(f"[{i}/{len(file_plan)}] Creating {fname}...", "info")

            try:
                content = self.coder.generate_file(fname, spec, project_context=running_context)
                if not content.strip():
                    raise ValueError(f"Coder ha restituito contenuto vuoto per {fname}")

                # Tree-sitter structural critic (2026-07-16): sintassi rotta
                # viene RIGETTATA prima ancora di scrivere il file — l'errore
                # con riga/colonna va al Critic via _self_heal (ramo except),
                # come da pattern "reject and feed back".
                verdict = syntax_check_text(fname, content)
                if verdict["errors"]:
                    raise ValueError(
                        f"sintassi non valida ({verdict.get('language') or '?'}): "
                        + "; ".join(verdict["errors"][:3])
                    )

                target = _safe_project_target(self.project_path, fname)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")

                written.append(fname)
                running_context += f"\n# FILE: {fname}\n{content[:1500]}\n"
                self._log(f"✓ {fname} ({len(content)} chars)", "success")

            except Exception as e:
                # Self-Healing: prova auto-correzione via Critic prima di segnare fallito
                healed_feedback = self._self_heal(f"scaffold_file:{fname}", str(e), context=spec)
                self._log(f"✗ {fname} fallito — feedback Critic: {healed_feedback[:150]}", "error")
                failed.append({"filename": fname, "error": str(e), "critic_feedback": healed_feedback})

        quality = self._scaffold_quality_gate(written)

        # LOOP MODE (2026-07-16): "green tests streak". Se il gate e' rosso, non
        # consegnare — rigenera i file di IMPLEMENTAZIONE passando al Coder
        # l'output dei test falliti come feedback, poi ri-verifica, finche'
        # verde o esaurito il budget iterazioni. Colpisce la classe di
        # fallimento #1 del batch MBPP (own_tests_failed: il modello consegnava
        # con la propria suite rossa). Disattivabile: coder.self_heal_loop.
        coder_cfg = (self.config or {}).get("coder", {}) if hasattr(self, "config") else {}
        heal_enabled = coder_cfg.get("self_heal_loop", True)
        heal_iters = int(coder_cfg.get("self_heal_max_iterations", 2) or 2)
        if heal_enabled and quality["status"] == "verified_failure" and written and not self._should_stop:
            spec_by_name = {it["filename"]: it["spec"] for it in file_plan}
            impl_files = [f for f in written if not _is_test_filename(f)]
            quality = self._scaffold_heal_loop(
                quality, impl_files, spec_by_name, running_context,
                max_iterations=heal_iters)

        if quality["status"] == "verified_failure":
            gate_error = "; ".join(quality["errors"])
            failed.append({
                "filename": "<quality_gate>",
                "error": gate_error,
                "critic_feedback": "Test o sintassi falliti: non committare come successo.",
            })
            self._log(f"Quality gate FALLITO: {gate_error[:500]}", "error")
        elif quality["status"] == "verified_success":
            self._log("Quality gate superato: tests + sintassi OK", "success")
        else:
            self._log("Quality gate parziale: sintassi OK, nessun tests.py eseguibile", "warning")

        if quality.get("security_warnings"):
            self._log(
                f"⚠️ Security ({quality.get('security_scanner')}): "
                f"{len(quality['security_warnings'])} finding MEDIUM+ — "
                + quality["security_warnings"][0][:200],
                "warning",
            )

        memory_outcome = self._remember_scaffold_outcome(task, quality, written)
        if memory_outcome != "not_recorded":
            self._log(f"Esito strutturato memoria: {memory_outcome}", "info")

        if written and not failed:
            try:
                self.git_ops.commit("", f"Zero-Shot Scaffold: {task}")
                self._log("Progetto committato", "info")
            except Exception as e:
                self._log(f"Git commit warning: {e}", "warning")

        # Status evidence-aware (2026-07-18): 'success' resta il contratto
        # "tutti i file pianificati scritti", ma uno scaffold SENZA test
        # eseguibili non ha la stessa evidenza di uno con suite verde —
        # la timeline deve poter distinguere i due livelli.
        scaffold_success = bool(written) and not failed
        if not scaffold_success:
            scaffold_status = "failed"
        elif quality.get("status") == "verified_success":
            scaffold_status = "verified_success"
        else:
            scaffold_status = "syntax_only"

        result = {
            "success": scaffold_success,
            "status": scaffold_status,
            "files_written": written,
            "files_failed": failed,
            "total_planned": len(file_plan),
            "duration": time.time() - start_time,
            "degraded_mode": self._degraded_mode,
            "quality_gate": quality,
            "memory_outcome": memory_outcome,
        }
        self._restore_local_test_models()
        self._log(f"Scaffolding completato: {len(written)}/{len(file_plan)} file scritti", "success" if not failed else "warning")
        return result

    # ============================================================
    # MODALITÀ 1 — MANTENIMENTO
    # Loop principale: Planner -> Coder -> Patcher -> Runner -> Critic
    # ============================================================

    def run_from_conversation(self, conversation_text: str, project_path: str = None,
                               run_id: str = None) -> Dict[str, Any]:
        """'Realizza dalla chat' su progetto CON codice (2026-07-10): il metodo
        era referenziato da fast_app.py ma non e' mai arrivato in questo file
        (perso in una delle consegne zip) — ogni click finiva in
        [FATAL] 'Orchestrator' object has no attribute 'run_from_conversation'.

        Implementazione: wrapper su run() con la conversazione come specifica.
        Il Planner ri-pianifica leggendo la discussione (decisioni e correzioni
        piu' RECENTI pesano di piu': si tiene la coda, il ctx locale e' 8192).
        Footer 'status: X' garantito dai return path di run()."""
        MAX_CONV_CHARS = 6000
        tail = conversation_text[-MAX_CONV_CHARS:]
        if len(conversation_text) > MAX_CONV_CHARS:
            tail = "[...conversazione precedente troncata...]\n" + tail
            self._log(f"Conversazione lunga ({len(conversation_text)} char): uso gli ultimi {MAX_CONV_CHARS}", "info")

        # DISTILLAZIONE (2026-07-10): la conversazione grezza (tabelle markdown,
        # esempi, divagazioni, fraintendimenti corretti) mandava in tilt il
        # Planner — visto sul campo: 29 step e un diff da 9146 righe per
        # "integra Pint". Un passaggio di reasoning la riduce a un task
        # operativo corto; la coda grezza resta solo come fallback.
        task = None
        try:
            distill_msgs = [
                {"role": "system", "content":
                    "Distilla dalla conversazione un TASK di sviluppo conciso e operativo. "
                    "Massimo 8 righe: cosa implementare/correggere, in quali file, vincoli. "
                    "Le decisioni piu' RECENTI prevalgono su quelle vecchie (le correzioni "
                    "dell'utente annullano i fraintendimenti precedenti). "
                    "Rispondi SOLO col task, niente premesse, niente codice."},
                {"role": "user", "content": tail},
            ]
            distilled = (self.ai_client.local(distill_msgs, mode="reasoning", timeout=90) or "").strip()
            if len(distilled) > 20:
                task = f"TASK (distillato dalla conversazione in chat):\n{distilled[:2500]}"
                self._log(f"Task distillato: {distilled[:250]}", "info")
        except Exception as e:
            self._log(f"Distillazione fallita ({e}), uso la conversazione grezza", "warning")

        if not task:
            task = ("Applica al progetto le modifiche discusse/concordate in questa conversazione "
                    "utente-assistente. Le decisioni e correzioni piu' recenti hanno precedenza "
                    "su quelle vecchie.\n\n=== CONVERSAZIONE ===\n" + tail)
        return self.run(task=task, project_path=project_path, run_id=run_id)

    def _small_project(self, max_lines: int) -> bool:
        """True se OGNI file di codice del progetto è sotto max_lines righe.
        In quel caso conviene la modalità WHOLE-FILE (il Coder riscrive i file
        interi, niente unified diff): più affidabile sui modelli piccoli. Su file
        grandi resta il diff (riscrivere 2000 righe è spreco e rischio)."""
        try:
            files = self.context_engine.collect_project_files()
        except Exception:
            return False
        if not files:
            return False
        for f in files:
            if (f.get("content", "").count("\n") + 1) > max_lines:
                return False
        return True

    def run(
        self,
        task: str,
        project_path: str = None,
        entrypoint: str = None,
        max_attempts: int = None,
        max_seconds: int = None,
        run_id: str = None
    ) -> Dict[str, Any]:
        self._reset_web_search_budget()
        if project_path:
            self.project_path = project_path
            self.git_ops.project_path = project_path

        # === TASK 13: Inizializza persistenza stato ===
        self._state_persistence = StatePersistence(self.project_path, run_id)

        # FIX (2026-07-18): cleanup() non era mai chiamato — gli stati di run
        # interrotti si accumulavano per sempre in .devin_state. Rimozione
        # bounded (default 24h) all'avvio di ogni run, solo per questo progetto.
        try:
            removed = self._state_persistence.cleanup()
            if removed:
                self._log(f"State cleanup: removed {removed} stale run state(s)", "info")
        except Exception as e:
            self._log(f"State cleanup skipped: {e}", "warning")

        # Prova a riprendere da stato precedente (solo se il chiamante ha
        # passato il run_id di un run interrotto — vedi get_resume_info).
        resume_info = self._state_persistence.get_resume_info()
        if resume_info and resume_info.get("can_resume"):
            self._log(
                f"Resuming previous run {resume_info['run_id']} "
                f"(attempt {resume_info['attempt']+1}/{resume_info.get('max_retries', 3)})",
                "warning"
            )

        start_time = time.time()
        logs = []
        max_retries = max_attempts or self.MAX_RETRIES

        # === TASK 13: Se c'è uno stato da riprendere, carica i dati salvati ===
        plan = None
        context = ""
        last_error = None
        attempt = 0
        prev_failure_sig = None
        failure_sig_history = []

        if resume_info:
            task = resume_info.get("task", task)
            attempt = resume_info.get("attempt", 0)
            last_error = resume_info.get("last_error")
            saved_plan = resume_info.get("plan")
            if saved_plan:
                from devin.agents.planner import Plan
                plan = Plan(
                    steps=saved_plan.get("steps", []),
                    raw_response=saved_plan.get("raw_response", "")
                )

        log_file = None
        if run_id:
            log_file = LOG_DIR / f"{run_id}.log"
            if resume_info and log_file.exists():
                # RESUME (2026-07-18): il log del run interrotto deve SOPRAVVIVERE —
                # prima write_text() lo troncava, cancellando l'evidenza del crash.
                with open(log_file, "a", encoding="utf-8") as f:
                    f.write(f"\n--- Run resumed: {run_id} ---\nTask: {task}\n")
            else:
                log_file.write_text(f"Run started: {run_id}\nTask: {task}\n", encoding="utf-8")

        def log(msg, level="info"):
            logs.append({"time": time.time() - start_time, "level": level, "msg": msg})
            self._log(msg, level)
            # FIX: self._log() sopra gia' inoltra a self.sse_callback, che in fast_app.py
            # scrive esattamente su LOG_DIR/{run_id}.log (stesso path di log_file qui sotto).
            # Scrivere ANCHE qui duplicava ogni riga nel file (e quindi nella dashboard,
            # che lo streamma via /stream/{run_id}). Scriviamo diretto SOLO come fallback
            # per chi istanzia Orchestrator con run_id ma senza sse_callback.
            if log_file and not self.sse_callback:
                with open(log_file, "a", encoding="utf-8") as f:
                    f.write(f"[{level.upper()}] {msg}\n")

        # FIX: helper UNICO per il footer 'status: X' del log file, chiamato da OGNI
        # return path di run() (prima 2 su 6 non lo scrivevano affatto — "No models
        # available" e "Planner failed" — mentre gli altri 4 lo scrivevano ad-hoc E
        # fast_app.py lo riscriveva UNA SECONDA VOLTA dopo che run() tornava, causando
        # "status: failed" duplicato in fondo al log). Ora e' scritto esattamente una
        # volta, qui, per qualunque esito — fast_app.py non deve piu' scriverlo.
        def write_status_footer(status: str):
            if log_file:
                with open(log_file, "a", encoding="utf-8") as f:
                    f.write(f"\nstatus: {status}\n")

        # === TASK 13: Helper per salvare stato ===
        def save_state(**kwargs):
            """Salva lo stato corrente su disco."""
            state = {
                "task": task,
                "attempt": attempt,
                "last_error": last_error,
                "last_patch": kwargs.get("patch", ""),
                "plan": plan.to_dict() if plan else None,
                "context_length": len(context),
                "max_retries": max_retries,
                "model_source": getattr(self.model_status, "model_source", "unknown"),
            }
            state.update(kwargs)
            self._state_persistence.save(state)

        log("DEVIN starting...", "info")
        model_status = self.ensure_models()
        if model_status.model_source == "unavailable":
            log("No AI models available. Cannot proceed.", "error")
            save_state(final_status="failed")
            write_status_footer("failed")
            return {
                "success": False,
                "status": "failed",
                "error": "No models available",
                "logs": logs,
                "duration": time.time() - start_time
            }


        log(f"Models ready (source: {model_status.model_source})", "success")

        # === FASE 1: SWAP PER PLANNER ===
        if self.serialize_vram:
            self._check_vram_and_swap("planner", release_alias="coder")

        log("Building context...", "info")
        # === TASK 13: Se abbiamo ripreso, usa context salvato ===
        if not context:
            try:
                context = self.context_engine.build(
                    project_path=self.project_path,
                    query=task
                )
                # === FASE 1: VECTOR STORE — indicizza il progetto con persistenza ===
                if self.config.get("context", {}).get("semantic_search_enabled"):
                    try:
                        files = self.context_engine.collect_project_files()
                        # Usa cache persistente in workspace/.devin_cache/
                        cache_dir = Path(self.project_path) / ".devin_cache"
                        cache_dir.mkdir(parents=True, exist_ok=True)
                        self.vector_store.index_project(
                            self.project_path, files,
                            cache_path=cache_dir / "semantic_index.json"
                        )
                        semantic = self.context_retriever.retrieve(task, self.project_path)
                        context = self.context_engine.prioritize(context, semantic, task)
                        log(f"Semantic context: {len(semantic)} chars", "info")
                    except Exception as e:
                        log(f"Semantic search warning: {e}", "warning")
                log(f"Context: {len(context)} chars", "info")
            except Exception as e:
                log(f"Context build warning: {e}", "warning")
                context = ""
        else:
            log(f"Resumed context: {len(context)} chars", "info")

        log("Planner analyzing...", "info")
        # === TASK 13: Se abbiamo ripreso, usa plan salvato ===
        if not plan:
            try:
                plan = self.planner.plan(task, context)
                log(f"Plan: {len(plan.steps)} steps", "info")
                save_state(step="planner_done")
            except Exception as e:
                log(f"Planner failed: {e}", "error")
                save_state(final_status="failed")
                write_status_footer("failed")
                return {"success": False, "status": "failed", "error": str(e), "logs": logs, "duration": time.time() - start_time}
        else:
            log(f"Resumed plan: {len(plan.steps)} steps", "info")

        # === Modalità edit: WHOLE-FILE per progetti piccoli, unified diff per i grandi ===
        # Il fallimento sistematico su modelli piccoli è l'unified diff con righe di
        # contesto allucinate (non applicabile). Su progetti piccoli il Coder riscrive
        # i file interi: niente patcher, niente fuzzy, gioca sul punto forte del modello.
        coder_cfg = self.config.get("coder", {}) or {}
        whole_file_enabled = coder_cfg.get("whole_file_enabled", True)
        whole_file_max_lines = int(coder_cfg.get("whole_file_max_lines", 300))
        use_whole_file = whole_file_enabled and self._small_project(whole_file_max_lines)
        if use_whole_file:
            log(f"Edit mode: WHOLE-FILE (progetto piccolo, file <= {whole_file_max_lines} righe) — bypass diff/patcher", "info")
        else:
            log("Edit mode: unified diff", "info")

        raw_run_token = run_id or datetime.now().strftime("run_%Y%m%d_%H%M%S_%f")
        run_token = "".join(ch for ch in raw_run_token if ch.isalnum() or ch in "-_")[:96]
        run_sandbox_root = f"workspace/sandboxes/{run_token}"

        while attempt < max_retries:
            # === TIMEOUT CHECK ===
            if max_seconds and (time.time() - start_time) > max_seconds:
                log("Timeout: max_seconds exceeded", "error")
                write_status_footer("timeout")
                save_state(final_status="timeout")
                return {
                    "success": False,
                    "status": "timeout",
                    "error": "Timeout: max_seconds exceeded",
                    "logs": logs,
                    "duration": time.time() - start_time,
                    "model_source": model_status.model_source
                }

            # === STOP CHECK ===
            if self._should_stop:
                log("Run stopped by user", "warning")
                write_status_footer("stopped")
                save_state(final_status="stopped")
                return {
                    "success": False,
                    "status": "stopped",
                    "error": "Run stopped by user",
                    "logs": logs,
                    "duration": time.time() - start_time,
                    "model_source": model_status.model_source
                }

            # === NO-PROGRESS GUARD (2026-07-18) ===
            # Se l'errore normalizzato e' IDENTICO a quello del giro precedente il
            # loop non sta facendo progressi (caso tipico: Critic offline ->
            # _self_heal restituisce l'errore grezzo invariato -> il Coder riceve
            # lo stesso feedback e rifallisce allo stesso modo). Meglio fermarsi
            # con uno stato distinto ("stalled") che bruciare tutti i retry
            # ripetendo la stessa generazione.
            failure_sig = None
            if last_error:
                normalized_error = " ".join(str(last_error).split())[:4000]
                failure_sig = hashlib.sha1(normalized_error.encode("utf-8")).hexdigest()[:12]
            # Alternanza A,B,A,B (estensione 2026-07-18): il confronto con la sola
            # firma precedente vede coppie consecutive — un loop che oscilla tra
            # DUE errori diversi non scatta mai e brucia tutto il budget. Periodo-2
            # confermato quando la firma corrente ripete quella di 2 giri fa E il
            # giro precedente ripete il suo -2 (serve la 4a ricorrenza: A,B,A,B).
            period2_cycle = (
                failure_sig is not None
                and len(failure_sig_history) >= 3
                and failure_sig == failure_sig_history[-2]
                and failure_sig_history[-1] == failure_sig_history[-3]
            )
            if failure_sig and (failure_sig == prev_failure_sig or period2_cycle):
                reason = ("alternating failure cycle (period 2)" if period2_cycle
                          and failure_sig != prev_failure_sig
                          else "identical failure repeated across attempts")
                log(f"No progress: {reason} — stopping early", "error")
                write_status_footer("stalled")
                save_state(final_status="stalled")
                return {
                    "success": False,
                    "status": "stalled",
                    "error": f"Stalled: {reason}. Last error: {last_error}",
                    "logs": logs,
                    "duration": time.time() - start_time,
                    "model_source": model_status.model_source
                }
            prev_failure_sig = failure_sig
            if failure_sig is not None:
                failure_sig_history.append(failure_sig)
            else:
                # Giro senza errore (o primo giro): come prev_failure_sig si
                # resetta, cosi' la storia — un ciclo interrotto non e' un ciclo.
                failure_sig_history.clear()

            attempt += 1
            log(f"\nAttempt {attempt}/{max_retries}", "info")
            save_state(attempt=attempt, step="attempt_start")

            # === FASE 1: SWAP PER CODER ===
            if self.serialize_vram:
                self._check_vram_and_swap("coder", release_alias="planner")

            log("Coder generating patch...", "info")
            if use_whole_file:
                # === WHOLE-FILE: il Coder riscrive i file interi, niente diff/patcher ===
                try:
                    full_files = self.coder.generate_full_files(plan, context, last_error)
                except Exception as e:
                    log(f"Coder failed: {e}", "error")
                    last_error = self._self_heal("coder", str(e), context=context)
                    save_state(last_error=last_error, step="coder_failed")
                    continue
                # `patch` sintetico (per commit/log/critic): serve solo come riepilogo testuale.
                patch = "\n\n".join(f"### FILE: {p}\n{c}" for p, c in full_files.items())
                total_chars = sum(len(c) for c in full_files.values())
                log(f"Whole-file: {len(full_files)} file, {total_chars} caratteri", "info")
                save_state(patch=patch, step="coder_done")
                if run_id:
                    debug_patch_file = LOG_DIR / f"{run_id}_attempt{attempt}_files.txt"
                    debug_patch_file.write_text(patch or "(nessun file)", encoding="utf-8")
                    log(f"Debug: file interi salvati in {debug_patch_file.name}", "info")
                if not full_files:
                    last_error = ("You returned NO files in the required format. For each file to "
                                  "create or modify, output a line '### FILE: <path>' then a fenced "
                                  "code block with the COMPLETE new file content.")
                    save_state(last_error=last_error, step="coder_no_files")
                    continue

                if self.serialize_vram:
                    vram = get_vram_status()
                    if vram.get("is_critical"):
                        log("VRAM critical before write, releasing coder", "warning")
                        from devin.ai.local_model_launcher import kill_server_on_port, MODELS
                        kill_server_on_port(MODELS["coder"]["port"])

                log("Patcher applying (whole-file)...", "info")
                try:
                    sandbox_path = self.patcher.apply_full_files(
                        full_files, self.project_path, sandbox_root=run_sandbox_root)
                    log("File interi scritti nel sandbox", "info")
                    save_state(patch=patch, step="patcher_done")
                except Exception as e:
                    log(f"Whole-file write failed: {e}", "error")
                    last_error = self._self_heal("patcher", str(e), patch=patch, context=context)
                    save_state(last_error=last_error, step="patcher_failed")
                    continue
            else:
                # === UNIFIED DIFF (progetti grandi) ===
                try:
                    patch = self.coder.generate(plan, context, last_error)
                    # FIX metrica (2026-07-10): prima loggava len(patch) come "lines"
                    # ma sono CARATTERI (il "9146 lines" era un diff da ~200 righe).
                    patch_lines = (patch.count("\n") + 1) if patch else 0
                    log(f"Patch: {patch_lines} righe ({len(patch)} caratteri)", "info")
                    save_state(patch=patch, step="coder_done")
                    # DEBUG: salva il diff grezzo COMPLETO per attempt, su file separato.
                    if run_id:
                        debug_patch_file = LOG_DIR / f"{run_id}_attempt{attempt}_patch.diff"
                        debug_patch_file.write_text(patch or "(patch vuota)", encoding="utf-8")
                        log(f"Debug: patch grezza salvata in {debug_patch_file.name}", "info")

                    # GUARDIA diff giganti (2026-07-10): un diff enorme non si
                    # applichera' mai (fuzzy match su centinaia di hunk imprecisi
                    # di un modello piccolo = minuti persi). Meglio rigettarlo subito.
                    if patch_lines > 800:
                        log(f"Patch enorme ({patch_lines} righe): rigettata prima del patcher", "warning")
                        last_error = (
                            f"Your previous diff had {patch_lines} lines — far too large to apply. "
                            "Regenerate a MINIMAL unified diff: touch ONLY the files that must "
                            "change, small hunks with exact context lines, never rewrite whole "
                            "files. If the task is big, implement only the FIRST concrete step.")
                        save_state(last_error=last_error, step="coder_patch_too_big")
                        continue
                except Exception as e:
                    log(f"Coder failed: {e}", "error")
                    # SELF-HEALING: Critic tenta auto-correzione prima del retry cieco
                    last_error = self._self_heal("coder", str(e), context=context)
                    save_state(last_error=last_error, step="coder_failed")
                    continue

                # === FASE 1: SWAP PER PATCHER/RUNNER (non serve GPU) ===
                if self.serialize_vram:
                    vram = get_vram_status()
                    if vram.get("is_critical"):
                        log("VRAM critical before patch, releasing coder", "warning")
                        from devin.ai.local_model_launcher import kill_server_on_port, MODELS
                        kill_server_on_port(MODELS["coder"]["port"])

                log("Patcher applying...", "info")
                try:
                    sandbox_path = self.patcher.apply(
                        patch, self.project_path, sandbox_root=run_sandbox_root)
                    log("Patch applied to sandbox", "info")
                    save_state(patch=patch, step="patcher_done")
                except Exception as e:
                    log(f"Patch failed: {e}", "error")
                    # SELF-HEALING: Critic tenta auto-correzione prima del retry cieco
                    last_error = self._self_heal("patcher", str(e), patch=patch, context=context)
                    save_state(last_error=last_error, step="patcher_failed")
                    continue

            log("Runner executing...", "info")
            try:
                runner_timeout = None
                if max_seconds:
                    elapsed = time.time() - start_time
                    runner_timeout = max(1, int(max_seconds - elapsed))
                result = self.runner.run(
                    sandbox_path, entrypoint=entrypoint, timeout=runner_timeout)
                if result.success:
                    log("Execution successful!", "success")

                    try:
                        self._sync_sandbox_to_project(sandbox_path, self.project_path)
                        log("Sandbox synced to project", "info")
                    except Exception as e:
                        log(f"Sandbox sync warning: {e}", "warning")

                    try:
                        self.git_ops.commit(patch, task)
                        log("Changes committed", "info")
                    except Exception as e:
                        log(f"Git commit warning: {e}", "warning")

                    final_result = {
                        "success": True,
                        "status": "success",
                        "plan": plan.to_dict(),
                        "patch": patch,
                        "logs": logs,
                        "duration": time.time() - start_time,
                        "model_source": model_status.model_source
                    }
                    save_state(final_status="success", patch=patch)
                    # Pulisci stato dopo successo
                    self._state_persistence.delete()
                    write_status_footer("success")
                    return final_result
                else:
                    log(f"Execution failed: {result.error}", "error")
                    last_error = result.error
                    save_state(last_error=last_error, step="runner_failed")
            except Exception as e:
                log(f"Runner error: {e}", "error")
                log(f"Runner traceback: {traceback.format_exc()}", "error")
                last_error = str(e)
                save_state(last_error=last_error, step="runner_exception")

            # === FASE 1: SWAP PER CRITIC (reasoning) ===
            if self.serialize_vram:
                self._check_vram_and_swap("planner", release_alias="coder")

            log("Critic analyzing...", "info")
            try:
                sandbox_files = {}
                for py_file in Path(sandbox_path).rglob("*.py"):
                    try:
                        rel = str(py_file.relative_to(sandbox_path))
                        sandbox_files[rel] = py_file.read_text(encoding="utf-8", errors="ignore")
                    except Exception:
                        pass

                # Errore di runtime "cercabile" (modulo/API/versioni)? Dai al
                # Critic un riferimento web REALE invece di farlo andare a memoria.
                critique = self.critic.analyze(
                    last_error, patch, context + self._maybe_web_reference(last_error),
                    sandbox_files=sandbox_files)
                log(f"Critic feedback: {critique.feedback[:200]}...", "info")
                last_error = critique.feedback
                save_state(last_error=last_error, step="critic_done")
            except Exception as e:
                log(f"Critic warning: {e}", "warning")

        log("Max retries exceeded", "error")
        write_status_footer("failed")
        save_state(final_status="failed")
        return {
            "success": False,
            "status": "failed",
            "error": f"Max retries exceeded. Last error: {last_error}",
            "logs": logs,
            "duration": time.time() - start_time,
            "model_source": model_status.model_source
        }

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        # NON spegniamo più i modelli automaticamente
        # L'utente li gestisce dalla Web UI
        self._log("Orchestrator finished. Models still running.", "info")
        return False


if __name__ == "__main__":
    print("=" * 60)
    print("DEVIN Orchestrator - Test Mode")
    print("=" * 60)
    try:
        with Orchestrator(config_path=_DEFAULT_CONFIG_PATH) as orch:
            print(f"\nModel status: {orch.get_model_status().to_dict()}")
            print("\nOrchestrator ready. Use orch.run(task) or orch.run_scaffold(task).")
    except Exception as e:
        print(f"\nERROR: {e}")
        traceback.print_exc()
