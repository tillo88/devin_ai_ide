"""BigCodeBench nella palestra (28/10): dataset verificato, gold, filtro, calibrazione, import."""
import asyncio
import gzip
import hashlib
import json
from pathlib import Path
import sys

import pytest

from devin.training import bigcodebench as b
from devin.training.store import TrainingStore

TEST_SOMMA = '''import unittest
import os


class TestCases(unittest.TestCase):
    def test_somma(self):
        self.assertEqual(task_func([1, 2, 3]), 6)

    def test_vuota(self):
        self.assertEqual(task_func([]), 0)
'''


def compito(n=1, soluzione="    return sum(numeri)\n", test=TEST_SOMMA, prompt="def task_func(numeri):\n"):
    return {"task_id": f"BigCodeBench/{n}", "instruct_prompt": f"Sum the numbers (task {n}).\nMore text.",
            "code_prompt": prompt, "canonical_solution": soluzione, "test": test,
            "entry_point": "task_func", "libs": ["os"]}


def esito(cartella: Path, soluzione: str, task) -> int:
    import subprocess
    (cartella / "solution.py").write_text(soluzione)
    (cartella / b.gold_nome(task)).write_text(b.gold_test(task))
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-c", "/dev/null",
                           "--rootdir", str(cartella), b.gold_nome(task)], cwd=cartella, capture_output=True,
                          text=True, timeout=120)
    return proc.returncode


# --- il gold ---------------------------------------------------------------------------

def test_il_gold_passa_con_la_soluzione_giusta_e_non_con_quella_sbagliata(tmp_path):
    task = compito()
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    assert esito(tmp_path / "a", "def task_func(numeri):\n    return sum(numeri)\n", task) == 0
    assert esito(tmp_path / "b", "def task_func(numeri):\n    return len(numeri)\n", task) != 0


def test_il_gold_ignora_i_test_di_devin_e_trova_la_funzione_in_qualunque_modulo(tmp_path):
    task = compito()
    (tmp_path / "test_solution.py").write_text("raise RuntimeError('i test di DEVIN non si caricano nel gold')\n")
    (tmp_path / "aiuto.py").write_text("def doppio(x):\n    return 2 * x\n")
    assert esito(tmp_path, "from aiuto import doppio\n\ndef task_func(numeri):\n    return sum(numeri)\n",
                 task) == 0


def test_multiprocessing_ritrova_le_funzioni_della_soluzione(tmp_path):
    # BigCodeBench/363: Pool.map su una funzione della soluzione. In un namespace finto pickle fallisce.
    test = '''import unittest


class TestCases(unittest.TestCase):
    def test_pool(self):
        self.assertEqual(task_func([1, 2, 3]), {1: 1, 2: 4, 3: 9})
'''
    soluzione = ("from multiprocessing import Pool\n\n\ndef quadrato(x):\n    return x * x\n\n\n"
                 "def task_func(numeri):\n    with Pool(2) as pool:\n"
                 "        return dict(zip(numeri, pool.map(quadrato, numeri)))\n")
    assert esito(tmp_path, soluzione, compito(test=test)) == 0


def test_la_soluzione_e_un_modulo_a_se_non_vede_gli_import_del_test(tmp_path):
    # il test importa os; la soluzione che lo usa senza importarlo e' sbagliata (harness ufficiale: passerebbe)
    assert esito(tmp_path, "def task_func(numeri):\n    os.getcwd()\n    return sum(numeri)\n", compito()) != 0


# --- filtro di sicurezza -----------------------------------------------------------------

@pytest.mark.parametrize("codice", ["os.kill(pid, 9)", "import subprocess", "psutil.process_iter()",
                                    "signal.signal(1, f)", "proc.terminate()", "open('/etc/passwd')",
                                    "os.system('ls')", "import ctypes", "'sudo rm'"])
def test_pericoloso(codice):
    assert b.pericoloso(compito(soluzione=f"    {codice}\n"))
    assert b.pericoloso(compito(test=TEST_SOMMA + f"\n# {codice}\n"))


@pytest.mark.parametrize("codice", ["os.remove(f)", "os.path.join(a, b)", "shutil.rmtree(d)",
                                    "os.makedirs(d)", "'/tmp/x'", "skill = 1", "os.environ['A']"])
def test_non_pericoloso(codice):
    assert not b.pericoloso(compito(soluzione=f"    {codice}\n"))


# --- i casi del training -------------------------------------------------------------------

def calibrazione(**esiti):
    return {"versione": b.VERSIONE, "esiti": {f"BigCodeBench/{k[1:]}": {"ok": v} for k, v in esiti.items()}}


