"""Test del Federated Evidence Council — Fase 1 (contratti + reviewer locale).

Offline: nessun modello, nessuna rete. I validator reali sono iniettabili, cosi'
i casi noti sono deterministici e il test non dipende da bandit installato.

Blocca i principi che danno valore al Council:
  - pacchetto CIECO (i verdetti altrui non arrivano al reviewer);
  - il SILENZIO non e' un PASS (strumento assente / evidenza assente);
  - reasoning obbligatorio, contratti validati;
  - copertura: `needs_evidence` non copre un asse.
"""

from __future__ import annotations

import pytest

from devin.core.council import (
    AXES,
    AXIS_CONCEPT,
    AXIS_CONSTRAINTS,
    AXIS_SECURITY,
    VERDICT_FAIL,
    VERDICT_NEEDS_EVIDENCE,
    VERDICT_PASS,
    CouncilError,
    LocalDeterministicReviewer,
    ReviewPacket,
    ReviewVerdict,
    coverage_gaps,
)


def _packet(**over) -> ReviewPacket:
    base = dict(
        packet_id="attempt-1",
        case={"case_id": "c1", "expected_signals": ["tests_pass"]},
        attempt={"attempt_id": "attempt-1", "status": "auto_success"},
        result={"quality_gate": {"tests_run": True}, "files_written": ["main.py"]},
        project_path="/tmp/progetto",
    )
    base.update(over)
    return ReviewPacket(**base)


def _verdict(**over) -> ReviewVerdict:
    base = dict(
        axis=AXIS_CONSTRAINTS,
        verdict=VERDICT_PASS,
        reviewer_id="r1",
        family="deterministic",
        reasoning="motivo",
    )
    base.update(over)
    return ReviewVerdict(**base)


# --- contratto ReviewVerdict ---------------------------------------------

class TestReviewVerdict:
    def test_verdetto_valido(self):
        v = _verdict()
        assert v.conclusive is True
        assert v.to_dict()["axis"] == AXIS_CONSTRAINTS

    def test_reasoning_obbligatorio(self):
        # Il Council valuta il ragionamento, non solo l'output.
        with pytest.raises(CouncilError):
            _verdict(reasoning="   ")

    def test_asse_e_verdetto_devono_essere_noti(self):
        with pytest.raises(CouncilError):
            _verdict(axis="asse_inventato")
        with pytest.raises(CouncilError):
            _verdict(verdict="forse")

    def test_family_obbligatoria(self):
        # Serve al router per il no-duplicati-di-famiglia.
        with pytest.raises(CouncilError):
            _verdict(family="")

    def test_confidence_in_range(self):
        with pytest.raises(CouncilError):
            _verdict(confidence=1.5)

    def test_needs_evidence_non_e_conclusivo(self):
        assert _verdict(verdict=VERDICT_NEEDS_EVIDENCE).conclusive is False


# --- pacchetto cieco ------------------------------------------------------

class TestBlindPacket:
    def test_from_teacher_packet_scarta_i_verdetti_altrui(self):
        row = {
            "packet_version": "teacher_review_v1",
            "case": {"case_id": "c1", "expected_signals": ["tests_pass"]},
            "attempt": {"attempt_id": "a-42", "status": "auto_success"},
            "known_reviews": [{"reviewer": "teacher", "status": "verified_success"}],
            "known_corrections": [{"correction": "usa X"}],
        }
        packet = ReviewPacket.from_teacher_packet(row)
        assert packet.packet_id == "a-42"
        # anti-anchoring: nessuna traccia dei giudizi altrui nel pacchetto
        blob = repr(packet)
        assert "verified_success" not in blob
        assert "usa X" not in blob
        assert not hasattr(packet, "known_reviews")

    def test_packet_senza_attempt_id_e_invalido(self):
        with pytest.raises(CouncilError):
            ReviewPacket.from_teacher_packet({"case": {}, "attempt": {}})

    def test_has_execution_evidence(self):
        assert _packet().has_execution_evidence() is True
        assert _packet(project_path="").has_execution_evidence() is False
        assert _packet(result={}).has_execution_evidence() is False


# --- asse vincoli ---------------------------------------------------------

