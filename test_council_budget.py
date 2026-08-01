"""Test del Budgeter e del dispatch (Fase 5) — degrado senza blocco.

Principi protetti:
  - un reviewer rotto o lento NON blocca il Council;
  - il degrado e' registrato e diventa copertura mancante per l'Aggregator;
  - senza copertura minima non si promuove.
"""

from __future__ import annotations

from devin.core.council import (
    AXES,
    AXIS_CONSTRAINTS,
    AXIS_SECURITY,
    VERDICT_PASS,
    ReviewerAdapter,
    ReviewPacket,
    ReviewVerdict,
)
from devin.core.council_aggregator import OUTCOME_NEEDS_HUMAN, Aggregator
from devin.core.council_budget import (
    DEGRADED_BUDGET_TOTAL,
    DEGRADED_CONTRACT,
    DEGRADED_ERROR,
    DEGRADED_TIMEOUT,
    Budget,
    dispatch_plan,
)
from devin.core.council_router import CouncilRouter


class FakeClock:
    """Orologio finto: ogni reviewer 'costa' un tempo deciso dal test."""

    def __init__(self, costs=None):
        self.t = 0.0
        self.costs = costs or {}
        self._pending = 0.0

    def __call__(self) -> float:
        self.t += self._pending
        self._pending = 0.0
        return self.t

    def charge(self, seconds: float) -> None:
        self._pending = seconds


class GoodReviewer(ReviewerAdapter):
    def __init__(self, rid="good", family="f-good", axes=AXES, clock=None, cost=0.0):
        self.reviewer_id, self.family, self.supported_axes = rid, family, tuple(axes)
        self.clock, self.cost = clock, cost
        self.calls = 0

    def review(self, packet, axis):
        self.calls += 1
        if self.clock and self.cost:
            self.clock.charge(self.cost)
        return ReviewVerdict(axis=axis, verdict=VERDICT_PASS, reviewer_id=self.reviewer_id,
                             family=self.family, reasoning="ok")


class BrokenReviewer(ReviewerAdapter):
    def __init__(self, rid="broken", family="f-broken", axes=AXES):
        self.reviewer_id, self.family, self.supported_axes = rid, family, tuple(axes)

    def review(self, packet, axis):
        raise RuntimeError("modello non raggiungibile")


class LiarReviewer(ReviewerAdapter):
    """Restituisce un verdetto sull'asse sbagliato."""

    def __init__(self, rid="liar", family="f-liar", axes=AXES):
        self.reviewer_id, self.family, self.supported_axes = rid, family, tuple(axes)

    def review(self, packet, axis):
        other = AXIS_SECURITY if axis != AXIS_SECURITY else AXIS_CONSTRAINTS
        return ReviewVerdict(axis=other, verdict=VERDICT_PASS, reviewer_id=self.reviewer_id,
                             family=self.family, reasoning="fuori asse")


def _packet():
    return ReviewPacket(packet_id="a1", case={"case_id": "c"}, attempt={"attempt_id": "a1"})


def _plan(reviewers, **kw):
    return CouncilRouter(reviewers, **kw).route(_packet())


class TestHappyPath:
    def test_tutti_i_verdetti_raccolti(self):
        rep = dispatch_plan(_plan([GoodReviewer()]))
        assert len(rep.verdicts) == len(AXES)
        assert rep.degraded is False
        assert rep.reviews_done == len(AXES)