def test_casi_solo_calibrati_verdi_e_non_pericolosi():
    tasks = [compito(1), compito(2), compito(3), compito(4, soluzione="    os.system('x')\n")]
    out = b.casi(tasks, calibrazione(n1=True, n2=False, n4=True), limit=10)
    assert out["idonei"] == 1
    assert out["scartati"] == {"non_calibrati": 1, "soluzione_ufficiale_rossa": 1, "pericolosi": 1}
    (caso,) = out["casi"]
    assert caso["ambiente"] == "bigcodebench" and caso["bcb_task_id"] == "BigCodeBench/1"
    assert caso["expected_signals"] == ["tests_pass"] and caso["title"] == "BigCodeBench 1: Sum the numbers (task 1)."
    assert list(caso["gold_tests"]) == ["test_gold_bcb_1.py"]
    assert "solution.py" in caso["prompt"] and "task_func" in caso["prompt"]
    # il test ufficiale e la soluzione ufficiale non finiscono nel compito
    assert "TestCases" not in caso["prompt"] and "return sum" not in caso["prompt"]


def test_casi_limit_e_offset():
    tasks = [compito(n) for n in range(1, 6)]
    out = b.casi(tasks, calibrazione(**{f"n{n}": True for n in range(1, 6)}), limit=2, offset=3)
    assert [c["bcb_task_id"] for c in out["casi"]] == ["BigCodeBench/4", "BigCodeBench/5"]


def cartella_dati(tmp_path, tasks, cal):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "tasks.jsonl").write_text("".join(json.dumps(t) + "\n" for t in tasks))
    (tmp_path / "calibrazione.json").write_text(json.dumps(cal))
    return tmp_path


def test_import_nello_store(tmp_path):
    dati = cartella_dati(tmp_path / "dati", [compito(1), compito(2)], calibrazione(n1=True, n2=True))
    store = TrainingStore(tmp_path / "store")
    out = b.import_bigcodebench_cases(store, limit=10, cartella=dati)
    assert out["created"] == 2 and out["idonei"] == 2 and out["versione"] == "v0.1.1"
    caso = [c for c in store.list_cases(limit=100) if c.get("source") == "bigcodebench"][0]
    assert caso["metadata"]["ambiente"] == "bigcodebench"
    assert set(caso["metadata"]["gold_tests"]) == {"test_gold_bcb_1.py"} or \
        set(caso["metadata"]["gold_tests"]) == {"test_gold_bcb_2.py"}
    # reimportare non duplica
    assert b.import_bigcodebench_cases(store, limit=10, cartella=dati)["created"] == 0


def test_import_rifiuta_una_calibrazione_di_un_altra_versione(tmp_path):
    dati = cartella_dati(tmp_path, [compito(1)], {"versione": "v0.1.4", "esiti": {}})
    with pytest.raises(ValueError, match="calibrazione"):
        b.import_bigcodebench_cases(TrainingStore(tmp_path / "s"), cartella=dati)


def test_endpoint_di_import(tmp_path, monkeypatch):
    import devin.ui.routers.training as training_router
    dati = cartella_dati(tmp_path / "dati", [compito(1)], calibrazione(n1=True))
    monkeypatch.setattr(b, "CARTELLA", dati)
    store = TrainingStore(tmp_path / "store")
    monkeypatch.setattr(training_router, "_training_store_for", lambda _p: store)
    percorsi = {r.path for r in training_router.router.routes}
    assert "/api/training/adapters/bigcodebench/import" in percorsi

    class Richiesta:
        async def json(self):
            return {"limit": 5}
    out = asyncio.run(training_router.api_training_adapter_bigcodebench(Richiesta()))
    assert out["created"] == 1, out
    monkeypatch.setattr(b, "CARTELLA", tmp_path / "manca")
    out = asyncio.run(training_router.api_training_adapter_bigcodebench(Richiesta()))
    assert "import BigCodeBench fallito" in out["error"]


# --- il dataset ----------------------------------------------------------------------------

def gz(tasks):
    return gzip.compress("".join(json.dumps(t) + "\n" for t in tasks).encode())


