"""Palestra (23/10): giri di training con tetti di tempo/casi, stop, etichetta
delle lezioni salvate in memoria e risultati per caso."""
import asyncio
import json
import time
from pathlib import Path

import devin.ui.routers.training as training_router
from devin.training.store import TrainingStore
from test_training_runner_declass import (_isolate_training_jobs, _patch_runner_surface, _register_job,  # noqa: F401
                                          _fake_gold_check, _runner_case, GREEN_GATE)


class _Req:
    def __init__(self, data):
        self._data = data

    async def json(self):
        return self._data


def _orch_che_impara(etichette, lezioni=1, attesa=0.0):
    class Orch:
        def __init__(self, **kwargs):
            self.etichetta_memoria = ""
            self.correzioni_salvate = 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def run_scaffold(self, task, project_path, run_id):
            etichette.append(self.etichetta_memoria)
            (Path(project_path) / "sol.py").write_text("def add(a, b):\n    return a + b\n")
            self.correzioni_salvate = lezioni
            time.sleep(attesa)
            return {"success": True, "quality_gate": dict(GREEN_GATE), "files_written": ["sol.py"]}
    return Orch


def _casi(n):
    return [_runner_case(case_id=f"c{i}", title=f"Caso {i}") for i in range(n)]


def _giro(monkeypatch, tmp_path, casi, job_extra, lezioni=1, attesa=0.0):
    fast_app = _patch_runner_surface(monkeypatch, tmp_path)
    etichette = []
    monkeypatch.setattr(fast_app, "Orchestrator", _orch_che_impara(etichette, lezioni, attesa))
    monkeypatch.setattr(training_router, "_verify_gold_tests_executed", _fake_gold_check)
    job = _register_job("giro", casi)
    with training_router._training_jobs_lock:
        job.update({"memory_label": "", "deadline": None, "stop_requested": False, "stopped": "",
                    "lessons_saved": 0, "results": [], **job_extra})
    training_router._run_training_cases_background("giro", TrainingStore(tmp_path / "t"), casi, "bench-test")
    with training_router._training_jobs_lock:
        return dict(training_router._training_jobs["giro"]), etichette


def test_le_lezioni_sono_etichettate_contate_e_ogni_caso_ha_il_suo_risultato(monkeypatch, tmp_path):
    job, etichette = _giro(monkeypatch, tmp_path, _casi(3), {"memory_label": "palestra"}, lezioni=2)
    assert etichette == ["palestra"] * 3
    assert job["lessons_saved"] == 6 and job["status"] == "finished" and job["stopped"] == ""
    assert [(r["case_id"], r["status"], r["lessons"]) for r in job["results"]] == [
        ("c0", "auto_success", 2), ("c1", "auto_success", 2), ("c2", "auto_success", 2)]
    assert all(isinstance(r["seconds"], int) for r in job["results"])


def test_scaduto_il_tempo_non_parte_un_caso_nuovo(monkeypatch, tmp_path):
    job, etichette = _giro(monkeypatch, tmp_path, _casi(3), {"deadline": time.time() - 1})
    assert etichette == [] and job["stopped"] == "tempo" and job["results"] == []


def test_lo_stop_ferma_dopo_il_caso_in_corso(monkeypatch, tmp_path):
    fast_app = _patch_runner_surface(monkeypatch, tmp_path)
    fatti = []

    class Orch:
        def __init__(self, **kwargs):
            self.etichetta_memoria, self.correzioni_salvate = "", 0

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def run_scaffold(self, task, project_path, run_id):
            fatti.append(run_id)
            asyncio.run(training_router.api_training_stop(_Req({"job_id": "giro"})))
            return {"success": True, "quality_gate": dict(GREEN_GATE), "files_written": ["sol.py"]}

    monkeypatch.setattr(fast_app, "Orchestrator", Orch)
    monkeypatch.setattr(training_router, "_verify_gold_tests_executed", _fake_gold_check)
    casi = _casi(3)
    job = _register_job("giro", casi)
    job.update({"deadline": None, "stop_requested": False, "stopped": "", "lessons_saved": 0, "results": []})
    job["status"] = "running"
    training_router._run_training_cases_background("giro", TrainingStore(tmp_path / "t"), casi, "bench-test")
    with training_router._training_jobs_lock:
        finale = dict(training_router._training_jobs["giro"])
    assert len(fatti) == 1 and finale["stopped"] == "stop" and len(finale["results"]) == 1
    assert asyncio.run(training_router.api_training_stop(_Req({"job_id": "nessuno"}))) == {"error": "job sconosciuto"}


