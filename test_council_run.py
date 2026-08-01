"""Test dell'orchestrazione end-to-end del Council.

Verifica che le cinque fasi collegate mantengano le garanzie: bounded, nessuna
promozione automatica, degrado che non blocca, arbitrato guidato dall'evidenza.
"""

from __future__ import annotations

from devin.core.council import (
    AXES,
    AXIS_CONCEPT,
    AXIS_SECURITY,
    VERDICT_FAIL,
    VERDICT_NEEDS_EVIDENCE,
    VERDICT_PASS,
    ReviewerAdapter,
    ReviewPacket,
    ReviewVerdict,
)
from devin.core.council_aggregator import (
    OUTCOME_FAILURE,
    OUTCOME_NEEDS_ARBITRATION,
    OUTCOME_NEEDS_HUMAN,
    OUTCOME_PASS_CANDIDATE,
)
from devin.core.council_arbiter import (
    RESULT_FAILED,
    RESULT_PASSED,
    Arbiter,
    Experiment,
    ExperimentResult,
)
from devin.core.council_budget import Budget
from devin.core.council_run import run_council


class Fixed(ReviewerAdapter):
    """Reviewer che restituisce sempre lo stesso verdetto."""

    def __init__(self, rid, family, axes, verdict=VERDICT_PASS, reasoning="ok"):
        self.reviewer_id, self.family, self.supported_axes = rid, family, tuple(axes)
        self._verdict, self._reasoning = verdict, reasoning

    def review(self, packet, axis):
        return ReviewVerdict(axis=axis, verdict=self._verdict, reviewer_id=self.reviewer_id,
                             family=self.family, reasoning=self._reasoning)


class Broken(ReviewerAdapter):
    def __init__(self, rid, family, axes):
        self.reviewer_id, self.family, self.supported_axes = rid, family, tuple(axes)

    def review(self, packet, axis):
        raise RuntimeError("giu'")


def _packet():
    return ReviewPacket(packet_id="a1", case={"case_id": "c"}, attempt={"attempt_id": "a1"})


def _omni(verdict=VERDICT_PASS, rid="omni", family="f1"):
    return Fixed(rid, family, AXES, verdict)


def _arbiter(result_status, on_pass=VERDICT_PASS, on_fail=VERDICT_FAIL):
    class Gen:
        def generate(self, axis_result, packet):
            return Experiment(kind="unit", spec="prova discriminante",
                              on_pass=on_pass, on_fail=on_fail)

    class Run:
        def run(self, experiment, packet):
            return ExperimentResult(result_status, "eseguito nel gate")

    return Arbiter(Gen(), Run(), arbiter_id="glm-colibri")


class TestHappyPath:
    def test_tutti_pass_resta_un_candidato(self):
        run = run_council(_packet(), [_omni()])
        assert run.outcome.outcome == OUTCOME_PASS_CANDIDATE
        assert run.outcome.promotable is False
        assert run.arbitrated is False
        assert len(run.verdicts) == len(AXES)

    def test_payload_per_lo_store_non_promuove(self):
        payload = run_council(_packet(), [_omni()]).to_review_payload()
        assert payload["attempt_id"] == "a1"
        assert payload["status"] == "pending_review"      # mai verified_success
        assert payload["council"]["promotable"] is False
        assert payload["council"]["plan"]["complete"] is True


class TestFailure:
    def test_un_fail_conclusivo_boccia(self):
        run = run_council(_packet(), [_omni(verdict=VERDICT_FAIL)])
        assert run.outcome.outcome == OUTCOME_FAILURE
        assert run.to_review_payload()["status"] == "verified_failure"


class TestDegradation:
    def test_reviewer_rotto_degrada_e_non_promuove(self):
        reviewers = [
            Broken("rotto", "f-rotto", [AXIS_SECURITY]),
            Fixed("ok", "f-ok", [a for a in AXES if a != AXIS_SECURITY]),
        ]
        run = run_council(_packet(), reviewers)
        assert run.outcome.outcome == OUTCOME_NEEDS_HUMAN
        assert run.dispatch.degraded is True

    def test_budget_stretto_taglia_la_copertura(self):
        run = run_council(_packet(), [_omni()], budget=Budget(max_reviews=2))
        assert run.outcome.outcome == OUTCOME_NEEDS_HUMAN
        assert run.dispatch.reviews_done == 2


class TestArbitration:
    def _discordant_reviewers(self):
        # due famiglie diverse sull'asse concetto, in disaccordo
        return [
            Fixed("a", "fam-a", [AXIS_CONCEPT], VERDICT_PASS, "sembra giusto"),
            Fixed("b", "fam-b", [AXIS_CONCEPT], VERDICT_FAIL, "conteggio errato"),
        ]

    def test_senza_arbitro_resta_da_arbitrare(self):
        run = run_council(_packet(), self._discordant_reviewers(),
                          axes=[AXIS_CONCEPT], critical=True)
        assert run.outcome.outcome == OUTCOME_NEEDS_ARBITRATION
        assert run.arbitrated is False

    def test_arbitro_risolve_con_l_esperimento(self):
        run = run_council(_packet(), self._discordant_reviewers(), axes=[AXIS_CONCEPT],
                          critical=True, arbiter=_arbiter(RESULT_PASSED))
        assert run.arbitrated is True
        assert run.outcome.outcome == OUTCOME_PASS_CANDIDATE   # risolto, ma non promosso
        assert run.preliminary_outcome.outcome == OUTCOME_NEEDS_ARBITRATION

    def test_esperimento_fallito_porta_a_failure(self):
        run = run_council(_packet(), self._discordant_reviewers(), axes=[AXIS_CONCEPT],
                          critical=True, arbiter=_arbiter(RESULT_FAILED))
        assert run.outcome.outcome == OUTCOME_FAILURE

    def test_un_solo_giro_di_arbitrato(self):
        # arbitro che non risolve: l'asse diventa irrisolto, non si ri-arbitra.
        from devin.core.council_arbiter import NullExperimentGenerator
        run = run_council(_packet(), self._discordant_reviewers(), axes=[AXIS_CONCEPT],
                          critical=True, arbiter=Arbiter(NullExperimentGenerator()))
        assert run.arbitrated is True
        assert run.outcome.outcome == OUTCOME_NEEDS_HUMAN
        assert run.arbitrations[0].resolved is False

    def test_provenance_dell_arbitrato_nel_payload(self):
        run = run_council(_packet(), self._discordant_reviewers(), axes=[AXIS_CONCEPT],
                          critical=True, arbiter=_arbiter(RESULT_PASSED))
        council = run.to_review_payload()["council"]
        assert len(council["arbitrations"]) == 1
        arb = council["arbitrations"][0]
        assert arb["arbiter_id"] == "glm-colibri"
        assert arb["experiment"]["kind"] == "unit"
        assert arb["result"]["status"] == RESULT_PASSED
        assert council["preliminary_outcome"] == OUTCOME_NEEDS_ARBITRATION


class TestObservability:
    def test_heartbeat_copre_review_e_arbitrato(self):
        eventi = []
        run_council(_packet(), [
            Fixed("a", "fam-a", [AXIS_CONCEPT], VERDICT_PASS),
            Fixed("b", "fam-b", [AXIS_CONCEPT], VERDICT_FAIL),
        ], axes=[AXIS_CONCEPT], critical=True, arbiter=_arbiter(RESULT_PASSED),
            on_event=lambda k, p: eventi.append(k))
        assert "review_start" in eventi
        assert "review_done" in eventi
        assert "arbitration" in eventi
