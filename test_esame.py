"""Modalita' esame (30/10, LiveCodeBench): un esame misura, non insegna."""
import asyncio
import json
from pathlib import Path

import pytest

import devin.ui.routers.training as training_router
from devin.ai import web_search
from devin.ai.hybrid_memory_client import MemoriaSolaLettura
from devin.core.fix_memory import ricorda_correzione
from devin.core.orchestrator import Orchestrator
from devin.training import livecodebench as lcb
from devin.training.store import TrainingStore
from test_training_runner_declass import (_fake_gold_check, _patch_runner_surface, _register_job,  # noqa: F401
                                          _runner_case, GREEN_GATE)


class _Req:
    def __init__(self, data):
        self._data = data

    async def json(self):
        return self._data


class Locale:
    def __init__(self, path):
        self.path = path
        self.enabled = True
        self.scritti = []

    def store(self, *a, **k):
        self.scritti.append(a)
        return "local_stored"

    def recall(self, query, tags=None, limit=3):
        return [f"ricordo di {query}"]

    def count(self):
        return 1

    def status(self):
        return {"enabled": True}


class Memoria:
    def __init__(self, tmp_path):
        self.enabled = True
        self.local = Locale(tmp_path / "local_memories.jsonl")
        self.scritti = []

    def recall(self, query, tags=None, limit=3):
        return self.local.recall(query)

    def store_local(self, contenuto, tags=None, importance=0.5):
        self.scritti.append(contenuto)
        return "local_stored"

    def store(self, *a, **k):
        self.scritti.append(a)
        return "stored"

    def flush_outbox(self):
        raise AssertionError("nessun invio da un esame")

    def outbox_size(self):
        return 0

    def status(self):
        return {"enabled": True}


def compito(**extra):
    return {"id": "atcoder-abc400_a", "piattaforma": "atcoder", "data": "2026-09-06T00:00:00",
            "difficolta": "easy", "titolo": "Somma", "testo": "Read A and B, print A+B.", "starter_code": "",
            "tipo": "stdin", "func_name": None, "errata": None, **extra}


# --- la memoria in sola lettura ----------------------------------------------------------------

def test_la_memoria_dell_esame_si_legge_e_non_si_scrive(tmp_path):
    vera = Memoria(tmp_path)
    m = MemoriaSolaLettura(vera)
    assert m.recall("errore") == ["ricordo di errore"] and m.local.path == vera.local.path
    assert m.store_local("x") == m.store("x") == m.local.store("x") == "not_recorded"
    assert m.flush_outbox() == 0
    assert ricorda_correzione(m, progetto="p", errore="E: boom", correzione="fix", origine="esame") == "not_recorded"
    assert vera.scritti == [] and vera.local.scritti == []
    with pytest.raises(AttributeError):
        m.scrivi_qualcosa_di_nuovo  # noqa: B018 - nessun inoltro generico


def test_modalita_esame_dell_orchestrator(tmp_path):
    orch = object.__new__(Orchestrator)
    orch.config = {"web_search": {"provider": "searxng"}}
    orch.ai_client = type("C", (), {"config": {}})()
    vera = Memoria(tmp_path)
    orch.memory_client = vera
    orch.modalita_esame()
    orch.modalita_esame()
    assert isinstance(orch.memory_client, MemoriaSolaLettura) and orch.memory_client._memoria is vera, \
        "una volta sola, mai una sola-lettura dentro un'altra"
    assert orch.config["web_search"] == {"provider": "searxng", "esame": True}
    assert orch.ai_client.config["web_search"]["esame"] is True and orch.esame is True


def test_niente_web_all_esame():
    class Provider:
        def search(self, *a, **k):
            raise AssertionError("nessuna ricerca in un esame")
    assert web_search._cerca(Provider(), "abc400 a solution", {"web_search": {"esame": True}}) == []

    class Vero:
        def search(self, query, max_results=4):
            return [{"url": "https://example.com", "title": query}]
    assert web_search._cerca(Vero(), "q", {"web_search": {}}) != []


