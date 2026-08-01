"""Test del ponte Council <-> training store.

Usa lo store REALE su directory temporanea (nessun modello, nessuna rete).
Protegge: evidenza registrata (non ri-derivata), pacchetto cieco, nessuna
promozione automatica, nessuna ri-review infinita.
"""

from __future__ import annotations

import pytest

from devin.core.council import (
    AXES,
    AXIS_CONSTRAINTS,
    AXIS_SECURITY,
    VERDICT_PASS,
    LocalDeterministicReviewer,
    ReviewerAdapter,
    ReviewVerdict,
)
from devin.core.council_aggregator import OUTCOME_PASS_CANDIDATE
from devin.core.council_store import (
    COUNCIL_REVIEWER_ID,
    already_reviewed_by_council,
    packet_from_attempt,
    review_attempt,
    review_queue,
)
from devin.training.store import TrainingStore


class AllPass(ReviewerAdapter):
    reviewer_id, family, supported_axes = "stub", "stub-fam", AXES

    def review(self, packet, axis):
        return ReviewVerdict(axis=axis, verdict=VERDICT_PASS, reviewer_id=self.reviewer_id,
                             family=self.family, reasoning="ok")


@pytest.fixture
def store(tmp_path):
    return TrainingStore(tmp_path / "training")


def _seed(store, *, status="auto_success", validators=None, gate=None, signals=None):
    case = store.add_case(task="scrivi is_prime", title="is_prime",
                          expected_signals=signals or ["file_created"])
    attempt = store.add_attempt(
        case_id=case["case_id"],
        prompt="scrivi is_prime",
        response="def is_prime(n): ...",
        status=status,
        tests={
            "validators": validators if validators is not None else {
                "overall": "pass",
                "signals": {"file_created": {"verdict": "pass", "detail": "1 file"}},
                "machine_checked": 1,
                "not_machine_checkable": [],
            },
            "quality_gate": gate if gate is not None else {
                "status": "verified_success", "tests_run": True,
                "security_scanner": "bandit", "security_warnings": [],
            },
        },
        artifacts=["is_prime.py"],
    )
    return case, attempt


class TestPacketFromAttempt:
    def test_porta_l_evidenza_registrata(self, store):
        case, attempt = _seed(store)
        packet = packet_from_attempt(attempt, case)
        assert packet.packet_id == attempt["attempt_id"]
        assert packet.validation["overall"] == "pass"
        assert packet.quality_gate["security_scanner"] == "bandit"

    def test_e_cieco_rispetto_alle_review_precedenti(self, store):
        case, attempt = _seed(store)
        store.add_review(attempt["attempt_id"], "verified_failure",
                         rationale="giudizio precedente", reviewer="human")
        packet = packet_from_attempt(attempt, case)
        blob = repr(packet)
        assert "giudizio precedente" not in blob
        assert "verified_failure" not in blob

    def test_attempt_senza_id_solleva(self):
        with pytest.raises(ValueError):
            packet_from_attempt({"case_id": "c"}, {})


class TestRecordedEvidenceWins:
    def test_usa_i_validators_registrati_senza_rieseguire(self, store):
        case, attempt = _seed(store)

        def boom(*a, **k):
            raise AssertionError("non deve rieseguire: c'e' l'evidenza registrata")

        reviewer = LocalDeterministicReviewer(validate_case=boom)
        packet = packet_from_attempt(attempt, case)
        v = reviewer.review(packet, AXIS_CONSTRAINTS)
        assert v.verdict == VERDICT_PASS

    def test_scanner_assente_al_run_non_e_pulito(self, store):
        case, attempt = _seed(store, gate={"security_scanner": "assente", "security_warnings": []})
        v = LocalDeterministicReviewer().review(packet_from_attempt(attempt, case), AXIS_SECURITY)
        assert v.verdict == "needs_evidence"
        assert v.evidence["recorded"] is True

    def test_finding_registrati_escalano(self, store):
        case, attempt = _seed(store, gate={
            "security_scanner": "bandit",
            "security_warnings": ["main.py: [B602 HIGH] shell=True (riga 3)"],
        })
        v = LocalDeterministicReviewer().review(packet_from_attempt(attempt, case), AXIS_SECURITY)
        assert v.verdict == "needs_evidence"
        assert v.violations