def test_il_run_valida_tetti_ed_etichetta(monkeypatch, tmp_path):
    store = TrainingStore(tmp_path / "t")
    monkeypatch.setattr(training_router, "_training_store_for", lambda project_path="": store)
    partiti = []
    monkeypatch.setattr(training_router.threading, "Thread",
                        lambda target, args, daemon: type("T", (), {"start": lambda self: partiti.append(args)})())
    out = asyncio.run(training_router.api_training_run(_Req({"benchmark_id": "devin-mini", "max_cases": 2,
                                                             "max_minutes": 90, "memory_label": "palestra"})))
    job = out["job"]
    assert job["total"] == 2 and job["memory_label"] == "palestra" and job["deadline"] > time.time() + 80 * 60
    assert len(partiti[0][2]) == 2
    for bad in ({"max_minutes": 1000}, {"max_cases": -1}, {"memory_label": "pal estra!"}, {"max_cases": "x"}):
        risposta = asyncio.run(training_router.api_training_run(_Req({"benchmark_id": "devin-mini", **bad})))
        assert "error" in risposta, bad


def test_l_orchestratore_etichetta_e_conta_le_correzioni(tmp_path):
    """Le correzioni salvate durante la palestra portano origin:palestra:<origine> e si contano;
    un duplicato non conta. Senza etichetta l'origine resta quella di sempre."""
    from types import SimpleNamespace
    from devin.ai.hybrid_memory_client import HybridMemoryClient
    from devin.core.orchestrator import Orchestrator
    from test_fix_memory import TRACEBACK

    memoria = HybridMemoryClient({"local_memory": {"path": str(tmp_path / "mem.jsonl")},
                                  "understory": {"enabled": False}, "automem": {"enabled": False}})
    finto = SimpleNamespace(memory_client=memoria, project_path=str(tmp_path / "mbpp_1"),
                            _project_language=lambda: "python", _log=lambda *a, **k: None,
                            etichetta_memoria="palestra", correzioni_salvate=0)
    Orchestrator._ricorda_correzione(finto, TRACEBACK, "--- a\n+++ b\n+fix\n", "agent_round")
    Orchestrator._ricorda_correzione(finto, TRACEBACK, "--- a\n+++ b\n+fix\n", "agent_round")
    assert finto.correzioni_salvate == 1
    righe = [json.loads(r) for r in (tmp_path / "mem.jsonl").read_text().splitlines()]
    assert any("origin:palestra:agent_round" in r.get("tags", []) for r in righe), righe
    normale = SimpleNamespace(**{**vars(finto), "etichetta_memoria": "", "correzioni_salvate": 0})
    Orchestrator._ricorda_correzione(normale, TRACEBACK.replace("FileNotFoundError", "KeyError"),
                                     "--- a\n+++ b\n+altro\n", "agent_round")
    righe = [json.loads(r) for r in (tmp_path / "mem.jsonl").read_text().splitlines()]
    assert any("origin:agent_round" in r.get("tags", []) for r in righe)


def test_ripasso_dei_falliti_con_tetto_ai_tentativi(monkeypatch, tmp_path):
    """retry_failed: solo i casi con l'ultimo tentativo auto_failure e meno di max_attempts tentativi;
    un runner_error non conta come tentativo."""
    store = TrainingStore(tmp_path / "t")
    store.seed_cases([{"title": f"Caso {i}", "prompt": f"compito {i}"} for i in range(4)], source="mbpp")
    casi = {c["title"]: c["case_id"] for c in store.list_cases()}
    for titolo, esiti in {"Caso 0": ["auto_failure"], "Caso 1": ["auto_success"],
                          "Caso 2": ["auto_failure", "auto_failure"], "Caso 3": ["runner_error", "auto_failure"]}.items():
        for esito in esiti:
            store.add_attempt(case_id=casi[titolo], prompt="p", response="r", status=esito)
    monkeypatch.setattr(training_router, "_training_store_for", lambda project_path="": store)
    partiti = []
    monkeypatch.setattr(training_router.threading, "Thread",
                        lambda target, args, daemon: type("T", (), {"start": lambda self: partiti.append(args)})())
    out = asyncio.run(training_router.api_training_run(_Req({"benchmark_id": "mbpp", "retry_failed": True})))
    assert sorted(c["title"] for c in partiti[0][2]) == ["Caso 0", "Caso 3"], out
    out = asyncio.run(training_router.api_training_run(_Req({"benchmark_id": "mbpp", "retry_failed": True,
                                                             "max_attempts": 3})))
    assert sorted(c["title"] for c in partiti[1][2]) == ["Caso 0", "Caso 2", "Caso 3"]