# --- i compiti ---------------------------------------------------------------------------------

def test_i_casi_d_esame():
    compiti = [compito(), compito(id="atcoder-abc300_a", data="2025-01-01T00:00:00"),
               compito(id="atcoder-abc337_e", errata="interattivo"),
               compito(id="leetcode-3999", piattaforma="leetcode", tipo="functional", func_name="maxSum",
                       starter_code="class Solution:\n    def maxSum(self, nums: List[int]) -> int:\n        ")]
    out = lcb.casi(compiti, "2026-08-14")
    assert out["idonei"] == 2 and out["scartati"] == {"prima_della_data": 1, "dopo_la_data": 0, "errata": 1,
                                                      "piattaforma": 0}
    stdin, funz = out["casi"]
    assert stdin["esame"] is True and "gold_tests" not in stdin and stdin["lcb_id"] == "atcoder-abc400_a"
    assert "standard input" in stdin["prompt"] and "solution.py" in stdin["prompt"]
    assert "Solution().maxSum(...)" in funz["prompt"] and "def maxSum" in funz["prompt"]
    assert stdin["title"] == "LiveCodeBench atcoder-abc400_a: Somma"
    assert lcb.casi(compiti, "2026-08-14", piattaforme=["leetcode"])["idonei"] == 1
    assert lcb.casi(compiti, "2024-01-01", al="2025-06-01")["idonei"] == 1


def test_compiti_con_i_test_rifiutati(tmp_path):
    path = tmp_path / "compiti.jsonl"
    path.write_text(json.dumps({**compito(), "test": [{"input": "1", "output": "2"}]}) + "\n")
    with pytest.raises(ValueError, match="cieco"):
        lcb.leggi_compiti(path)
    path.write_text(json.dumps({"id": "x"}) + "\n")
    with pytest.raises(ValueError):
        lcb.leggi_compiti(path)


def test_import(tmp_path, monkeypatch):
    path = tmp_path / "compiti.jsonl"
    path.write_text("".join(json.dumps(c) + "\n" for c in (compito(), compito(id="atcoder-abc1_a",
                                                                                data="2024-01-01T00:00:00"))))
    store = TrainingStore(tmp_path / "t")
    monkeypatch.setattr(training_router, "_training_store_for", lambda project_path="": store)
    monkeypatch.setattr(lcb, "COMPITI", path)
    assert "error" in asyncio.run(training_router.api_training_adapter_livecodebench(_Req({})))
    assert "error" in asyncio.run(training_router.api_training_adapter_livecodebench(
        _Req({"dal": "2026-08-14", "piattaforme": ["topcoder"]})))
    out = asyncio.run(training_router.api_training_adapter_livecodebench(_Req({"dal": "2026-08-14"})))
    assert out["created"] == 1 and out["scartati"]["prima_della_data"] == 1, out
    caso, = store.list_cases()
    assert caso["source"] == "livecodebench" and caso["metadata"]["esame"] is True and lcb.e_esame(caso)


# --- il giro ------------------------------------------------------------------------------------

