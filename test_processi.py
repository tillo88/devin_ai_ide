"""run_limitato (30/10): allo scadere SIGTERM al gruppo, mai SIGKILL; forma di subprocess.run."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from devin.engine import processi


def vivo(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        stato = Path(f"/proc/{pid}/stat").read_text().split(")")[-1].split()[0]
        return stato != "Z"
    except OSError:
        return True


def test_come_subprocess_run():
    out = processi.run_limitato([sys.executable, "-c", "import sys; print(sys.stdin.read().upper())"],
                                input="ciao", capture_output=True, text=True, timeout=30)
    assert out.returncode == 0 and out.stdout.strip() == "CIAO"
    out = processi.run_limitato([sys.executable, "-c", "import sys; print(repr(sys.stdin.read()))"],
                                capture_output=True, text=True, timeout=30)
    assert out.stdout.strip() == "''", "senza input lo stdin e' /dev/null, mai la tastiera del backend"


def test_sessione_propria():
    out = processi.run_limitato([sys.executable, "-c", "import os; print(os.getsid(0) == os.getpid())"],
                                capture_output=True, text=True, timeout=30)
    assert out.stdout.strip() == "True"


def test_allo_scadere_sigterm_a_tutto_il_gruppo(tmp_path):
    pidfile = tmp_path / "nipote"
    script = ("import multiprocessing, signal, sys, time\n"
              f"open({str(tmp_path / 'figlio')!r}, 'w').write('')\n"
              "signal.signal(signal.SIGTERM, lambda *a: (open("
              f"{str(tmp_path / 'preso')!r}, 'w').write('si'), sys.exit(0)))\n"
              "p = multiprocessing.Process(target=time.sleep, args=(120,)); p.start()\n"
              f"open({str(pidfile)!r}, 'w').write(str(p.pid))\n"
              "time.sleep(120)\n")
    inizio = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired) as preso:
        processi.run_limitato([sys.executable, "-c", script], capture_output=True, text=True, timeout=3)
    assert time.monotonic() - inizio < 30
    assert preso.value.processo_rimasto == ""
    assert (tmp_path / "preso").read_text() == "si", "SIGTERM: il programma ha potuto chiudersi da solo"
    nipote = int(pidfile.read_text())
    for _ in range(50):
        if not vivo(nipote):
            break
        time.sleep(0.1)
    assert not vivo(nipote), "anche i processi lanciati dal figlio"


def test_mai_sigkill(monkeypatch):
    segnali = []
    vero_kill, vero_killpg = os.kill, os.killpg

    def kill(pid, segnale):
        segnali.append(segnale)
        return vero_kill(pid, segnale)

    def killpg(pgid, segnale):
        segnali.append(segnale)
        return vero_killpg(pgid, segnale)
    monkeypatch.setattr(os, "kill", kill)
    monkeypatch.setattr(os, "killpg", killpg)
    script = ("import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
              "signal.signal(signal.SIGINT, signal.SIG_IGN); time.sleep(6)")
    with pytest.raises(subprocess.TimeoutExpired) as preso:
        processi.run_limitato([sys.executable, "-c", script], timeout=1, grazia=1.0)
    assert signal.SIGKILL not in segnali and signal.SIGTERM in segnali and signal.SIGINT in segnali
    assert "ancora vivo dopo SIGTERM e SIGINT (nessun SIGKILL)" in preso.value.processo_rimasto


def test_check_non_supportato():
    with pytest.raises(ValueError):
        processi.run_limitato(["true"], timeout=5, check=True)


def test_nessun_sigkill_nel_codice_di_devin():
    """Il guardrail del rig, nel sorgente: niente SIGKILL e niente .kill() da nessuna parte."""
    radice = Path(__file__).resolve().parent / "devin"
    colpevoli = []
    for path in radice.rglob("*.py"):
        for numero, riga in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            codice = riga.split("#", 1)[0]
            if "signal.SIGKILL" in codice or ".kill()" in codice:
                colpevoli.append(f"{path.relative_to(radice.parent)}:{numero}: {riga.strip()}")
    assert not colpevoli, colpevoli


def test_i_punti_che_eseguono_codice_usano_run_limitato():
    radice = Path(__file__).resolve().parent / "devin"
    for rel in ("ui/routers/training.py", "core/orchestrator.py", "agents/tool_agent.py", "core/app_check.py",
                "core/goal_mode.py", "core/risolutore.py", "core/modella3d.py", "core/affetta.py",
                "engine/shell.py"):
        testo = (radice / rel).read_text(encoding="utf-8")
        assert "run_limitato(" in testo, rel


def test_stdin_e_dev_null_senza_input(monkeypatch):
    visti = {}
    vero = subprocess.Popen

    def popen(argv, **kw):
        visti.update(kw)
        return vero(argv, **kw)
    monkeypatch.setattr(subprocess, "Popen", popen)
    processi.run_limitato(["true"], timeout=10)
    assert visti["stdin"] is subprocess.DEVNULL
