"""Training (28/10): sul web solo documentazione ufficiale, niente soluzioni degli esercizi."""
import pytest

import devin.ai.web_search as ws

RISULTATI = [
    {"title": "MBPP 18 solution", "snippet": "def remove_dirty_chars ...", "url": "https://www.geeksforgeeks.org/x"},
    {"title": "Stack Overflow", "snippet": "risposta", "url": "https://stackoverflow.com/q/1"},
    {"title": "mbpp solutions", "snippet": "tutte", "url": "https://github.com/someone/mbpp-solutions"},
    {"title": "str.translate", "snippet": "Return a copy", "url": "https://docs.python.org/3/library/stdtypes.html"},
]


@pytest.mark.parametrize("url,doc", [
    ("https://docs.python.org/3/library/re.html", True),
    ("https://build123d.readthedocs.io/en/latest/", True),
    ("https://docs.github.com/en/actions", True),
    ("https://docs.pytest.org/en/stable/", True),
    ("https://github.com/someone/mbpp-solutions", False),
    ("https://stackoverflow.com/questions/1", False),
    ("https://docs.python.org.evil.com/x", False),
    ("https://notdocs.python.org/x", False),
    ("not a url", False),
])
def test_quali_siti_sono_documentazione(url, doc) -> None:
    assert ws.e_documentazione(url) is doc


class Provider:
    def __init__(self, primo, secondo=()):
        self.primo, self.secondo, self.query = list(primo), list(secondo), []

    def search(self, query, max_results=4):
        self.query.append(query)
        return self.primo if len(self.query) == 1 else self.secondo


def test_con_solo_documentazione_restano_solo_le_doc(monkeypatch) -> None:
    provider = Provider(RISULTATI)
    monkeypatch.setattr(ws, "get_web_search_provider", lambda config: provider)
    monkeypatch.setattr(ws, "fetch_top_results", lambda results, **kw: "")
    monkeypatch.setattr(ws, "fetch_page_smart", lambda url, **kw: "")
    tutto = ws.search_coding_context("remove dirty chars python", {})
    assert "geeksforgeeks" in tutto and "stackoverflow" in tutto, "lavoro normale: invariato"
    config = {"web_search": {"solo_documentazione": True}}
    for funzione in (ws.search_coding_context, ws.search_docs_context):
        provider.query.clear()
        blocco = funzione("remove dirty chars python", config)
        assert "docs.python.org" in blocco
        assert not any(x in blocco for x in ("geeksforgeeks", "stackoverflow", "mbpp-solutions"))


def test_secondo_tentativo_e_niente_se_non_ci_sono_doc(monkeypatch) -> None:
    config = {"web_search": {"solo_documentazione": True}}
    monkeypatch.setattr(ws, "fetch_top_results", lambda results, **kw: "")
    provider = Provider(RISULTATI[:3], RISULTATI[3:])
    monkeypatch.setattr(ws, "get_web_search_provider", lambda c: provider)
    assert "docs.python.org" in ws.search_coding_context("translate", config)
    assert provider.query == ["translate", "translate documentation"]
    provider = Provider(RISULTATI[:3], RISULTATI[:3])
    monkeypatch.setattr(ws, "get_web_search_provider", lambda c: provider)
    assert ws.search_coding_context("translate", config) == "", "nessuna doc: niente, non le soluzioni"


def test_l_orchestratore_si_limita_e_il_training_lo_chiede(monkeypatch, tmp_path) -> None:
    from types import SimpleNamespace
    from devin.core.orchestrator import Orchestrator
    finto = SimpleNamespace(config={}, ai_client=SimpleNamespace(config={"web_search": {"provider": "x"}}))
    Orchestrator.limita_web_alla_documentazione(finto)
    assert ws.solo_documentazione(finto.config) and ws.solo_documentazione(finto.ai_client.config)
    assert finto.ai_client.config["web_search"]["provider"] == "x"

    import devin.ui.routers.training as training_router
    from devin.training.store import TrainingStore
    from test_training_runner_declass import (_patch_runner_surface, _register_job, _fake_gold_check,  # noqa: F401
                                              _runner_case, GREEN_GATE, _isolate_training_jobs)
    limitati = []

    class Orch:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def limita_web_alla_documentazione(self):
            limitati.append(True)

        def run_scaffold(self, task, project_path, run_id):
            assert limitati, "il limite va messo PRIMA dello scaffold"
            return {"success": True, "quality_gate": dict(GREEN_GATE), "files_written": ["sol.py"]}

    fast_app = _patch_runner_surface(monkeypatch, tmp_path)
    monkeypatch.setattr(fast_app, "Orchestrator", Orch)
    monkeypatch.setattr(training_router, "_verify_gold_tests_executed", _fake_gold_check)
    case = _runner_case()
    _register_job("job_web", [case])
    training_router._run_training_cases_background("job_web", TrainingStore(tmp_path / "t"), [case], "b")
    assert limitati == [True]