def test_il_caso_d_esame_nel_giro(monkeypatch, tmp_path):
    visti = {"esame": 0}
    vera = Memoria(tmp_path)

    class Orch:
        def __init__(self, **kw):
            self.memory_client = vera
            self.etichetta_memoria = ""
            self.correzioni_salvate = 0
            self.config = {}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def modalita_esame(self):
            visti["esame"] += 1
            Orchestrator.modalita_esame(self)

        def run_scaffold(self, task, project_path, run_id):
            visti["prompt"] = task
            visti["file"] = sorted(p.name for p in Path(project_path).iterdir())
            (Path(project_path) / "solution.py").write_text("print(sum(map(int, input().split())))\n")
            self.memory_client.store_local("lezione dall'esame")   # come _remember_scaffold_outcome
            self.correzioni_salvate = 2
            return {"success": True, "status": "completed", "files_written": ["solution.py"],
                    "quality_gate": dict(GREEN_GATE)}
    fast_app = _patch_runner_surface(monkeypatch, tmp_path)
    monkeypatch.setattr(fast_app, "Orchestrator", Orch)
    monkeypatch.setattr(training_router, "_verify_gold_tests_executed", _fake_gold_check)
    store = TrainingStore(tmp_path / "t")
    store.seed_cases(lcb.casi([compito()], "2026-08-14")["casi"], source="livecodebench")
    caso = dict(store.list_cases()[0])
    caso["metadata"]["gold_tests"] = {"test_gold_x.py": "def test_x():\n    assert False\n"}
    store.add_attempt(case_id=caso["case_id"], status="auto_failure", error_reason="sbagliato prima",
                      artifacts=[str(tmp_path / "vecchio")])
    job = _register_job("job_e", [caso])
    job["memory_label"] = "palestra"
    training_router._run_training_cases_background("job_e", store, [caso], "livecodebench")
    assert visti["esame"] == 1
    assert "TENTATIVO PRECEDENTE" not in visti["prompt"] and "sbagliato prima" not in visti["prompt"]
    assert "test_gold_x.py" not in visti["file"], "mai un gold in un esame"
    assert vera.scritti == [], "niente in memoria"
    ultimo = store.list_attempts()[-1]
    assert ultimo["status"] == "auto_success" and ultimo["tests"]["esame"] is True
    with training_router._training_jobs_lock:
        fatto = dict(training_router._training_jobs["job_e"])
    assert fatto["lessons_saved"] == 0


def test_un_esame_una_volta_sola(monkeypatch, tmp_path):
    store = TrainingStore(tmp_path / "t")
    store.seed_cases(lcb.casi([compito(), compito(id="atcoder-abc401_a", titolo="Altro"),
                               compito(id="atcoder-abc402_a", titolo="Terzo")], "2026-08-14")["casi"],
                     source="livecodebench")
    casi = {c["metadata"]["lcb_id"]: c["case_id"] for c in store.list_cases()}
    store.add_attempt(case_id=casi["atcoder-abc400_a"], prompt="p", response="r", status="auto_failure")
    store.add_attempt(case_id=casi["atcoder-abc401_a"], prompt="p", response="r", status="runner_error")
    monkeypatch.setattr(training_router, "_training_store_for", lambda project_path="": store)
    partiti = []
    monkeypatch.setattr(training_router.threading, "Thread",
                        lambda target, args, daemon: type("T", (), {"start": lambda self: partiti.append(args)})())
    out = asyncio.run(training_router.api_training_run(_Req({"benchmark_id": "livecodebench", "retry_failed": True})))
    assert "error" in out and partiti == []
    asyncio.run(training_router.api_training_run(_Req({"benchmark_id": "livecodebench"})))
    assert sorted(c["metadata"]["lcb_id"] for c in partiti[0][2]) == ["atcoder-abc401_a", "atcoder-abc402_a"], \
        "gia' tentato = fatto; un guasto del rig si rifa'"


def test_gli_export_non_portano_l_esame(tmp_path):
    store = TrainingStore(tmp_path / "t")
    store.seed_cases(lcb.casi([compito()], "2026-08-14")["casi"], source="livecodebench")
    store.seed_cases([{"title": "MBPP 1: x", "prompt": "fai x"}], source="mbpp")
    for caso in store.list_cases():
        tentativo = store.add_attempt(case_id=caso["case_id"], prompt="p", response="r", status="auto_failure")
        store.add_correction(attempt_id=tentativo["attempt_id"], correction="giusto", corrected_solution="codice")
    pacchetto = Path(store.export_teacher_packet()["path"]).read_text()
    sft = Path(store.export_sft_dataset()["path"]).read_text()
    assert "MBPP 1" in pacchetto and "LiveCodeBench" not in pacchetto
    assert "fai x" in sft and "Read A and B" not in sft
