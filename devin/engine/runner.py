import os
import re
import signal
import subprocess
import sys
import threading
from pathlib import Path

# Nomi import != nome pacchetto pip (i casi comuni). Fallback: usa il nome import.
_PIP_ALIASES = {
    "cv2": "opencv-python",
    "PIL": "pillow",
    "yaml": "pyyaml",
    "bs4": "beautifulsoup4",
    "sklearn": "scikit-learn",
    "dotenv": "python-dotenv",
    "dateutil": "python-dateutil",
    "OpenSSL": "pyopenssl",
    "Crypto": "pycryptodome",
    "serial": "pyserial",
    "usb": "pyusb",
}
_MODULE_RE = re.compile(r"No module named ['\"]([\w\.]+)['\"]")


def _run_process(argv, cwd, timeout, on_process=None):
    """Run one process with a hard timeout and kill its whole process group."""
    proc = subprocess.Popen(
        argv,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=(os.name != "nt"),
    )
    if on_process:
        on_process(proc)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        return subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)
    except subprocess.TimeoutExpired:
        if os.name != "nt":
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            proc.kill()
        stdout, stderr = proc.communicate()
        stderr = (stderr or "") + f"\nTimeout dopo {timeout}s"
        return subprocess.CompletedProcess(argv, 124, stdout or "", stderr)
    finally:
        if on_process:
            on_process(None)


def _find_likely_entrypoint(sandbox_path: Path):
    """
    FIX: quando ci sono PIU' file .py e nessun main.py/entrypoint esplicito,
    prima si arrendeva subito ("no entrypoint trovato") a meno che non ci fosse
    ESATTAMENTE un solo file .py in tutto il progetto — capitava spesso con
    progetti scaffoldati a 2+ file (es. calculator.py + calculator_logic.py).

    Euristica aggiuntiva: cerca tra i file .py di primo livello (non in
    sottocartelle tipo test/, venv/) quello che contiene un blocco
    `if __name__ == "__main__"` — segnale forte che quel file e' pensato per
    essere eseguito direttamente, anche se ce ne sono altri (moduli di supporto).
    Se ne trova esattamente uno, lo usa. Se ne trova piu' di uno o zero, non
    decide (ambiguo) e lascia che il chiamante segnali l'errore come prima.
    """
    candidates = []
    for f in sandbox_path.glob("*.py"):
        try:
            content = f.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if "__main__" in content and "if __name__" in content:
            candidates.append(f)

    if len(candidates) == 1:
        return candidates
    return []


def run_project(sandbox_path, entrypoint=None, args=None, timeout=120,
                auto_install=False, on_process=None):
    sandbox_path = Path(sandbox_path).resolve()
    args = args or []

    req_file = sandbox_path / "requirements.txt"
    if req_file.exists() and auto_install:
        print("📦 Trovato requirements.txt, installo dipendenze...")
        setup_proc = _run_process(
            [sys.executable, "-m", "pip", "install", "-r", str(req_file), "-q"],
            cwd=sandbox_path, timeout=min(timeout, 180), on_process=on_process
        )
        if setup_proc.returncode != 0:
            print(f"⚠️ Installazione dipendenze fallita: {setup_proc.stderr}")
    elif req_file.exists():
        print("requirements.txt presente: auto-install disabilitato "
              "(imposta DEVIN_AUTO_INSTALL_DEPS=1 per abilitarlo)")

    candidates = []

    if entrypoint:
        candidates = list(sandbox_path.rglob(entrypoint))

    if not candidates:
        candidates = list(sandbox_path.rglob("main.py"))

    if not candidates:
        py_files = list(sandbox_path.glob("*.py"))
        if len(py_files) == 1:
            candidates = py_files

    if not candidates:
        # FIX: fallback euristico prima di arrendersi (vedi _find_likely_entrypoint)
        candidates = _find_likely_entrypoint(sandbox_path)
        if candidates:
            print(f"ℹ️ Entrypoint non specificato, individuato per euristica: {candidates[0].name}")

    if candidates:
        f = candidates[0]
        try:
            proc = _run_process(
                [sys.executable, str(f), *args], cwd=sandbox_path,
                timeout=timeout, on_process=on_process)
            # AUTO-INSTALL dipendenze mancanti (2026-07-10): il Coder scrive
            # `import pint` ma spesso NON aggiorna requirements.txt, quindi il
            # codice si applica e gira ma esplode su ModuleNotFoundError. Invece
            # di bruciare i retry rigenerando codice gia' corretto, installiamo il
            # pacchetto e rilanciamo. A catena (una dep alla volta), con guardia
            # anti-loop se il nome import != nome pip e non e' tra gli alias noti.
            seen = set()
            for _ in range(5):
                if proc.returncode == 0:
                    break
                m = _MODULE_RE.search(proc.stderr or "")
                if not m:
                    break
                mod = m.group(1).split(".")[0]
                if mod in seen:
                    break  # gia' provato: install "riuscito" ma import ancora KO (alias mancante)
                seen.add(mod)
                if not auto_install:
                    break
                pkg = _PIP_ALIASES.get(mod, mod)
                print(f"📦 ModuleNotFoundError '{mod}' → pip install '{pkg}'...")
                inst = _run_process(
                    [sys.executable, "-m", "pip", "install", pkg, "-q"],
                    cwd=sandbox_path, timeout=min(timeout, 180),
                    on_process=on_process)
                if inst.returncode != 0:
                    print(f"⚠️ install '{pkg}' fallito: {(inst.stderr or '')[:200]}")
                    break
                proc = _run_process(
                    [sys.executable, str(f), *args], cwd=sandbox_path,
                    timeout=timeout, on_process=on_process)
            return proc
        except Exception as e:
            return subprocess.CompletedProcess(
                args=["python3", str(f)],
                returncode=1,
                stdout="",
                stderr=f"Runner exception: {type(e).__name__}: {e}"
            )

    return subprocess.CompletedProcess(
        args=[],
        returncode=1,
        stdout="",
        stderr="no entrypoint trovato"
    )


class RunnerResult:
    def __init__(self, success: bool, error: str):
        self.success = success
        self.error = error


class Runner:
    def __init__(self, timeout: int = 120, auto_install=None):
        self.timeout = timeout
        if auto_install is None:
            auto_install = os.getenv("DEVIN_AUTO_INSTALL_DEPS", "").lower() in {
                "1", "true", "yes", "on"
            }
        self.auto_install = bool(auto_install)
        self._process = None
        self._process_lock = threading.Lock()

    def _set_process(self, process):
        with self._process_lock:
            self._process = process

    def stop(self):
        with self._process_lock:
            process = self._process
        if not process or process.poll() is not None:
            return
        if os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            process.kill()

    def run(self, sandbox_path: str, entrypoint: str = None,
            timeout: int = None) -> RunnerResult:
        """Esegue il progetto e mappa l'output nel formato richiesto."""
        try:
            proc = run_project(
                sandbox_path, entrypoint=entrypoint,
                timeout=timeout or self.timeout,
                auto_install=self.auto_install,
                on_process=self._set_process,
            )
            success = proc.returncode == 0
            error_msg = proc.stderr if not success else ""
            return RunnerResult(success=success, error=error_msg)
        except Exception as e:
            return RunnerResult(success=False, error=f"Runner.run() exception: {type(e).__name__}: {e}")