def test_il_training_applica_il_verificato_e_dice_il_perche_dei_fallimenti(monkeypatch, tmp_path):
    """#1842: 0/10 con "training scaffold failed": in review lo scaffold verificato restava in attesa
    di approvazione (success=False). Il runner ora chiede l'applicazione automatica del verificato e,
    quando fallisce, riporta stato e primi errori del gate."""
    fast_app = _patch_runner_surface(monkeypatch, tmp_path)
    visti = []

    class Orch:
        def __init__(self, **kwargs):
            self.etichetta_memoria, self.correzioni_salvate, self.auto_apply_verified = "", 0, False

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def run_scaffold(self, task, project_path, run_id):
            visti.append(self.auto_apply_verified)
            return {"success": False, "status": "failed", "files_failed": ["a.py"],
                    "quality_gate": {"status": "verified_failure", "errors": ["test_gold_mbpp_1 FAILED: 3 != 4"]}}

    monkeypatch.setattr(fast_app, "Orchestrator", Orch)
    monkeypatch.setattr(training_router, "_verify_gold_tests_executed", _fake_gold_check)
    casi = _casi(1)
    job = _register_job("giro", casi)
    job.update({"deadline": None, "stop_requested": False, "stopped": "", "lessons_saved": 0, "results": []})
    training_router._run_training_cases_background("giro", TrainingStore(tmp_path / "t"), casi, "bench-test")
    with training_router._training_jobs_lock:
        finale = dict(training_router._training_jobs["giro"])
    assert visti == [True]
    assert finale["results"][0]["reason"] == "scaffold failed: test_gold_mbpp_1 FAILED: 3 != 4 (file falliti: 1)"


# ---------------------------------------------------------------------------
# 28/10: 105 titoli MBPP che, tagliati a 70 caratteri, finivano con uno spazio
# ---------------------------------------------------------------------------

def _riga_mbpp(task_id, testo):
    return {"task_id": task_id, "text": testo, "test_list": [f"assert f{task_id}(1) == 1"], "test_setup_code": ""}


def _store_con_doppioni(tmp_path):
    """Lo store come sul rig: il vecchio seed creava un doppione a ogni import per i titoli con lo spazio in coda."""
    from devin.training.adapters import mbpp_rows_to_cases
    store = TrainingStore(tmp_path / "t")
    righe = [_riga_mbpp(18, "Write a function to remove characters from the first string which are present in the second"),
             _riga_mbpp(7, "Find words")]
    casi = mbpp_rows_to_cases(righe, limit=10)
    vecchio = dict(casi[0], title=casi[0]["title"] + " ")       # il titolo di prima del fix, con lo spazio
    for _ in range(3):                                            # tre import col codice vecchio = tre copie
        store.add_case(task=vecchio["prompt"], title=vecchio["title"], source="mbpp")
    store.add_case(task=casi[1]["prompt"], title=casi[1]["title"], source="mbpp")
    return store, righe


def test_il_reseed_non_crea_doppioni_e_ritira_quelli_vecchi(tmp_path):
    from devin.training.adapters import mbpp_rows_to_cases
    store, righe = _store_con_doppioni(tmp_path)
    primo = [c["case_id"] for c in store.list_cases() if c["title"].startswith("MBPP 18")][0]
    assert mbpp_rows_to_cases(righe, limit=10)[0]["title"].endswith("second") is False
    assert not mbpp_rows_to_cases(righe, limit=10)[0]["title"].endswith(" "), "titolo senza spazio in coda"
    for _ in range(3):
        assert store.seed_cases(mbpp_rows_to_cases(righe, limit=10), source="mbpp") == []
    attivi = [c for c in store.list_cases() if c["source"] == "mbpp"]
    assert sorted(c["title"][:7] for c in attivi) == ["MBPP 18", "MBPP 7:"], "una copia per esercizio"
    assert [c["case_id"] for c in attivi if c["title"].startswith("MBPP 18")] == [primo], "resta il piu' vecchio"
    assert store.last_seed_retired_duplicates == 0, "il ritiro avviene una volta sola"
    # chiavi normalizzate anche per chi passa titoli o compiti con spazi attorno
    for _ in range(2):
        store.seed_cases([{"title": "Caso libero ", "prompt": " compito libero\n"}], source="mbpp")
    assert sum(c["title"] == "Caso libero" for c in store.list_cases()) == 1


