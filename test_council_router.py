"""Test del CouncilRouter (Fase 2) — copertura, bounded, no-duplicati-di-famiglia.

Offline: reviewer stub, nessun modello.
"""

from __future__ import annotations

import pytest

from devin.core.council import (
    AXES,
    AXIS_CONCEPT,
    AXIS_CONSTRAINTS,
    AXIS_LENS,
    AXIS_QUALITY,
    AXIS_ROBUSTNESS,
    AXIS_SECURITY,
    VERDICT_PASS,
    CouncilError,
    ReviewerAdapter,
    ReviewPacket,
    ReviewVerdict,
)
from devin.core.council_router import CouncilRouter


class StubReviewer(ReviewerAdapter):
    def __init__(self, reviewer_id: str, family: str, axes):
        self.reviewer_id = reviewer_id
        self.family = family
        self.supported_axes = tuple(axes)

    def review(self, packet, axis):
        return ReviewVerdict(
            axis=axis, verdict=VERDICT_PASS, reviewer_id=self.reviewer_id,
            family=self.family, reasoning="stub",
        )


def _packet(pid: str = "a1") -> ReviewPacket:
    return ReviewPacket(packet_id=pid, case={"case_id": "c"}, attempt={"attempt_id": pid})


def _all_axes_reviewer(rid="omni", family="f-omni"):
    return StubReviewer(rid, family, AXES)


class TestCoverage:
    def test_copre_tutti_gli_assi_se_qualcuno_puo(self):
        plan = CouncilRouter([_all_axes_reviewer()]).route(_packet())
        assert plan.complete is True
        assert set(plan.covered_axes) == set(AXES)
        assert len(plan.assignments) == len(AXES)

    def test_assi_scoperti_sono_dichiarati_non_nascosti(self):
        # Solo un reviewer deterministico su 2 assi: gli altri 3 restano scoperti.
        r = StubReviewer("det", "deterministic", (AXIS_CONSTRAINTS, AXIS_SECURITY))
        plan = CouncilRouter([r]).route(_packet())
        assert plan.complete is False
        assert set(plan.uncovered_axes) == {AXIS_CONCEPT, AXIS_ROBUSTNESS, AXIS_QUALITY}

    def test_nessun_reviewer_nessuna_copertura(self):
        plan = CouncilRouter([]).route(_packet())
        assert plan.assignments == []
        assert set(plan.uncovered_axes) == set(AXES)

    def test_subset_di_assi(self):
        plan = CouncilRouter([_all_axes_reviewer()]).route(_packet(), axes=[AXIS_SECURITY])
        assert plan.covered_axes == [AXIS_SECURITY]
        assert plan.complete is True

    def test_asse_sconosciuto_solleva(self):
        with pytest.raises(CouncilError):
            CouncilRouter([]).route(_packet(), axes=["asse_finto"])


class TestFamilyDuplication:
    def test_mai_due_della_stessa_famiglia_sullo_stesso_asse(self):
        # Due reviewer stessa famiglia: su un cambio critico ne entra UNO solo.
        a = StubReviewer("glm-a", "glm", (AXIS_CONCEPT,))
        b = StubReviewer("glm-b", "glm", (AXIS_CONCEPT,))
        plan = CouncilRouter([a, b], critical_max_per_axis=2).route(_packet(), critical=True)
        assigned = plan.for_axis(AXIS_CONCEPT)
        assert len(assigned) == 1
        assert plan.families_on(AXIS_CONCEPT) == ["glm"]

    def test_famiglie_diverse_possono_coesistere_su_un_asse(self):
        a = StubReviewer("glm-a", "glm", (AXIS_CONCEPT,))
        b = StubReviewer("qwen-a", "qwen", (AXIS_CONCEPT,))
        plan = CouncilRouter([a, b], critical_max_per_axis=2).route(_packet(), critical=True)
        assert len(plan.for_axis(AXIS_CONCEPT)) == 2
        assert sorted(plan.families_on(AXIS_CONCEPT)) == ["glm", "qwen"]


class TestBounded:
    def test_non_critico_un_solo_reviewer_per_asse(self):
        a = StubReviewer("glm-a", "glm", (AXIS_CONCEPT,))
        b = StubReviewer("qwen-a", "qwen", (AXIS_CONCEPT,))
        plan = CouncilRouter([a, b], max_per_axis=1, critical_max_per_axis=2).route(_packet())
        assert len(plan.for_axis(AXIS_CONCEPT)) == 1

    def test_critico_allarga_ma_resta_bounded(self):
        revs = [StubReviewer(f"r{i}", f"fam{i}", (AXIS_CONCEPT,)) for i in range(5)]
        plan = CouncilRouter(revs, max_per_axis=1, critical_max_per_axis=2).route(_packet(), critical=True)
        assert len(plan.for_axis(AXIS_CONCEPT)) == 2  # non 5

    def test_max_total_taglia_il_council_esteso(self):
        revs = [StubReviewer(f"r{i}", f"fam{i}", AXES) for i in range(4)]
        plan = CouncilRouter(revs, critical_max_per_axis=3, max_total=4).route(_packet(), critical=True)
        assert len(plan.assignments) <= 4

    def test_configurazione_incoerente_solleva(self):
        with pytest.raises(CouncilError):
            CouncilRouter([], max_per_axis=0)
        with pytest.raises(CouncilError):
            CouncilRouter([], max_per_axis=3, critical_max_per_axis=1)


class TestPacketAndLens:
    def test_ogni_assegnazione_porta_la_lente_dell_asse(self):
        plan = CouncilRouter([_all_axes_reviewer()]).route(_packet())
        for a in plan.assignments:
            assert a.lens == AXIS_LENS[a.axis]
            assert a.lens  # non vuota

    def test_il_pacchetto_resta_cieco(self):
        # Il router non deve iniettare verdetti altrui nel pacchetto.
        plan = CouncilRouter([_all_axes_reviewer()]).route(_packet())
        for a in plan.assignments:
            assert not hasattr(a.packet, "known_reviews")
            assert "verdict" not in repr(a.packet)

    def test_piano_deterministico(self):
        revs = [
            StubReviewer("z", "zeta", AXES),
            StubReviewer("a", "alpha", AXES),
        ]
        p1 = CouncilRouter(revs).route(_packet()).to_dict()
        p2 = CouncilRouter(list(reversed(revs))).route(_packet()).to_dict()
        assert p1 == p2  # ordinamento stabile per (family, reviewer_id)