class TestPersistence:
    def test_scrive_una_review_col_marchio_del_council(self, store):
        case, attempt = _seed(store)
        run = review_attempt(store, attempt, [AllPass()], case=case)
        reviews = store.list_reviews(attempt_id=attempt["attempt_id"])
        assert len(reviews) == 1
        r = reviews[0]
        assert r["reviewer"] == COUNCIL_REVIEWER_ID
        assert r["status"] == "pending_review"
        assert "council" in r["tags"]
        assert r["evidence"]["outcome"] == OUTCOME_PASS_CANDIDATE
        assert run.outcome.outcome == OUTCOME_PASS_CANDIDATE

    def test_niente_promozione_automatica_nemmeno_a_livello_store(self, store):
        # Lo store marca 'eligible' solo per gli status safe-success: il Council
        # non li emette mai, quindi la promozione resta manuale per costruzione.
        case, attempt = _seed(store)
        review_attempt(store, attempt, [AllPass()], case=case)
        assert store.list_reviews(attempt_id=attempt["attempt_id"])[0]["promotion"] == "manual_required"

    def test_persist_false_non_scrive(self, store):
        case, attempt = _seed(store)
        review_attempt(store, attempt, [AllPass()], case=case, persist=False)
        assert store.list_reviews(attempt_id=attempt["attempt_id"]) == []

    def test_la_review_porta_la_provenance(self, store):
        case, attempt = _seed(store)
        review_attempt(store, attempt, [AllPass()], case=case)
        ev = store.list_reviews(attempt_id=attempt["attempt_id"])[0]["evidence"]
        assert "plan" in ev and "dispatch" in ev and "axis_results" in ev
        assert ev["plan"]["complete"] is True


class TestBatch:
    def test_giudica_la_coda(self, store):
        for _ in range(3):
            _seed(store)
        report = review_queue(store, [AllPass()])
        assert len(report.reviewed) == 3
        assert report.counts[OUTCOME_PASS_CANDIDATE] == 3

    def test_non_rigiudica_all_infinito(self, store):
        # pending_review NON chiude la coda dello store: senza guardia, il
        # secondo giro rigiudicherebbe gli stessi attempt per sempre.
        _seed(store)
        first = review_queue(store, [AllPass()])
        second = review_queue(store, [AllPass()])
        assert len(first.reviewed) == 1
        assert len(second.reviewed) == 0
        assert second.skipped[0]["reason"] == "gia' giudicato dal Council"

    def test_force_rigiudica(self, store):
        _seed(store)
        review_queue(store, [AllPass()])
        again = review_queue(store, [AllPass()], force=True)
        assert len(again.reviewed) == 1

    def test_un_attempt_rotto_non_ferma_il_batch(self, store, monkeypatch):
        _seed(store)
        _seed(store)

        calls = {"n": 0}
        import devin.core.council_store as mod
        original = mod.run_council

        def flaky(packet, reviewers, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("packet corrotto")
            return original(packet, reviewers, **kw)

        monkeypatch.setattr(mod, "run_council", flaky)
        report = review_queue(store, [AllPass()])
        assert len(report.errors) == 1
        assert len(report.reviewed) == 1

    def test_report_serializzabile(self, store):
        _seed(store)
        d = review_queue(store, [AllPass()]).to_dict()
        assert d["reviewed"] == 1 and d["errors"] == 0


class TestRealReviewerEndToEnd:
    def test_col_reviewer_deterministico_reale(self, store):
        # Solo 2 assi coperti su 5 -> il Council non promuove.
        case, attempt = _seed(store)
        run = review_attempt(store, attempt, [LocalDeterministicReviewer()], case=case)
        assert run.outcome.promotable is False
        assert store.list_reviews(attempt_id=attempt["attempt_id"])[0]["status"] == "pending_review"
