"""pytest confinato al progetto (#1856): niente rootdir, ini o conftest presi da cartelle antenate."""
from pathlib import Path

from devin.core.goal_mode import Criterion, _check_tests_pass
from devin.core.orchestrator import Orchestrator
from devin.engine.pytest_isolato import argomenti_isolati, config_del_progetto
from devin.training.adapters import build_mbpp_gold_test
from devin.ui.routers.training import _verify_gold_tests_executed

GIUSTO = "def is_not_prime(n):\n    return n < 2 or any(n % i == 0 for i in range(2, n))\n"
SBAGLIATO = "def is_not_prime(n):\n    return False\n"
GOLD = build_mbpp_gold_test(3, "", ["assert is_not_prime(2) == False", "assert is_not_prime(10) == True"])


def progetto_dentro_un_repo(tmp_path: Path, codice: str = GIUSTO) -> Path:
    """Come sul rig: <repo DEVIN con pytest.ini e conftest.py>/workspace/_training_runs/mbpp/<caso>."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "pytest.ini").write_text("[pytest]\naddopts = -p no:doctest\nnorecursedirs = workspace\n")
    # un conftest antenato che, se caricato, romperebbe ogni test del progetto
    (tmp_path / "conftest.py").write_text(
        "import pytest\n\n@pytest.fixture(autouse=True)\ndef _antenato():\n"
        "    raise RuntimeError('conftest del repo caricato nella sandbox')\n")
    proj = tmp_path / "workspace" / "_training_runs" / "mbpp" / "mbpp_3_train_x"
    proj.mkdir(parents=True)
    # come sul rig (#1858): la sandbox dello scaffold dentro il progetto, con COPIE dei file
    sandbox = proj / "workspace" / "sandboxes" / "train_x"
    for cartella in (proj, sandbox):
        cartella.mkdir(parents=True, exist_ok=True)
        (cartella / "primes.py").write_text(codice)
        (cartella / "test_gold_mbpp_3.py").write_text(GOLD)
    (proj / ".devin_state" / "backup").mkdir(parents=True)
    (proj / ".devin_state" / "backup" / "test_gold_mbpp_3.py").write_text(GOLD)
    return proj


def test_gold_verificati_anche_dentro_il_repo(tmp_path) -> None:
    ev = _verify_gold_tests_executed(progetto_dentro_un_repo(tmp_path), ["test_gold_mbpp_3.py"])
    assert ev["executed"] is True and ev["missing"] == [], ev
    ev = _verify_gold_tests_executed(progetto_dentro_un_repo(tmp_path / "b", SBAGLIATO), ["test_gold_mbpp_3.py"])
    assert ev["executed"] is False and ev["missing"] == ["test_gold_mbpp_3.py"]


def test_il_gate_e_goal_mode_non_caricano_il_conftest_antenato(tmp_path) -> None:
    proj = progetto_dentro_un_repo(tmp_path)
    gate = Orchestrator._run_pytest_gate(object(), proj)
    assert gate["success"] is True and gate["command"] == "pytest", gate["output"]
    esito = _check_tests_pass(Criterion("tests_pass", {}), proj, True)
    assert esito.passed, esito.detail
    sbagliato = progetto_dentro_un_repo(tmp_path / "b", SBAGLIATO)
    assert Orchestrator._run_pytest_gate(object(), sbagliato)["success"] is False


def test_la_configurazione_del_progetto_resta_sua(tmp_path) -> None:
    proj = progetto_dentro_un_repo(tmp_path)
    assert config_del_progetto(proj) is None
    (proj / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    assert config_del_progetto(proj) is None, "pyproject senza sezione pytest non conta"
    (proj / "pyproject.toml").write_text("[tool.pytest.ini_options]\naddopts = '-q'\n")
    assert config_del_progetto(proj) == proj / "pyproject.toml"
    (proj / "pytest.ini").write_text("[pytest]\n")
    with argomenti_isolati(proj) as args:
        r = proj.resolve()
        assert args == ["-c", str(proj / "pytest.ini"), "--rootdir", str(r), "--confcutdir", str(r),
                        "--ignore", str(r / "workspace"), "--ignore", str(r / ".devin_state")]
    with argomenti_isolati(tmp_path / "workspace") as args:
        vuoto = Path(args[1])
        assert vuoto.is_file() and not vuoto.is_relative_to(tmp_path), "ini vuoto fuori dal progetto"
    assert not vuoto.exists(), "e poi cancellato"


def test_un_conftest_del_modello_che_salta_i_gold_resta_scoperto(tmp_path) -> None:
    """L'isolamento non apre il buco che la verifica dei gold chiude: il conftest del progetto vale."""
    proj = progetto_dentro_un_repo(tmp_path)
    (proj / "conftest.py").write_text("collect_ignore = ['test_gold_mbpp_3.py']\n")
    (proj / "test_mio.py").write_text("def test_ok():\n    assert True\n")
    ev = _verify_gold_tests_executed(proj, ["test_gold_mbpp_3.py"])
    assert ev["executed"] is False and ev["missing"] == ["test_gold_mbpp_3.py"]