def test_un_esercizio_fatto_sotto_un_doppione_e_fatto(monkeypatch, tmp_path):
    """skip_attempted e ripasso guardano l'esercizio (fonte + titolo), non il case_id."""
    store, _ = _store_con_doppioni(tmp_path)
    copie = [c["case_id"] for c in store.list_cases() if c["title"].startswith("MBPP 18")]
    store.add_attempt(case_id=copie[0], prompt="p", response="r", status="auto_success")
    monkeypatch.setattr(training_router, "_training_store_for", lambda project_path="": store)
    partiti = []
    monkeypatch.setattr(training_router.threading, "Thread",
                        lambda target, args, daemon: type("T", (), {"start": lambda self: partiti.append(args)})())
    asyncio.run(training_router.api_training_run(_Req({"benchmark_id": "mbpp", "skip_attempted": True})))
    assert [c["title"][:7] for c in partiti[0][2]] == ["MBPP 7:"], "MBPP 18 e' gia' fatto sotto un'altra copia"
    store.add_attempt(case_id=copie[1], prompt="p", response="r", status="auto_failure")
    store.add_attempt(case_id=copie[2], prompt="p", response="r", status="auto_failure")
    out = asyncio.run(training_router.api_training_run(_Req({"benchmark_id": "mbpp", "retry_failed": True})))
    assert "error" in out, "due fallimenti su copie diverse = due tentativi: oltre il tetto di 2"
    out = asyncio.run(training_router.api_training_run(_Req({"benchmark_id": "mbpp", "retry_failed": True,
                                                             "max_attempts": 4})))
    assert [c["title"][:7] for c in partiti[-1][2]] == ["MBPP 18"], out


def test_la_prova_dell_app_non_inventa_casi_d_errore():
    """28/10: 26 dei 47 non risolti erano la prova dell'app che pretendeva ValueError/TypeError su
    input che il compito non nomina. Ora solo comportamenti chiesti dal compito, prima i suoi esempi."""
    from devin.core.app_check import richiesta_piano
    testo = richiesta_piano("Scrivi f(n)", ["solution.py"], {}, {"python": True})
    assert "compreso un errore di input" not in testo
    assert "Niente casi d'errore inventati" in testo and "test di riferimento del compito" in testo


def test_il_ripasso_dice_a_devin_che_cosa_era_andato_storto(tmp_path):
    """28/10: il ripasso non e' piu' un nuovo tiro di dadi: il compito porta il motivo e l'uscita
    dei test ufficiali dell'ultimo fallimento, anche se registrato sotto un'altra copia del caso."""
    store, _ = _store_con_doppioni(tmp_path)
    copie = [c for c in store.list_cases() if c["title"].startswith("MBPP 18")]
    assert training_router._tentativo_precedente(store, copie[0]) == "", "mai provato: niente sezione"
    store.add_attempt(case_id=copie[1]["case_id"], prompt="p", response="r", status="runner_error", error_reason="rig")
    assert training_router._tentativo_precedente(store, copie[0]) == "", "un guasto del rig non e' un tentativo"
    store.add_attempt(case_id=copie[1]["case_id"], prompt="p", response="r", status="auto_failure",
                      error_reason="gold test non eseguiti: non raccolti/passati: test_gold_mbpp_18.py",
                      tests={"gold_executed": {"exit_code": 1, "missing": ["t"], "output": "E AssertionError: 'bacuve'"}})
    testo = training_router._tentativo_precedente(store, copie[0])
    assert "TENTATIVO PRECEDENTE FALLITO (1 finora)" in testo and "AssertionError: 'bacuve'" in testo
    store.add_attempt(case_id=copie[2]["case_id"], prompt="p", response="r", status="auto_failure",
                      error_reason="gold test non eseguiti: non raccolti/passati: t.py",
                      tests={"gold_executed": {"exit_code": 0, "missing": ["t"], "output": "1 passed"}})
    assert training_router._tentativo_precedente(store, copie[0]) == "", "falso negativo del 23/10: niente"
    store.add_attempt(case_id=copie[0]["case_id"], prompt="p", response="r", status="auto_success")
    assert training_router._tentativo_precedente(store, copie[0]) == "", "ultimo riuscito: niente"


def test_il_runner_passa_il_tentativo_precedente_a_devin(monkeypatch, tmp_path):
    compiti = []

    class Orch:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def run_scaffold(self, task, project_path, run_id):
            compiti.append(task)
            return {"success": True, "quality_gate": dict(GREEN_GATE), "files_written": ["sol.py"]}

    fast_app = _patch_runner_surface(monkeypatch, tmp_path)
    monkeypatch.setattr(fast_app, "Orchestrator", Orch)
    monkeypatch.setattr(training_router, "_verify_gold_tests_executed", _fake_gold_check)
    store = TrainingStore(tmp_path / "training")
    case = _runner_case()
    store.add_attempt(case_id=case["case_id"], prompt="p", response="r", status="auto_failure",
                      error_reason="quality gate: test_sol.py::test_add FAILED")
    _register_job("job_prec", [case])
    training_router._run_training_cases_background("job_prec", store, [case], "bench-test")
    assert "TENTATIVO PRECEDENTE FALLITO" in compiti[0] and "test_add FAILED" in compiti[0]
