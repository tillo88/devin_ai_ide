"""Test del LocalModelReviewer — model-agnostic e fail-soft.

NESSUNA chiamata a un modello reale: `chat` e' sempre iniettata.

Il principio centrale qui: il modello operativo e' un **placeholder** finche' la
Model Evaluation Suite non sceglie il candidato. Questi test bloccano che il
Council resti corretto quando il modello cambia, e che un modello che non
risponde non produca mai un pass.
"""

from __future__ import annotations

import json

import pytest

from devin.core.council import (
    AXIS_CONCEPT,
    AXIS_QUALITY,
    AXIS_ROBUSTNESS,
    AXIS_SECURITY,
    VERDICT_FAIL,
    VERDICT_NEEDS_EVIDENCE,
    VERDICT_PASS,
    CouncilError,
    ReviewPacket,
)
from devin.core.council_model_reviewer import (
    LocalModelReviewer,
    ModelReviewSpec,
    extract_json,
)

SEMANTIC_AXES = (AXIS_CONCEPT, AXIS_ROBUSTNESS, AXIS_QUALITY)


def _spec(rid="model-reviewer", family="modelfam", axes=SEMANTIC_AXES):
    return ModelReviewSpec(reviewer_id=rid, family=family, supported_axes=axes)


def _packet():
    return ReviewPacket(
        packet_id="a1",
        case={"case_id": "c1", "task": "scrivi is_prime", "expected_signals": ["tests_pass"]},
        attempt={"attempt_id": "a1", "response": "def is_prime(n): ...", "status": "auto_success"},
        result={"files_written": ["is_prime.py"]},
        project_path="/tmp/x",
    )


def _reply(verdict=VERDICT_PASS, reasoning="il concetto di primalita' e' applicato bene", **extra):
    payload = {"verdict": verdict, "reasoning": reasoning, "violations": [], "confidence": 0.8}
    payload.update(extra)
    return json.dumps(payload)


def _reviewer(reply, *, spec=None, identity=None):
    calls = []

    def chat(messages):
        calls.append(messages)
        return reply(messages) if callable(reply) else reply

    r = LocalModelReviewer(chat, spec or _spec(), model_identity=identity)
    r.calls = calls
    return r


# --- niente hardcode del modello -----------------------------------------

class TestModelAgnostic:
    def test_nessun_nome_di_modello_nel_sorgente(self):
        # Guardia contro le regressioni: il Council non deve nominare modelli.
        from pathlib import Path
        import devin.core.council_model_reviewer as mod
        source = Path(mod.__file__).read_text(encoding="utf-8").lower()
        for name in ("ornith", "qwen", "llama-3", "glm-5", "deepseek", "gpt-4"):
            assert name not in source, f"modello hardcodato nel sorgente: {name}"

    def test_identita_del_modello_registrata_nella_provenance(self):
        r = _reviewer(_reply(), identity=lambda: "modello-A-v1")
        v = r.review(_packet(), AXIS_CONCEPT)
        assert v.evidence["model"] == "modello-A-v1"

    def test_cambio_modello_cambia_la_provenance_non_il_codice(self):
        # Stesso reviewer, modello diverso -> attribuzione diversa.
        served = {"name": "modello-A"}
        r = _reviewer(_reply(), identity=lambda: served["name"])
        v1 = r.review(_packet(), AXIS_CONCEPT)
        served["name"] = "modello-B"
        v2 = r.review(_packet(), AXIS_CONCEPT)
        assert v1.evidence["model"] == "modello-A"
        assert v2.evidence["model"] == "modello-B"

    def test_identita_sconosciuta_e_dichiarata(self):
        v = _reviewer(_reply()).review(_packet(), AXIS_CONCEPT)
        assert v.evidence["model"] == "unknown"

    def test_identita_che_esplode_non_rompe_la_review(self):
        def boom():
            raise RuntimeError("endpoint giu'")

        v = _reviewer(_reply(), identity=boom).review(_packet(), AXIS_CONCEPT)
        assert v.verdict == VERDICT_PASS
        assert v.evidence["model"] == "unknown"

    def test_family_obbligatoria_protegge_dagli_errori_correlati(self):
        with pytest.raises(CouncilError):
            ModelReviewSpec(reviewer_id="r", family="  ", supported_axes=SEMANTIC_AXES)


# --- fail-soft: mai un pass indovinato -----------------------------------

class TestFailSoft:
    def test_modello_irraggiungibile(self):
        def boom(messages):
            raise ConnectionError("connection refused")

        v = LocalModelReviewer(boom, _spec()).review(_packet(), AXIS_CONCEPT)
        assert v.verdict == VERDICT_NEEDS_EVIDENCE
        assert v.confidence == 0.0

    def test_risposta_vuota(self):
        v = _reviewer("").review(_packet(), AXIS_CONCEPT)
        assert v.verdict == VERDICT_NEEDS_EVIDENCE

    def test_risposta_none(self):
        v = _reviewer(None).review(_packet(), AXIS_CONCEPT)
        assert v.verdict == VERDICT_NEEDS_EVIDENCE

    def test_json_non_parsabile(self):
        v = _reviewer("il codice mi sembra a posto, direi pass").review(_packet(), AXIS_CONCEPT)
        assert v.verdict == VERDICT_NEEDS_EVIDENCE
        assert "raw_excerpt" in v.evidence

    def test_verdetto_fuori_contratto(self):
        v = _reviewer(json.dumps({"verdict": "boh", "reasoning": "non so"})).review(_packet(), AXIS_CONCEPT)
        assert v.verdict == VERDICT_NEEDS_EVIDENCE

    def test_verdetto_senza_ragionamento_e_scartato(self):
        # Il Council valuta il ragionamento: un'etichetta nuda non e' evidenza.
        v = _reviewer(json.dumps({"verdict": "pass", "reasoning": "   "})).review(_packet(), AXIS_CONCEPT)
        assert v.verdict == VERDICT_NEEDS_EVIDENCE


