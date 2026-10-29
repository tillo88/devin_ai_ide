"""La scatola della palestra (29/10): il codice dei casi scrive solo nel suo progetto, non vede ne'
ferma altri processi, non ha rete. Prove vere con bubblewrap dove c'e' (il rig, questo container)."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

import pytest

from devin.engine import ambienti


def _bwrap_funziona() -> bool:
    try:
        ambienti._scatola_provata.clear()
        ambienti.prova_scatola()
        return True
    except ambienti.ScatolaMancante:
        return False


serve_bwrap = pytest.mark.skipif(not _bwrap_funziona(), reason="bubblewrap non disponibile qui")


@pytest.fixture
def progetto(tmp_path):
    cartella = tmp_path / "progetto"
    cartella.mkdir()
    return cartella


def esegui(argv, cwd, env=None, **kw):
    return subprocess.run(ambienti.protetto(argv, cwd, env), cwd=str(cwd), capture_output=True, text=True,
                          timeout=60, env=env, **kw)


def test_fuori_dalla_scatola_niente_cambia(progetto):
    assert ambienti.scatola_attiva() is None
    assert ambienti.protetto(["python", "-c", "1"], progetto) == ["python", "-c", "1"]


def test_scatola_mancante_non_ripiega(monkeypatch, progetto):
    monkeypatch.setattr(ambienti, "BWRAP", "/non/esiste/bwrap")
    ambienti._scatola_provata.clear()
    with pytest.raises(ambienti.ScatolaMancante, match="non installato"):
        ambienti.prova_scatola("/non/esiste/bwrap")
    with pytest.raises(ambienti.ScatolaMancante):
        with ambienti.scatola(progetto):
            pass
    assert ambienti.scatola_attiva() is None


def test_l_argv_della_scatola(progetto, monkeypatch):
    monkeypatch.setattr(ambienti, "prova_scatola", lambda *a, **k: None)
    fd, junit = tempfile.mkstemp(suffix=".xml")
    os.close(fd)
    try:
        with ambienti.scatola(progetto):
            argv = ambienti.protetto(["python", "-m", "pytest", f"--junitxml={junit}"], progetto,
                                     {"HOME": "/home/tillo"})
            con_rete = ambienti.protetto(["python", "server.py"], progetto, {}, rete=True)
    finally:
        os.unlink(junit)
    assert argv[0] == ambienti.BWRAP and argv[-4:] == ["python", "-m", "pytest", f"--junitxml={junit}"]
    assert argv[argv.index("--ro-bind") + 1:argv.index("--ro-bind") + 3] == ["/", "/"]
    legati = [argv[i + 1] for i, a in enumerate(argv) if a == "--bind"]
    assert legati == [str(progetto.resolve()), str(Path(junit).resolve())], "solo il progetto e il report"
    for opzione in ("--unshare-pid", "--unshare-net", "--unshare-ipc", "--die-with-parent", "--tmpfs"):
        assert opzione in argv
    assert "--new-session" not in argv, "SIGTERM al gruppo deve arrivare dentro"
    assert argv[argv.index("HOME") + 1] == "/tmp", "la home vera e' in sola lettura"
    assert "--unshare-net" not in con_rete and "--unshare-pid" in con_rete


@pytest.fixture
def fuori_da_tmp():
    """Cartelle vere fuori da /tmp (nella scatola /tmp e' nuovo): come sul rig, dove i progetti
    del training stanno in /opt/devin-ai-ide-frontend/workspace e la home e' /home/tillo."""
    import shutil
    base = Path(tempfile.mkdtemp(prefix=".scatola-prova-", dir=str(Path(__file__).resolve().parent)))
    try:
        yield base
    finally:
        shutil.rmtree(base, ignore_errors=True)


@serve_bwrap
def test_dentro_si_scrive_solo_nel_progetto(fuori_da_tmp):
    progetto, casa = fuori_da_tmp / "progetto", fuori_da_tmp / "casa"
    progetto.mkdir()
    casa.mkdir()
    accanto = fuori_da_tmp / "accanto.txt"
    script = (f"open('dentro.txt', 'w').write('ok')\n"
              f"for p in ({str(accanto)!r}, {str(casa / 'x')!r}, '/usr/x', {str(Path(__file__).resolve())!r}):\n"
              f"    try:\n        open(p, 'a').write('x'); print('SCRIVE', p)\n"
              f"    except OSError as e: print('no', e.errno)\n"
              f"open('/tmp/mio', 'w').write('t'); print('tmp ok')\n")
    with ambienti.scatola(progetto):
        out = esegui([sys.executable, "-c", script], progetto, dict(os.environ, HOME=str(casa)))
    assert out.returncode == 0, out.stderr
    assert (progetto / "dentro.txt").read_text() == "ok"
    assert "SCRIVE" not in out.stdout and out.stdout.count("no 30") == 4, out.stdout   # 30 = EROFS
    assert "tmp ok" in out.stdout and not Path("/tmp/mio").exists(), "il /tmp della scatola e' suo"
    assert not accanto.exists() and not (casa / "x").exists()


@serve_bwrap
def test_dentro_non_si_vedono_altri_processi_ne_la_rete(progetto):
    script = ("import os\nprint(len([p for p in os.listdir('/proc') if p.isdigit()]))\n"
              "print(len(open('/proc/net/dev').read().splitlines()) - 2)\n"
              f"os.kill({os.getpid()}, 0)\n")
    with ambienti.scatola(progetto):
        out = esegui([sys.executable, "-c", script], progetto, dict(os.environ))
    righe = out.stdout.split()
    assert int(righe[0]) <= 3 and int(righe[1]) == 1, out.stdout
    assert "ProcessLookupError" in out.stderr, "il backend fuori dalla scatola non esiste per il caso"


@serve_bwrap
def test_sigterm_al_gruppo_arriva_dentro(progetto):
    script = ("import signal, sys, time\n"
              "signal.signal(signal.SIGTERM, lambda *a: (open('preso', 'w').write('si'), sys.exit(0)))\n"
              "print('pronto', flush=True)\ntime.sleep(60)\n")
    with ambienti.scatola(progetto):
        argv = ambienti.protetto([sys.executable, "-c", script], progetto, dict(os.environ))
    proc = subprocess.Popen(argv, cwd=str(progetto), stdout=subprocess.PIPE, text=True, start_new_session=True)
    assert proc.stdout.readline().strip() == "pronto"
    assert ambienti.termina_gruppo(proc.pid) >= 1, "SIGTERM al codice del caso, non a bubblewrap"
    proc.wait(timeout=20)
    for _ in range(50):
        if (progetto / "preso").exists():
            break
        time.sleep(0.1)
    assert (progetto / "preso").read_text() == "si", "il codice del caso riceve SIGTERM, niente SIGKILL"


@serve_bwrap
def test_gate_gold_agente_e_prova_dell_app_girano_nella_scatola(progetto, monkeypatch):
    (progetto / "sol.py").write_text("def f():\n    return 1\n")
    (progetto / "test_sol.py").write_text("from sol import f\n\n\ndef test_f():\n    assert f() == 1\n")
    (progetto / "test_gold_x.py").write_text("from sol import f\n\n\ndef test_gold():\n    assert f() == 1\n")
    lanciati = []
    vero_run, vero_popen = subprocess.run, subprocess.Popen

    def run(argv, *a, **k):
        lanciati.append(list(argv)[0])
        return vero_run(argv, *a, **k)

    monkeypatch.setattr(subprocess, "run", run)
    from devin.core.orchestrator import Orchestrator
    from devin.ui.routers.training import _verify_gold_tests_executed
    from devin.agents.tool_agent import AgenteStrumenti
    from devin.core import app_check
    with ambienti.scatola(progetto):
        assert Orchestrator._run_pytest_gate(object(), progetto)["success"] is True
        gold = _verify_gold_tests_executed(progetto, ["test_gold_x.py"])
        assert gold["executed"] is True, gold
        agente = AgenteStrumenti.__new__(AgenteStrumenti)
        agente.radice = progetto
        assert agente._esegui(["python", "-c", "open('/usr/x','w')"]).startswith("exit 1")
        assert agente._esegui(["python", "-c", "open('ok.txt','w').write('1')"]).startswith("exit 0")
        prova = app_check.esegui_piano(progetto, [{"tipo": "comando", "nome": "gira", "argv": ["python", "-c",
                                       "print('ciao')"], "contiene": ["ciao"], "non_contiene": [], "exit": 0,
                                       "secondi": 30, "stdin": ""}])
        assert prova["stato"] == "riuscita", prova
    assert (progetto / "ok.txt").exists()
    assert lanciati and all(a == ambienti.BWRAP for a in lanciati), lanciati


@serve_bwrap
def test_la_prova_http_raggiunge_il_server_nella_scatola(progetto):
    from devin.core import app_check
    (progetto / "app.py").write_text(
        "import http.server, os\n"
        "class H(http.server.BaseHTTPRequestHandler):\n"
        "    def do_GET(self):\n        self.send_response(200); self.end_headers(); self.wfile.write(b'ciao')\n"
        "http.server.HTTPServer(('127.0.0.1', int(os.environ['PORT'])), H).serve_forever()\n")
    with ambienti.scatola(progetto):
        prova = app_check.esegui_piano(progetto, [{"tipo": "http", "nome": "pagina", "argv": ["python", "app.py"],
                                                   "path": "/", "status": 200, "contiene": ["ciao"],
                                                   "non_contiene": [], "secondi": 20, "browser": False}])
    assert prova["stato"] == "riuscita", prova


def test_il_training_accende_la_scatola_e_senza_bubblewrap_e_un_guasto_del_rig(monkeypatch, tmp_path):
    import devin.ui.routers.training as training_router
    from test_verdetto_gold import giro
    monkeypatch.delenv("DEVIN_SCATOLA")
    assert training_router._scatola_nel_training() is True, "di norma accesa"
    monkeypatch.setattr(ambienti, "BWRAP", "/non/esiste/bwrap")
    monkeypatch.setattr(ambienti, "prova_scatola", lambda bwrap=None: (_ for _ in ()).throw(
        ambienti.ScatolaMancante("scatola non disponibile: bubblewrap non installato")))
    tentativo, job = giro(monkeypatch, tmp_path)
    assert tentativo["status"] == "runner_error" and "scatola non disponibile" in tentativo["error_reason"]
    assert ambienti.scatola_attiva() is None
    assert job["stopped"] == "scatola non disponibile"


def test_senza_scatola_il_giro_si_ferma_al_primo_caso(monkeypatch, tmp_path):
    import devin.ui.routers.training as training_router
    from devin.training.store import TrainingStore
    from test_training_runner_declass import _patch_runner_surface, _register_job, _runner_case
    monkeypatch.delenv("DEVIN_SCATOLA")
    monkeypatch.setattr(ambienti, "prova_scatola", lambda bwrap=None: (_ for _ in ()).throw(
        ambienti.ScatolaMancante("scatola non disponibile: bubblewrap non parte")))
    _patch_runner_surface(monkeypatch, tmp_path)
    casi = [_runner_case(case_id=f"c{i}", title=f"caso {i}") for i in range(4)]
    _register_job("job_s", casi)
    store = TrainingStore(tmp_path / "t")
    training_router._run_training_cases_background("job_s", store, casi, "mbpp")
    with training_router._training_jobs_lock:
        job = dict(training_router._training_jobs["job_s"])
    assert job["runner_error"] == 1 and job["completed"] == 1 and job["stopped"] == "scatola non disponibile"
    assert job["status"] == "finished" and len(store.list_attempts()) == 1


@serve_bwrap
def test_il_training_verifica_i_gold_nella_scatola(monkeypatch, tmp_path):
    from test_verdetto_gold import giro
    monkeypatch.setenv("DEVIN_SCATOLA", "1")
    lanciati = []
    vero = subprocess.run

    def run(argv, *a, **k):
        lanciati.append(list(argv))
        return vero(argv, *a, **k)
    monkeypatch.setattr(subprocess, "run", run)
    tentativo, _job = giro(monkeypatch, tmp_path)
    assert tentativo["status"] == "auto_success", tentativo["error_reason"]
    pytest_lanciati = [a for a in lanciati if "pytest" in a]
    assert pytest_lanciati and all(a[0] == ambienti.BWRAP for a in pytest_lanciati)
    assert ambienti.scatola_attiva() is None


@serve_bwrap
@pytest.mark.parametrize("modulo", ["ambienti", "bigcodebench"])
def test_la_chiusura_e_gentile_anche_nella_calibrazione(fuori_da_tmp, modulo):
    """Calibrazione e prova dell'app chiudono con SIGTERM al programma: il suo handler gira."""
    from devin.training import bigcodebench
    cartella = fuori_da_tmp / "compito"
    cartella.mkdir()
    script = ("import signal, sys, time\n"
              "signal.signal(signal.SIGTERM, lambda *a: (open('preso', 'w').write('si'), sys.exit(0)))\n"
              "print('pronto', flush=True)\ntime.sleep(60)\n")
    argv = bigcodebench.argv_isolato(ambienti.BWRAP, cartella, [sys.executable, "-c", script])
    proc = subprocess.Popen(argv, cwd=str(cartella), stdout=subprocess.PIPE, text=True, start_new_session=True)
    assert proc.stdout.readline().strip() == "pronto"
    if modulo == "ambienti":
        ambienti.termina_gruppo(proc.pid)
    else:
        bigcodebench._sigterm_al_gruppo(proc.pid)
    proc.wait(timeout=20)
    assert (cartella / "preso").read_text() == "si"


@serve_bwrap
def test_anche_runner_e_goal_mode_passano_dalla_scatola(progetto, monkeypatch):
    from devin.engine import runner
    from devin.core import goal_mode
    lanciati = []
    vero_popen, vero_run = subprocess.Popen, subprocess.run

    def popen(argv, *a, **k):
        lanciati.append(list(argv)[0])
        return vero_popen(argv, *a, **k)

    def run(argv, *a, **k):
        lanciati.append(list(argv)[0])
        return vero_run(argv, *a, **k)
    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(subprocess, "run", run)
    with ambienti.scatola(progetto):
        fatto = runner._run_process([sys.executable, "-c", "open('/usr/x', 'w')"], progetto, 30)
        assert fatto.returncode != 0 and "Read-only file system" in fatto.stderr
        assert goal_mode._run([sys.executable, "-c", "print(1)"], progetto, 30)["returncode"] == 0
    assert len(lanciati) >= 2 and set(lanciati) == {ambienti.BWRAP}, lanciati