class TestDegradation:
    def test_reviewer_rotto_non_blocca_il_council(self):
        plan = _plan([BrokenReviewer(axes=[AXIS_SECURITY]),
                      GoodReviewer(axes=[a for a in AXES if a != AXIS_SECURITY])])
        rep = dispatch_plan(plan)
        assert len(rep.verdicts) == len(AXES) - 1          # il Council prosegue
        assert any(d.reason == DEGRADED_ERROR for d in rep.degradations)
        assert "non raggiungibile" in rep.degradations[0].detail

    def test_verdetto_fuori_asse_e_scartato(self):
        plan = _plan([LiarReviewer(axes=[AXIS_SECURITY])])
        rep = dispatch_plan(plan)
        assert rep.verdicts == []
        assert rep.degradations[0].reason == DEGRADED_CONTRACT

    def test_budget_totale_ferma_le_review_successive(self):
        plan = _plan([GoodReviewer()])
        rep = dispatch_plan(plan, budget=Budget(max_reviews=2))
        assert len(rep.verdicts) == 2
        assert all(d.reason == DEGRADED_BUDGET_TOTAL for d in rep.degradations)
        assert len(rep.degradations) == len(AXES) - 2

    def test_quota_per_reviewer(self):
        plan = _plan([GoodReviewer()])
        rep = dispatch_plan(plan, budget=Budget(max_reviews_per_reviewer=1))
        assert len(rep.verdicts) == 1

    def test_reviewer_lento_tiene_il_verdetto_ma_e_segnalato(self):
        clock = FakeClock()
        slow = GoodReviewer(rid="slow", axes=[AXIS_SECURITY], clock=clock, cost=5.0)
        plan = _plan([slow])
        rep = dispatch_plan(plan, budget=Budget(max_seconds_per_reviewer=1.0), clock=clock)
        # il verdetto e' evidenza reale: si tiene...
        assert len(rep.verdicts) == 1
        # ...ma il degrado e' registrato, cosi' non gli si da' altro lavoro
        assert any(d.reason == DEGRADED_TIMEOUT for d in rep.degradations)

    def test_tempo_totale_esaurito(self):
        clock = FakeClock()
        r = GoodReviewer(clock=clock, cost=2.0)
        rep = dispatch_plan(_plan([r]), budget=Budget(max_total_seconds=3.0), clock=clock)
        assert len(rep.verdicts) < len(AXES)
        assert any(d.reason == DEGRADED_BUDGET_TOTAL for d in rep.degradations)


class TestHeartbeat:
    def test_eventi_emessi_per_ogni_review(self):
        eventi = []
        dispatch_plan(_plan([GoodReviewer(axes=[AXIS_SECURITY])]),
                      on_event=lambda k, p: eventi.append((k, p["axis"])))
        assert ("review_start", AXIS_SECURITY) in eventi
        assert ("review_done", AXIS_SECURITY) in eventi

    def test_evento_di_errore(self):
        eventi = []
        dispatch_plan(_plan([BrokenReviewer(axes=[AXIS_SECURITY])]),
                      on_event=lambda k, p: eventi.append(k))
        assert "review_error" in eventi


class TestCoverageAfterDegradation:
    def test_asse_perso_per_degrado_conta_come_scoperto(self):
        plan = _plan([BrokenReviewer(axes=[AXIS_SECURITY]),
                      GoodReviewer(axes=[a for a in AXES if a != AXIS_SECURITY])])
        rep = dispatch_plan(plan)
        assert AXIS_SECURITY in rep.uncovered_axes(plan)

    def test_niente_promozione_se_il_budget_ha_tagliato_la_copertura(self):
        # Il ponte con la Fase 3: degrado -> copertura incompleta -> niente promozione.
        plan = _plan([BrokenReviewer(axes=[AXIS_SECURITY]),
                      GoodReviewer(axes=[a for a in AXES if a != AXIS_SECURITY])])
        rep = dispatch_plan(plan)
        outcome = Aggregator().aggregate("a1", rep.verdicts, uncovered_axes=rep.uncovered_axes(plan))
        assert outcome.outcome == OUTCOME_NEEDS_HUMAN
        assert outcome.promotable is False

    def test_report_serializzabile(self):
        rep = dispatch_plan(_plan([GoodReviewer(axes=[AXIS_SECURITY])]))
        d = rep.to_dict()
        assert d["reviews_done"] == 1
        assert d["degraded"] is False