class TestConstraintsAxis:
    def test_violazione_macchina_verificabile_e_fail(self):
        def fake_validate(case, result, project_path):
            return {
                "overall": "fail",
                "signals": {"tests_pass": {"verdict": "fail", "detail": "test FALLITI"}},
                "machine_checked": 1,
                "not_machine_checkable": [],
            }

        reviewer = LocalDeterministicReviewer(validate_case=fake_validate)
        v = reviewer.review(_packet(), AXIS_CONSTRAINTS)
        assert v.verdict == VERDICT_FAIL
        assert v.violations and "test FALLITI" in v.violations[0]
        assert v.proposed_experiment  # l'arbitro deve avere un seme

    def test_tutti_i_vincoli_rispettati_e_pass(self):
        def fake_validate(case, result, project_path):
            return {"overall": "pass", "signals": {"tests_pass": {"verdict": "pass", "detail": "ok"}},
                    "machine_checked": 1, "not_machine_checkable": []}

        v = LocalDeterministicReviewer(validate_case=fake_validate).review(_packet(), AXIS_CONSTRAINTS)
        assert v.verdict == VERDICT_PASS
        assert v.confidence == 1.0

    def test_copertura_parziale_non_e_un_pass(self):
        # Se restano vincoli del caso che nessun controllo copre, l'asse NON e'
        # verificato per intero: dire "pass" li dichiarerebbe rispettati senza
        # averli guardati (stessa fallacia del punteggio su meta' dei pesi).
        def fake_validate(case, result, project_path):
            return {"overall": "pass", "signals": {}, "machine_checked": 1,
                    "not_machine_checkable": ["codice_manutenibile"]}

        v = LocalDeterministicReviewer(validate_case=fake_validate).review(_packet(), AXIS_CONSTRAINTS)
        assert v.verdict == VERDICT_NEEDS_EVIDENCE
        assert "codice_manutenibile" in v.reasoning
        assert v.evidence["not_machine_checkable"] == ["codice_manutenibile"]

    def test_copertura_parziale_ammessa_solo_se_esplicitamente_accettata(self):
        def fake_validate(case, result, project_path):
            return {"overall": "pass", "signals": {}, "machine_checked": 1,
                    "not_machine_checkable": ["codice_manutenibile"]}

        v = LocalDeterministicReviewer(
            validate_case=fake_validate, strict_coverage=False
        ).review(_packet(), AXIS_CONSTRAINTS)
        assert v.verdict == VERDICT_PASS
        assert v.confidence < 1.0   # e comunque a confidenza ridotta

    def test_niente_di_verificabile_e_needs_evidence_non_pass(self):
        def fake_validate(case, result, project_path):
            return {"overall": "unknown", "signals": {}, "machine_checked": 0,
                    "not_machine_checkable": ["design_pulito"]}

        v = LocalDeterministicReviewer(validate_case=fake_validate).review(_packet(), AXIS_CONSTRAINTS)
        assert v.verdict == VERDICT_NEEDS_EVIDENCE

    def test_senza_evidenza_esecuzione_non_inventa_un_pass(self):
        def boom(*a, **k):  # non deve nemmeno essere chiamato
            raise AssertionError("validate_case non va invocato senza evidenza")

        v = LocalDeterministicReviewer(validate_case=boom).review(
            _packet(project_path="", result={}), AXIS_CONSTRAINTS
        )
        assert v.verdict == VERDICT_NEEDS_EVIDENCE


# --- asse sicurezza -------------------------------------------------------

class TestSecurityAxis:
    def test_scanner_assente_non_e_pulito(self):
        # Il cuore del principio: silenzio != prova di pulizia.
        reviewer = LocalDeterministicReviewer(
            security_scan=lambda root, files: [],
            security_available=lambda: False,
        )
        v = reviewer.review(_packet(), AXIS_SECURITY)
        assert v.verdict == VERDICT_NEEDS_EVIDENCE
        assert v.evidence["available"] is False

    def test_scansione_pulita_e_pass(self):
        reviewer = LocalDeterministicReviewer(
            security_scan=lambda root, files: [],
            security_available=lambda: True,
        )
        v = reviewer.review(_packet(), AXIS_SECURITY)
        assert v.verdict == VERDICT_PASS
        assert v.evidence["findings"] == 0

    def test_finding_escalano_ma_non_bocciano_da_soli(self):
        # Policy anti-rumore esistente: i linter hanno falsi positivi.
        reviewer = LocalDeterministicReviewer(
            security_scan=lambda root, files: ["main.py: [B602 HIGH] subprocess shell=True (riga 3)"],
            security_available=lambda: True,
        )
        v = reviewer.review(_packet(), AXIS_SECURITY)
        assert v.verdict == VERDICT_NEEDS_EVIDENCE
        assert v.violations
        assert v.proposed_experiment

    def test_nessun_file_python_niente_giurisdizione(self):
        reviewer = LocalDeterministicReviewer(
            security_scan=lambda root, files: [],
            security_available=lambda: True,
        )
        v = reviewer.review(
            _packet(result={"quality_gate": {}, "files_written": ["app.js", "README.md"]}),
            AXIS_SECURITY,
        )
        assert v.verdict == VERDICT_NEEDS_EVIDENCE
        assert v.evidence["python_files"] == 0


# --- portata del reviewer -------------------------------------------------

class TestReviewerScope:
    def test_copre_solo_i_suoi_due_assi(self):
        reviewer = LocalDeterministicReviewer()
        assert reviewer.can_review(AXIS_CONSTRAINTS)
        assert reviewer.can_review(AXIS_SECURITY)
        assert not reviewer.can_review(AXIS_CONCEPT)

    def test_asse_fuori_portata_solleva(self):
        with pytest.raises(CouncilError):
            LocalDeterministicReviewer().review(_packet(), AXIS_CONCEPT)


# --- copertura ------------------------------------------------------------

class TestCoverage:
    def test_needs_evidence_non_copre_l_asse(self):
        verdicts = [
            _verdict(axis=AXIS_CONSTRAINTS, verdict=VERDICT_PASS),
            _verdict(axis=AXIS_SECURITY, verdict=VERDICT_NEEDS_EVIDENCE),
        ]
        gaps = coverage_gaps(verdicts)
        assert AXIS_CONSTRAINTS not in gaps
        assert AXIS_SECURITY in gaps

    def test_un_solo_reviewer_deterministico_non_copre_il_council(self):
        # Fase 1: il Council "a un reviewer" copre al massimo 2 assi su 5.
        verdicts = [
            _verdict(axis=AXIS_CONSTRAINTS, verdict=VERDICT_PASS),
            _verdict(axis=AXIS_SECURITY, verdict=VERDICT_PASS),
        ]
        assert len(coverage_gaps(verdicts)) == len(AXES) - 2