# --- parsing tollerante ---------------------------------------------------

class TestParsing:
    def test_code_fence(self):
        v = _reviewer("```json\n" + _reply() + "\n```").review(_packet(), AXIS_CONCEPT)
        assert v.verdict == VERDICT_PASS

    def test_json_annegato_nel_testo(self):
        v = _reviewer("Ecco la mia analisi.\n" + _reply(verdict=VERDICT_FAIL) + "\nSpero sia utile.").review(
            _packet(), AXIS_CONCEPT)
        assert v.verdict == VERDICT_FAIL

    def test_extract_json_helper(self):
        assert extract_json('{"a": 1}') == {"a": 1}
        assert extract_json("nessun json qui") is None
        assert extract_json("") is None
        assert extract_json("[1,2,3]") is None      # solo oggetti

    def test_due_oggetti_json_prende_il_primo_valido(self):
        # Una regex greedy prenderebbe dal primo '{' all'ultimo '}' e fallirebbe.
        raw = 'Prima: {"verdict": "pass", "reasoning": "ok"} Poi: {"altro": 1}'
        assert extract_json(raw)["verdict"] == "pass"

    def test_graffe_dentro_le_stringhe_non_confondono(self):
        raw = '{"verdict": "fail", "reasoning": "il dict {a: 1} e\' sbagliato"}'
        data = extract_json(raw)
        assert data["verdict"] == "fail"
        assert "{a: 1}" in data["reasoning"]

    def test_json_annidato(self):
        raw = 'testo {"verdict": "pass", "reasoning": "ok", "meta": {"n": 1}} coda'
        assert extract_json(raw)["meta"]["n"] == 1

    def test_confidence_fuori_range_viene_normalizzata(self):
        v = _reviewer(_reply(confidence=42)).review(_packet(), AXIS_CONCEPT)
        assert v.confidence == 1.0

    def test_confidence_non_numerica_ha_default(self):
        v = _reviewer(_reply(confidence="molta")).review(_packet(), AXIS_CONCEPT)
        assert 0.0 <= v.confidence <= 1.0

    def test_violations_non_lista_viene_normalizzata(self):
        v = _reviewer(_reply(verdict=VERDICT_FAIL, violations="una sola")).review(_packet(), AXIS_CONCEPT)
        assert v.violations == ["una sola"]


# --- prompt ---------------------------------------------------------------

class TestPrompt:
    def test_il_prompt_porta_la_lente_dell_asse(self):
        r = _reviewer(_reply())
        r.review(_packet(), AXIS_ROBUSTNESS)
        system = r.calls[0][0]["content"]
        assert AXIS_ROBUSTNESS in system
        assert "casi limite" in system.lower() or "bordi" in system.lower()

    def test_needs_evidence_e_dichiarato_legittimo(self):
        # Non forzare una scelta binaria su evidenza insufficiente.
        r = _reviewer(_reply())
        r.review(_packet(), AXIS_CONCEPT)
        assert "legittima" in r.calls[0][0]["content"].lower()

    def test_il_prompt_non_rivela_altri_verdetti(self):
        r = _reviewer(_reply())
        r.review(_packet(), AXIS_CONCEPT)
        blob = json.dumps(r.calls[0])
        assert "known_reviews" not in blob
        assert "verified_success" not in blob

    def test_avviso_anti_anchoring_sull_esito_meccanico(self):
        # L'esito del gate e' un dato, non un giudizio: il reviewer non deve
        # allinearsi per inerzia (falserebbe anche il confronto tra modelli).
        r = _reviewer(_reply())
        r.review(_packet(), AXIS_CONCEPT)
        system = r.calls[0][0]["content"].lower()
        assert "non allinearti" in system
        assert "dato, non un giudizio" in system

    def test_esito_meccanico_puo_essere_nascosto_del_tutto(self):
        chat_calls = []

        def chat(messages):
            chat_calls.append(messages)
            return _reply()

        r = LocalModelReviewer(chat, _spec(), include_mechanical_status=False)
        packet = _packet()
        packet.attempt["status"] = "auto_success"
        r.review(packet, AXIS_CONCEPT)
        assert "auto_success" not in chat_calls[0][1]["content"]

    def test_campi_lunghi_troncati(self):
        packet = _packet()
        packet.attempt["response"] = "x" * 50000
        r = _reviewer(_reply())
        r.review(packet, AXIS_CONCEPT)
        assert "troncato" in r.calls[0][1]["content"]


# --- portata --------------------------------------------------------------

class TestScope:
    def test_asse_non_supportato_solleva(self):
        with pytest.raises(CouncilError):
            _reviewer(_reply()).review(_packet(), AXIS_SECURITY)

    def test_spec_invalida(self):
        with pytest.raises(CouncilError):
            ModelReviewSpec(reviewer_id="", family="f", supported_axes=SEMANTIC_AXES)
        with pytest.raises(CouncilError):
            ModelReviewSpec(reviewer_id="r", family="f", supported_axes=())
        with pytest.raises(CouncilError):
            ModelReviewSpec(reviewer_id="r", family="f", supported_axes=("asse_finto",))
