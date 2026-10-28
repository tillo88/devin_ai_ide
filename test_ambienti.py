"""Ambienti di prova (28/10, BigCodeBench): un solo punto decide con che Python gira il codice del progetto."""
import os
from pathlib import Path
import sys

import pytest

from devin.engine import ambienti


def venv_finto(home: Path, nome: str = "bigcodebench") -> Path:
    """Un 'venv' il cui python e' un link al python vero: basta per vedere CHI viene usato."""
    cartella = home / ambienti.AMBIENTI[nome] / "bin"
    cartella.mkdir(parents=True)
    python = cartella / "python"
    python.symlink_to(sys.executable)
    return python


def test_fuori_dal_blocco_resta_il_python_di_devin(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert ambienti.python_corrente() == sys.executable and ambienti.nome_corrente() is None
    env = {"PATH": "/usr/bin"}
    assert ambienti.env_corrente(env) == env


def test_dentro_il_blocco_tutto_usa_l_ambiente(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    python = venv_finto(tmp_path)
    with ambienti.usa("bigcodebench") as scelto:
        assert scelto == str(python) == ambienti.python_corrente()
        env = ambienti.env_corrente({"PATH": "/usr/bin"})
        assert env["PATH"].split(os.pathsep)[0] == str(python.parent) and env["VIRTUAL_ENV"].endswith("venv-bigcodebench")
        assert env["CUDA_VISIBLE_DEVICES"] == "", "la GPU e' del modello"
    assert ambienti.python_corrente() == sys.executable, "uscito dal blocco si torna a DEVIN"


def test_niente_ripieghi_silenziosi(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(ambienti.AmbienteMancante, match="non installato"):
        with ambienti.usa("bigcodebench"):
            pass
    with pytest.raises(ambienti.AmbienteMancante, match="sconosciuto"):
        ambienti.interprete("/usr/bin/python3")      # mai un percorso dai dati


def test_gate_verifica_gold_agente_e_prova_dell_app_usano_l_ambiente(tmp_path, monkeypatch):
    """I quattro punti che eseguono il codice del progetto passano dall'ambiente corrente."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    python = venv_finto(tmp_path / "home")
    chiamate, gpu = [], []
    vero = __import__("subprocess").run

    def run(argv, *a, **k):
        chiamate.append((list(argv), (k.get("env") or {}).get("VIRTUAL_ENV", "")))
        gpu.append((k.get("env") or {}).get("CUDA_VISIBLE_DEVICES"))
        return vero(argv, *a, **k)

    import subprocess
    monkeypatch.setattr(subprocess, "run", run)
    progetto = tmp_path / "p"
    progetto.mkdir()
    (progetto / "sol.py").write_text("def f():\n    return 1\n")
    (progetto / "test_sol.py").write_text("from sol import f\n\ndef test_f():\n    assert f() == 1\n")
    from devin.core.orchestrator import Orchestrator
    from devin.ui.routers.training import _verify_gold_tests_executed
    from devin.agents.tool_agent import AgenteStrumenti
    from devin.core import app_check
    with ambienti.usa("bigcodebench"):
        assert Orchestrator._run_pytest_gate(object(), progetto)["success"] is True
        assert _verify_gold_tests_executed(progetto, ["test_sol.py"])["executed"] is True
        agente = AgenteStrumenti.__new__(AgenteStrumenti)
        agente.radice = progetto
        agente._esegui(["python", "-c", "print(1)"])
        agente._esegui(["pytest", "-q"])
        assert app_check._argv({"argv": ["python", "sol.py"]}, None)[0] == str(python)
        assert app_check._ambiente(progetto)["VIRTUAL_ENV"].endswith("venv-bigcodebench")
        assert app_check._ambiente(progetto)["CUDA_VISIBLE_DEVICES"] == ""
    eseguiti = [argv for argv, _ in chiamate]
    assert eseguiti and all(argv[0] == str(python) for argv in eseguiti), eseguiti
    assert all(venv.endswith("venv-bigcodebench") for _, venv in chiamate)
    assert gpu and all(g == "" for g in gpu), "niente GPU per il codice del benchmark mentre il modello gira"
    assert ["-m", "pytest", "-q"] == eseguiti[-1][1:4], "pytest dell'agente = python -m pytest dell'ambiente"


def test_il_training_fa_girare_il_caso_nel_suo_ambiente(tmp_path, monkeypatch):
    import devin.ui.routers.training as training_router
    from devin.training.store import TrainingStore
    from test_training_runner_declass import (_patch_runner_surface, _register_job, _fake_gold_check,  # noqa: F401
                                              _runner_case, GREEN_GATE, _isolate_training_jobs)
    visti = []

    class Orch:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def run_scaffold(self, task, project_path, run_id):
            visti.append(ambienti.python_corrente())
            (Path(project_path) / "sol.py").write_text("def f():\n    return 1\n")
            return {"success": True, "quality_gate": dict(GREEN_GATE), "files_written": ["sol.py"]}

    fast_app = _patch_runner_surface(monkeypatch, tmp_path)
    monkeypatch.setattr(fast_app, "Orchestrator", Orch)
    monkeypatch.setattr(training_router, "_verify_gold_tests_executed",
                        lambda *a, **k: (visti.append(("gold", ambienti.python_corrente())), _fake_gold_check(*a, **k))[1])
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    caso = _runner_case(metadata={"ambiente": "bigcodebench", "gold_tests": {"test_gold.py": "def test_g():\n    assert 1\n"}})
    _register_job("job_amb", [caso])
    store = TrainingStore(tmp_path / "t")
    training_router._run_training_cases_background("job_amb", store, [caso], "bigcodebench")
    tentativo = store.list_attempts()[-1]
    assert tentativo["status"] == "runner_error" and "non installato" in tentativo["error_reason"], \
        "ambiente mancante = guasto del rig, mai un fallimento di DEVIN"
    assert visti == [], "il modello non e' nemmeno partito"
    python = venv_finto(tmp_path / "home")
    _register_job("job_amb2", [caso])
    training_router._run_training_cases_background("job_amb2", store, [caso], "bigcodebench")
    assert store.list_attempts()[-1]["status"] == "auto_success", store.list_attempts()[-1]["error_reason"]
    assert visti == [str(python), ("gold", str(python))], "scaffold e verifica dei gold nello stesso ambiente"
    assert ambienti.python_corrente() == sys.executable, "finito il caso si torna a DEVIN"
