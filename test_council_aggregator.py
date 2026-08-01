"""Test dell'Aggregator (Fase 3) — concordanza, discordanza, copertura.

Il punto che questi test proteggono: **niente promozione automatica** e
**niente maggioranza cieca**. Una discordanza non si risolve contando i voti.
"""

from __future__ import annotations

from devin.core.council import (
    AXES,
    AXIS_CONCEPT,
    AXIS_CONSTRAINTS,
    AXIS_QUALITY,
    AXIS_ROBUSTNESS,
    AXIS_SECURITY,
    VERDICT_FAIL,
    VERDICT_NEEDS_EVIDENCE,
    VERDICT_PASS,
    ReviewVerdict,
)
from devin.core.council_aggregator import (
    AXIS_DISCORDANT,
    AXIS_FAIL,
    AXIS_PASS,
    AXIS_UNCOVERED,
    AXIS_UNRESOLVED,
    OUTCOME_FAILURE,
    OUTCOME_NEEDS_ARBITRATION,
    OUTCOME_NEEDS_HUMAN,
    OUTCOME_PASS_CANDIDATE,
    Aggregator,
    aggregate_axis,
)


def _v(axis, verdict, rid="r1", family="fam1", reasoning="perche' si"):
    return ReviewVerdict(axis=axis, verdict=verdict, reviewer_id=rid, family=family, reasoning=reasoning)


def _all_pass():
    return [_v(axis, VERDICT_PASS) for axis in AXES]


class TestAxisAggregation:
    def test_concordi_pass(self):
        r = aggregate_axis(AXIS_CONCEPT, [_v(AXIS_CONCEPT, VERDICT_PASS, "a", "f1"),
                                          _v(AXIS_CONCEPT, VERDICT_PASS, "b", "f2")])
        assert r.status == AXIS_PASS

    def test_concordi_fail(self):
        r = aggregate_axis(AXIS_CONCEPT, [_v(AXIS_CONCEPT, VERDICT_FAIL, "a", "f1", "regola sbagliata")])
        assert r.status == AXIS_FAIL
        assert "regola sbagliata" in r.reason

    def test_discordanza_non_si_risolve_a_maggioranza(self):
        # 2 pass contro 1 fail NON diventa pass: serve un esperimento.
        verdicts = [
            _v(AXIS_CONCEPT, VERDICT_PASS, "a", "f1"),
            _v(AXIS_CONCEPT, VERDICT_PASS, "b", "f2"),
            _v(AXIS_CONCEPT, VERDICT_FAIL, "c", "f3", "conteggio errato"),
        ]
        r = aggregate_axis(AXIS_CONCEPT, verdicts)
        assert r.status == AXIS_DISCORDANT
        assert r.needs_arbitration is True
        assert len(r.conflicting_reasons()) == 3

    def test_solo_needs_evidence_e_irrisolto(self):
        r = aggregate_axis(AXIS_SECURITY, [_v(AXIS_SECURITY, VERDICT_NEEDS_EVIDENCE)])
        assert r.status == AXIS_UNRESOLVED

    def test_nessun_verdetto_e_scoperto(self):
        assert aggregate_axis(AXIS_QUALITY, []).status == AXIS_UNCOVERED

    def test_ignora_i_verdetti_di_altri_assi(self):
        r = aggregate_axis(AXIS_CONCEPT, [_v(AXIS_SECURITY, VERDICT_FAIL)])
        assert r.status == AXIS_UNCOVERED


class TestCouncilOutcome:
    def test_tutti_pass_e_solo_un_CANDIDATO(self):
        out = Aggregator().aggregate("a1", _all_pass())
        assert out.outcome == OUTCOME_PASS_CANDIDATE
        # il punto: non promuove, e sullo store resta pending_review
        assert out.promotable is False
        assert out.store_status == "pending_review"
        assert "rerun" in out.reason

    def test_un_asse_fail_boccia(self):
        verdicts = [_v(a, VERDICT_PASS) for a in AXES if a != AXIS_SECURITY]
        verdicts.append(_v(AXIS_SECURITY, VERDICT_FAIL, reasoning="segreto in chiaro"))
        out = Aggregator().aggregate("a1", verdicts)
        assert out.outcome == OUTCOME_FAILURE
        assert out.store_status == "verified_failure"
        assert [r.axis for r in out.failed_axes()] == [AXIS_SECURITY]

    def test_discordanza_precede_tutto(self):
        # Anche con un altro asse fallito, la discordanza va risolta prima.
        verdicts = [_v(a, VERDICT_PASS) for a in AXES if a not in (AXIS_CONCEPT, AXIS_QUALITY)]
        verdicts += [
            _v(AXIS_CONCEPT, VERDICT_PASS, "a", "f1"),
            _v(AXIS_CONCEPT, VERDICT_FAIL, "b", "f2"),
            _v(AXIS_QUALITY, VERDICT_FAIL, "c", "f3"),
        ]
        out = Aggregator().aggregate("a1", verdicts)
        assert out.outcome == OUTCOME_NEEDS_ARBITRATION
        assert [r.axis for r in out.axes_needing_arbitration()] == [AXIS_CONCEPT]

    def test_copertura_incompleta_niente_promozione(self):
        # 4 assi pass, 1 scoperto -> mai un candidato pass.
        verdicts = [_v(a, VERDICT_PASS) for a in AXES if a != AXIS_ROBUSTNESS]
        out = Aggregator().aggregate("a1", verdicts, uncovered_axes=[AXIS_ROBUSTNESS])
        assert out.outcome == OUTCOME_NEEDS_HUMAN
        assert out.store_status == "pending_review"
        assert [r.axis for r in out.incomplete_axes()] == [AXIS_ROBUSTNESS]

    def test_needs_evidence_su_un_asse_blocca_la_promozione(self):
        verdicts = [_v(a, VERDICT_PASS) for a in AXES if a != AXIS_SECURITY]
        verdicts.append(_v(AXIS_SECURITY, VERDICT_NEEDS_EVIDENCE, reasoning="bandit assente"))
        out = Aggregator().aggregate("a1", verdicts)
        assert out.outcome == OUTCOME_NEEDS_HUMAN

    def test_fail_batte_copertura_incompleta(self):
        verdicts = [_v(AXIS_CONSTRAINTS, VERDICT_FAIL, reasoning="endpoint inventato")]
        out = Aggregator().aggregate("a1", verdicts)
        assert out.outcome == OUTCOME_FAILURE

    def test_solo_gli_assi_richiesti(self):
        agg = Aggregator(required_axes=[AXIS_CONSTRAINTS, AXIS_SECURITY])
        out = agg.aggregate("a1", [_v(AXIS_CONSTRAINTS, VERDICT_PASS), _v(AXIS_SECURITY, VERDICT_PASS)])
        assert out.outcome == OUTCOME_PASS_CANDIDATE
        assert len(out.axis_results) == 2

    def test_serializzazione(self):
        out = Aggregator().aggregate("a1", _all_pass())
        d = out.to_dict()
        assert d["promotable"] is False
        assert d["store_status"] == "pending_review"
        assert len(d["axis_results"]) == len(AXES)
