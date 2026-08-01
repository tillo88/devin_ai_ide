"""Test dell'endpoint Council (`/api/council/review`, `/api/council/status`).

Offline: nessun modello. Verifica che il default sia deterministico (sicuro
anche mentre il rig calibra/benchmarka) e che l'endpoint non promuova nulla.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from devin.core.council import AXES, AXIS_CONSTRAINTS, AXIS_SECURITY, LocalDeterministicReviewer
from devin.training.store import TrainingStore
from devin.ui.routers import council as council_router_mod


@pytest.fixture
def store(tmp_path):
    return TrainingStore(tmp_path / "training")


@pytest.fixture
def client(store, monkeypatch):
    monkeypatch.setattr(council_router_mod, "_store_for", lambda project_path="": store)
    monkeypatch.setattr(council_router_mod, "_load_config", lambda: {})
    app = FastAPI()
    app.include_router(council_router_mod.router)
    return TestClient(app)


def _seed(store, status="auto_success"):
    case = store.add_case(task="scrivi is_prime", title="is_prime",
                          expected_signals=["file_created"])
    attempt = store.add_attempt(
        case_id=case["case_id"], prompt="p", response="r", status=status,
        tests={
            "validators": {"overall": "pass",
                           "signals": {"file_created": {"verdict": "pass", "detail": "ok"}},
                           "machine_checked": 1, "not_machine_checkable": []},
            "quality_gate": {"tests_run": True, "security_scanner": "bandit",
                             "security_warnings": []},
        },
        artifacts=["is_prime.py"],
    )
    return case, attempt


class TestReviewersFromConfig:
    def test_default_e_solo_deterministico(self):
        reviewers = council_router_mod.build_reviewers({})
        assert len(reviewers) == 1
        assert isinstance(reviewers[0], LocalDeterministicReviewer)
        assert set(reviewers[0].supported_axes) == {AXIS_CONSTRAINTS, AXIS_SECURITY}

    def test_nessun_modello_senza_configurazione_esplicita(self):
        # Sicurezza operativa: l'endpoint non deve toccare modelli per default.
        reviewers = council_router_mod.build_reviewers({"council": {}})
        assert all(type(r).__name__ == "LocalDeterministicReviewer" for r in reviewers)

    def test_reviewer_modello_mal_configurato_non_blocca(self):
        reviewers = council_router_mod.build_reviewers({
            "council": {"model_reviewers": [{"reviewer_id": "", "family": "", "axes": []}]}
        })
        assert len(reviewers) == 1   # resta il deterministico

    def test_strict_coverage_configurabile(self):
        r = council_router_mod.build_reviewers({"council": {"strict_coverage": False}})[0]
        assert r.strict_coverage is False


class TestReviewEndpoint:
    def test_review_di_un_singolo_attempt(self, client, store):
        _case, attempt = _seed(store)
        resp = client.post("/api/council/review", json={"attempt_id": attempt["attempt_id"]})
        assert resp.status_code == 200
        body = resp.json()
        assert body["mode"] == "single"
        assert body["council"]["status"] == "pending_review"
        assert body["council"]["council"]["promotable"] is False

    def test_review_della_coda(self, client, store):
        for _ in range(3):
            _seed(store)
        body = client.post("/api/council/review", json={"limit": 10}).json()
        assert body["mode"] == "queue"
        assert body["report"]["reviewed"] == 3

    def test_seconda_passata_non_rigiudica(self, client, store):
        _seed(store)
        client.post("/api/council/review", json={})
        body = client.post("/api/council/review", json={}).json()
        assert body["report"]["reviewed"] == 0
        assert body["report"]["skipped"] == 1

    def test_force_rigiudica(self, client, store):
        _seed(store)
        client.post("/api/council/review", json={})
        body = client.post("/api/council/review", json={"force": True}).json()
        assert body["report"]["reviewed"] == 1

    def test_persist_false_non_scrive(self, client, store):
        _case, attempt = _seed(store)
        client.post("/api/council/review",
                    json={"attempt_id": attempt["attempt_id"], "persist": False})
        assert store.list_reviews(attempt_id=attempt["attempt_id"]) == []

    def test_attempt_sconosciuto(self, client):
        body = client.post("/api/council/review", json={"attempt_id": "non-esiste"}).json()
        assert "error" in body

    def test_assi_sconosciuti_rifiutati(self, client):
        body = client.post("/api/council/review", json={"axes": ["asse_finto"]}).json()
        assert "error" in body
        assert "valid_axes" in body

    def test_budget_applicato(self, client, store):
        _seed(store)
        body = client.post("/api/council/review", json={"max_reviews": 1}).json()
        assert body["report"]["reviewed"] == 1

    def test_subset_di_assi(self, client, store):
        _case, attempt = _seed(store)
        body = client.post("/api/council/review", json={
            "attempt_id": attempt["attempt_id"], "axes": [AXIS_CONSTRAINTS],
        }).json()
        results = body["council"]["council"]["axis_results"]
        assert [r["axis"] for r in results] == [AXIS_CONSTRAINTS]


class TestStatusEndpoint:
    def test_status_vuoto(self, client):
        body = client.get("/api/council/status").json()
        assert body["queue_total"] == 0
        assert body["pending_for_council"] == 0

    def test_status_dichiara_gli_assi_scoperti(self, client, store):
        _seed(store)
        body = client.get("/api/council/status").json()
        assert body["queue_total"] == 1
        assert body["pending_for_council"] == 1
        # col solo deterministico, 3 assi su 5 restano scoperti: va detto.
        assert set(body["axes_covered"]) == {AXIS_CONSTRAINTS, AXIS_SECURITY}
        assert len(body["axes_uncovered"]) == len(AXES) - 2

    def test_status_dopo_la_review(self, client, store):
        _seed(store)
        client.post("/api/council/review", json={})
        body = client.get("/api/council/status").json()
        assert body["already_reviewed"] == 1
        assert body["pending_for_council"] == 0