def test_prepara_accetta_solo_il_file_con_lo_sha_fissato(tmp_path, monkeypatch):
    dati = gz([compito(1), compito(2)])
    (tmp_path / "x.gz").write_bytes(dati)
    destinazione = tmp_path / "out" / "tasks.jsonl"
    with pytest.raises(ValueError, match="sha256"):
        b.prepara(destinazione, tmp_path / "x.gz")
    assert not destinazione.exists()
    monkeypatch.setattr(b, "SHA256", hashlib.sha256(dati).hexdigest())
    monkeypatch.setattr(b, "RIGHE", 3)
    with pytest.raises(ValueError, match="righe"):
        b.prepara(destinazione, tmp_path / "x.gz")
    monkeypatch.setattr(b, "RIGHE", 2)
    out = b.prepara(destinazione, tmp_path / "x.gz")
    assert out["righe"] == 2 and [t["task_id"] for t in b.leggi_tasks(destinazione)] == ["BigCodeBench/1",
                                                                                         "BigCodeBench/2"]
    assert not (destinazione.parent / "tasks.jsonl.parziale").exists()


def test_prepara_rifiuta_righe_senza_i_campi(tmp_path, monkeypatch):
    rotto = compito(1)
    del rotto["test"]
    dati = gz([rotto])
    (tmp_path / "x.gz").write_bytes(dati)
    monkeypatch.setattr(b, "SHA256", hashlib.sha256(dati).hexdigest())
    monkeypatch.setattr(b, "RIGHE", 1)
    with pytest.raises(ValueError, match="campi"):
        b.prepara(tmp_path / "tasks.jsonl", tmp_path / "x.gz")


def test_versione_e_url_coerenti():
    assert b.VERSIONE == "v0.1.1" and f"/{b.VERSIONE}/BigCodeBench.jsonl.gz" in b.URL
    assert b.URL.startswith("https://github.com/bigcode-project/") and len(b.SHA256) == 64 and b.RIGHE == 1140


# --- calibrazione ----------------------------------------------------------------------------

def test_calibrazione(tmp_path):
    tasks = [compito(1), compito(2, soluzione="    return 0\n"), compito(3, soluzione="    os.kill(1, 15)\n")]
    out = b.calibra(tasks, sys.executable, paralleli=2)
    assert out["versione"] == b.VERSIONE and out["isolamento"] == "nessuno"
    assert out["totali"] == 3 and out["ok"] == 1 and out["pericolosi"] == 1
    assert out["esiti"]["BigCodeBench/1"]["ok"] is True
    assert out["esiti"]["BigCodeBench/2"]["ok"] is False and "FAILED" in out["esiti"]["BigCodeBench/2"]["motivo"]
    assert out["esiti"]["BigCodeBench/3"]["motivo"] == "pericoloso"


def test_calibrazione_ogni_compito_ha_i_suoi_temporanei():
    test = """import unittest
import os
import tempfile


class TestCases(unittest.TestCase):
    def test_tmp(self):
        self.assertEqual(os.path.realpath(tempfile.gettempdir()), os.path.realpath(os.getcwd()))
"""
    assert b.calibra_task(compito(test=test), sys.executable)["ok"] is True


def test_calibrazione_timeout(tmp_path):
    lento = compito(1, soluzione="    import time\n    time.sleep(30)\n    return sum(numeri)\n")
    out = b.calibra_task(lento, sys.executable, secondi=3)
    assert out["ok"] is False and out["motivo"] == "timeout 3s"


def test_argv_isolato():
    argv = ["python", "-m", "pytest"]
    assert b.argv_isolato(None, Path("/x"), argv) == argv
    isolato = b.argv_isolato("/usr/bin/bwrap", Path("/x"), argv)
    assert isolato[0] == "/usr/bin/bwrap" and isolato[-3:] == argv
    for opzione in ("--unshare-net", "--unshare-pid", "--die-with-parent", "--new-session"):
        assert opzione in isolato
    assert isolato[isolato.index("--ro-bind") + 1:isolato.index("--ro-bind") + 3] == ["/", "/"]
    assert isolato[isolato.index("--bind") + 1:isolato.index("--bind") + 3] == ["/x", "/x"]


def test_riga_di_comando(tmp_path, monkeypatch, capsys):
    dati = gz([compito(1)])
    (tmp_path / "x.gz").write_bytes(dati)
    monkeypatch.setattr(b, "SHA256", hashlib.sha256(dati).hexdigest())
    monkeypatch.setattr(b, "RIGHE", 1)
    assert b.main(["prepara", str(tmp_path / "tasks.jsonl"), "--da", str(tmp_path / "x.gz")]) == 0
    assert b.main(["calibra", str(tmp_path / "tasks.jsonl"), str(tmp_path / "cal.json"), "--paralleli", "1"]) == 0
    cal = json.loads((tmp_path / "cal.json").read_text())
    assert cal["ok"] == 1 and cal["versione"] == b.VERSIONE
