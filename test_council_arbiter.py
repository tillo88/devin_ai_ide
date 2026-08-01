"""Test dell'Arbiter (Fase 4) — l'evidenza risolve, non l'autorevolezza.

I due principi protetti qui:
  - **pre-registrazione**: l'esperimento dichiara PRIMA cosa implica ciascun esito;
  - **inconclusivo != pass**: se non si genera, non gira o non decide, l'asse
    resta irrisolto e va a review umana.
"""

from __future__ import annotations

import pytest

from devin.core.council import (
    AXIS_CONCEPT,
    VERDICT_FAIL,
    VERDICT_NEEDS_EVIDENCE,
    VERDICT_PASS,
    CouncilError,
    ReviewPacket,
    ReviewVerdict,
)
from devin.core.council_aggregator import aggregate_axis
from devin.core.council_arbiter import (
    RESULT_FAILED,
    RESULT_INCONCLUSIVE,
    RESULT_PASSED,
    Arbiter,
    Experiment,
    ExperimentResult,
    NullExperimentGenerator,
)


def _packet() -> ReviewPacket:
    return ReviewPacket(packet_id="a1", case={"case_id": "c"}, attempt={"attempt_id": "a1"})


def _discordant():
    verdicts = [
        ReviewVerdict(axis=AXIS_CONCEPT, verdict=VERDICT_PASS, reviewer_id="a",
                      family="f1", reasoning="la regola mi sembra giusta"),
        ReviewVerdict(axis=AXIS_CONCEPT, verdict=VERDICT_FAIL, reviewer_id="b",
                      family="f2", reasoning="il conteggio parte da 1 invece che da 0"),
    ]
    return aggregate_axis(AXIS_CONCEPT, verdicts)


class _Gen:
    def __init__(self, experiment):
        self.experiment = experiment
        self.seen = None

    def generate(self, axis_result, packet):
        self.seen = axis_result
        return self.experiment


class _Runner:
    def __init__(self, result):
        self.result = result
        self.ran = None

    def run(self, experiment, packet):
        self.ran = experiment
        return self.result


def _exp(on_pass=VERDICT_PASS, on_fail=VERDICT_FAIL, kind="unit"):
    return Experiment(kind=kind, spec="assert count_up_to(3) == [0,1,2]",
                      on_pass=on_pass, on_fail=on_fail, rationale="isola il conteggio")


# --- contratto Experiment (pre-registrazione) ----------------------------

class TestExperimentContract:
    def test_esperimento_valido(self):
        e = _exp()
        assert e.to_dict()["on_pass"] == VERDICT_PASS

    def test_esiti_devono_essere_pre_registrati(self):
        with pytest.raises(CouncilError):
            Experiment(kind="unit", spec="x", on_pass="forse", on_fail=VERDICT_FAIL)

    def test_esperimento_che_non_discrimina_e_invalido(self):
        # se entrambi gli esiti portano allo stesso verdetto non prova nulla
        with pytest.raises(CouncilError):
            Experiment(kind="unit", spec="x", on_pass=VERDICT_PASS, on_fail=VERDICT_PASS)

    def test_tipo_e_spec_obbligatori(self):
        with pytest.raises(CouncilError):
            Experiment(kind="magia", spec="x", on_pass=VERDICT_PASS, on_fail=VERDICT_FAIL)
        with pytest.raises(CouncilError):
            Experiment(kind="unit", spec="  ", on_pass=VERDICT_PASS, on_fail=VERDICT_FAIL)


# --- risoluzione per evidenza --------------------------------------------

class TestArbitration:
    def test_esperimento_passato_risolve_secondo_pre_registrazione(self):
        arb = Arbiter(_Gen(_exp()), _Runner(ExperimentResult(RESULT_PASSED, "3 test verdi")))
        rec = arb.arbitrate(_discordant(), _packet())
        assert rec.resolved is True
        assert rec.resolved_verdict == VERDICT_PASS
        assert "3 test verdi" in rec.reasoning

    def test_esperimento_fallito_risolve_in_fail(self):
        arb = Arbiter(_Gen(_exp()), _Runner(ExperimentResult(RESULT_FAILED, "off-by-one confermato")))
        rec = arb.arbitrate(_discordant(), _packet())
        assert rec.resolved_verdict == VERDICT_FAIL

    def test_pre_registrazione_invertita_e_rispettata(self):
        # esperimento "cerca il bug": se PASSA, il bug esiste -> asse fail
        exp = _exp(on_pass=VERDICT_FAIL, on_fail=VERDICT_PASS)
        arb = Arbiter(_Gen(exp), _Runner(ExperimentResult(RESULT_PASSED, "riprodotto")))
        rec = arb.arbitrate(_discordant(), _packet())
        assert rec.resolved_verdict == VERDICT_FAIL

    def test_l_arbitro_vede_le_ragioni_contrastanti(self):
        gen = _Gen(_exp())
        Arbiter(gen, _Runner(ExperimentResult(RESULT_PASSED))).arbitrate(_discordant(), _packet())
        motivi = gen.seen.conflicting_reasons()
        assert any("conteggio" in m for m in motivi)
        assert len(motivi) == 2


# --- il silenzio non promuove --------------------------------------------

class TestInconclusiveNeverPasses:
    def test_nessun_generatore_resta_irrisolto(self):
        rec = Arbiter(NullExperimentGenerator()).arbitrate(_discordant(), _packet())
        assert rec.resolved is False
        assert rec.resolved_verdict == VERDICT_NEEDS_EVIDENCE

    def test_proposta_non_eseguita_non_e_evidenza(self):
        rec = Arbiter(_Gen(_exp()), runner=None).arbitrate(_discordant(), _packet())
        assert rec.resolved_verdict == VERDICT_NEEDS_EVIDENCE
        assert rec.experiment is not None      # la proposta resta tracciata
        assert rec.result is None

    def test_esito_inconclusivo_non_diventa_pass(self):
        arb = Arbiter(_Gen(_exp()), _Runner(ExperimentResult(RESULT_INCONCLUSIVE, "runner crashato")))
        rec = arb.arbitrate(_discordant(), _packet())
        assert rec.resolved_verdict == VERDICT_NEEDS_EVIDENCE
        assert "inconclusivo" in rec.reasoning

    def test_arbitrare_un_asse_non_discorde_solleva(self):
        concorde = aggregate_axis(AXIS_CONCEPT, [
            ReviewVerdict(axis=AXIS_CONCEPT, verdict=VERDICT_PASS, reviewer_id="a",
                          family="f1", reasoning="ok"),
        ])
        with pytest.raises(CouncilError):
            Arbiter().arbitrate(concorde, _packet())


# --- provenance -----------------------------------------------------------

class TestProvenance:
    def test_il_verdetto_porta_esperimento_ed_esito(self):
        arb = Arbiter(_Gen(_exp()), _Runner(ExperimentResult(RESULT_PASSED, "verde")),
                      arbiter_id="glm-colibri")
        v = arb.arbitrate(_discordant(), _packet()).to_verdict(family="glm")
        assert v.reviewer_id == "glm-colibri"
        assert v.evidence["arbitration"] is True
        assert v.evidence["experiment"]["kind"] == "unit"
        assert v.evidence["experiment_result"]["status"] == RESULT_PASSED
        assert len(v.evidence["conflicting_reasons"]) == 2

    def test_verdetto_irrisolto_ha_confidence_zero(self):
        v = Arbiter(NullExperimentGenerator()).arbitrate(_discordant(), _packet()).to_verdict()
        assert v.verdict == VERDICT_NEEDS_EVIDENCE
        assert v.confidence == 0.0
