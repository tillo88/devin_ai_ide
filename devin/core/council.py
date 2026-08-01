"""Federated Evidence Council — Fase 1: contratti + reviewer deterministico locale.

Design: `docs/devin_federated_council_design_v1.md` (§3 assi, §4.1 ReviewerAdapter).

Questa fase e' **offline e senza modelli**: definisce il contratto comune dei
reviewer e implementa il primo reviewer reale, che non e' un LLM ma i validator
deterministici che il progetto ha gia' (`training/validators.py`,
`engine/security_critic.py`). Serve a poter costruire router/aggregator/arbiter
su un Council gia' funzionante "a un reviewer", esattamente come il mini-swarm
e' stato costruito su executor stub.

Principi implementati qui (ereditati da P6/anti-contaminazione):

- **Cieco**: il pacchetto per il reviewer NON contiene i verdetti altrui
  (`ReviewPacket.from_teacher_packet` scarta `known_reviews`).
- **Il silenzio non e' un PASS**: se un controllo non e' eseguibile (nessuna
  allowlist, scanner non installato, evidenza mancante) il verdetto e'
  `needs_evidence`, mai `pass`. L'assenza di un finding non e' prova di
  assenza del problema.
- **Ragionamento obbligatorio**: un verdetto senza `reasoning` e' invalido.
- **Nessuna auto-promozione**: qui si producono review, non stati promossi.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

# --- assi (§3 del design) -------------------------------------------------
AXIS_CONCEPT = "correttezza_concettuale"
AXIS_ROBUSTNESS = "robustezza"
AXIS_CONSTRAINTS = "vincoli"
AXIS_SECURITY = "sicurezza"
AXIS_QUALITY = "qualita"

AXES: tuple = (
    AXIS_CONCEPT,
    AXIS_ROBUSTNESS,
    AXIS_CONSTRAINTS,
    AXIS_SECURITY,
    AXIS_QUALITY,
)

# Lente per-asse: cosa deve guardare il reviewer, e cosa NON e' compito suo.
# Serve al router per costruire il pacchetto mirato e ai reviewer-modello come
# istruzione. Ogni lente chiede il ragionamento sul CONCETTO prima del codice.
AXIS_LENS: Dict[str, str] = {
    AXIS_CONCEPT: (
        "Valuta se la REGOLA logica/astratta applicata e' corretta, prima di guardare il codice. "
        "Spiega il concetto con parole tue e indica dove il ragionamento devia (es. '3+3=5' significa "
        "che non ha capito il conteggio, non che ha sbagliato a digitare). "
        "NON giudicare stile, sicurezza o copertura dei test: non e' il tuo asse."
    ),
    AXIS_ROBUSTNESS: (
        "Cerca i casi in cui la soluzione si rompe: bordi, input negativi o degeneri, valori vuoti, "
        "concorrenza, dati malformati. Il tuo obiettivo e' distinguere il 'corretto davvero' dal "
        "'plausibile ma fragile'. Proponi il caso limite piu' discriminante. "
        "NON giudicare correttezza concettuale, sicurezza o stile."
    ),
    AXIS_CONSTRAINTS: (
        "Verifica l'aderenza ai vincoli dichiarati: endpoint/allowlist reali, nessuna API inventata, "
        "nessun hardcoding o mock che aggiri il test invece di soddisfarlo. "
        "NON giudicare eleganza, prestazioni o sicurezza."
    ),
    AXIS_SECURITY: (
        "Cerca vulnerabilita' reali sfruttabili nel contesto: input non validati, segreti, injection, "
        "deserializzazione non sicura, permessi. Distingui un rischio reale da un falso positivo di linter. "
        "NON giudicare correttezza funzionale o stile."
    ),
    AXIS_QUALITY: (
        "Verifica che sia stato fatto ESATTAMENTE il richiesto: niente scope creep, niente lavoro incompleto "
        "spacciato per finito, codice manutenibile. "
        "NON giudicare sicurezza ne' correttezza concettuale."
    ),
}

# --- verdetti -------------------------------------------------------------
VERDICT_PASS = "pass"
VERDICT_FAIL = "fail"
VERDICT_NEEDS_EVIDENCE = "needs_evidence"

VERDICTS: tuple = (VERDICT_PASS, VERDICT_FAIL, VERDICT_NEEDS_EVIDENCE)


class CouncilError(ValueError):
    """Contratto del Council violato (verdetto/pacchetto malformato)."""


@dataclass
class ReviewVerdict:
    """Esito di UN reviewer su UN asse.

    `reasoning` e' obbligatorio: il Council valuta il ragionamento, non solo
    l'output (§1 del design). `proposed_experiment` e' il seme dell'arbitro:
    un test che proverebbe o smentirebbe il verdetto.
    """

    axis: str
    verdict: str
    reviewer_id: str
    family: str
    reasoning: str
    confidence: float = 1.0
    violations: List[str] = field(default_factory=list)
    proposed_experiment: Optional[str] = None
    evidence: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.axis not in AXES:
            raise CouncilError(f"asse sconosciuto: {self.axis!r}")
        if self.verdict not in VERDICTS:
            raise CouncilError(f"verdetto sconosciuto: {self.verdict!r}")
        if not str(self.reviewer_id).strip():
            raise CouncilError("reviewer_id obbligatorio")
        if not str(self.family).strip():
            raise CouncilError("family obbligatoria (serve per il no-duplicati-di-famiglia)")
        if not str(self.reasoning).strip():
            raise CouncilError("reasoning obbligatorio: il Council valuta il ragionamento")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise CouncilError("confidence deve essere in [0,1]")

    @property
    def conclusive(self) -> bool:
        """True se il verdetto decide (pass/fail). `needs_evidence` non decide."""
        return self.verdict in (VERDICT_PASS, VERDICT_FAIL)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "axis": self.axis,
            "verdict": self.verdict,
            "reviewer_id": self.reviewer_id,
            "family": self.family,
            "reasoning": self.reasoning,
            "confidence": self.confidence,
            "violations": list(self.violations),
            "proposed_experiment": self.proposed_experiment,
            "evidence": dict(self.evidence),
        }


@dataclass
class ReviewPacket:
    """Evidenza data a UN reviewer. Cieco per costruzione.

    `result` e `project_path` servono ai controlli deterministici (quality gate,
    file scritti); restano vuoti quando si giudica solo da packet esportato, e
    in quel caso il reviewer deterministico risponde `needs_evidence` invece di
    inventare un PASS.
    """

    packet_id: str
    case: Dict[str, Any] = field(default_factory=dict)
    attempt: Dict[str, Any] = field(default_factory=dict)
    result: Dict[str, Any] = field(default_factory=dict)
    project_path: str = ""

    def __post_init__(self) -> None:
        if not str(self.packet_id).strip():
            raise CouncilError("packet_id obbligatorio")

    @classmethod
    def from_teacher_packet(
        cls,
        row: Dict[str, Any],
        *,
        result: Optional[Dict[str, Any]] = None,
        project_path: str = "",
    ) -> "ReviewPacket":
        """Costruisce un pacchetto CIECO da una riga `teacher_review_v1`.

        `known_reviews` e `known_corrections` vengono deliberatamente SCARTATI:
        un reviewer non deve vedere i giudizi altrui (anti-anchoring, §1).
        """
        row = row or {}
        attempt = dict(row.get("attempt") or {})
        packet_id = str(attempt.get("attempt_id") or row.get("packet_id") or "").strip()
        if not packet_id:
            raise CouncilError("teacher packet senza attempt_id: impossibile identificare la review")
        return cls(
            packet_id=packet_id,
            case=dict(row.get("case") or {}),
            attempt=attempt,
            result=dict(result or {}),
            project_path=project_path,
        )

    @property
    def expected_signals(self) -> List[str]:
        return [str(s) for s in (self.case.get("expected_signals") or [])]

    @property
    def files_written(self) -> List[str]:
        return [str(f) for f in (self.result.get("files_written") or [])]

    def has_execution_evidence(self) -> bool:
        """True se il pacchetto porta l'evidenza d'esecuzione (gate + progetto).

        Senza questa, i controlli deterministici non sono eseguibili e il
        verdetto onesto e' `needs_evidence`.
        """
        return bool(self.project_path) and bool(self.result)


class ReviewerAdapter:
    """Contratto comune a reviewer locali, modello ed esterni (§4.1).

    Le implementazioni dichiarano `reviewer_id`, `family` (per la regola
    no-duplicati-di-famiglia del router) e gli assi che sanno coprire.
    """

    reviewer_id: str = "abstract"
    family: str = "abstract"
    supported_axes: Sequence[str] = ()

    def can_review(self, axis: str) -> bool:
        return axis in self.supported_axes

    def review(self, packet: ReviewPacket, axis: str) -> ReviewVerdict:  # pragma: no cover
        raise NotImplementedError

    # helper condiviso: costruisce un verdetto gia' etichettato col reviewer
    def _verdict(
        self,
        axis: str,
        verdict: str,
        reasoning: str,
        *,
        confidence: float = 1.0,
        violations: Optional[List[str]] = None,
        proposed_experiment: Optional[str] = None,
        evidence: Optional[Dict[str, Any]] = None,
    ) -> ReviewVerdict:
        return ReviewVerdict(
            axis=axis,
            verdict=verdict,
            reviewer_id=self.reviewer_id,
            family=self.family,
            reasoning=reasoning,
            confidence=confidence,
            violations=list(violations or []),
            proposed_experiment=proposed_experiment,
            evidence=dict(evidence or {}),
        )


class LocalDeterministicReviewer(ReviewerAdapter):
    """Reviewer degli assi VINCOLI e SICUREZZA, senza modelli.

    Non e' un LLM: riusa i validator deterministici gia' esistenti. E' il
    reviewer piu' affidabile del Council (niente allucinazioni) ma anche il piu'
    limitato: fuori dai suoi due assi non ha voce, e quando un controllo non e'
    eseguibile lo dichiara invece di dare un PASS gratis.
    """

    reviewer_id = "local-deterministic"
    family = "deterministic"
    supported_axes = (AXIS_CONSTRAINTS, AXIS_SECURITY)

    def __init__(self, *, validate_case=None, security_scan=None, security_available=None,
                 strict_coverage: bool = True):
        # Iniettabili per i test offline; di default usano i moduli reali.
        self._validate_case = validate_case
        self._security_scan = security_scan
        self._security_available = security_available
        # strict_coverage=True (default onesto): se restano vincoli del caso non
        # macchina-verificabili, l'asse NON e' dichiarato pass — e' `needs_evidence`
        # e passa a un reviewer semantico. Con False si torna al pass a confidenza
        # ridotta (utile solo se si accetta esplicitamente la copertura parziale).
        self.strict_coverage = bool(strict_coverage)

    # --- lazy import: il core non deve dipendere dal training a import-time
    def _get_validate_case(self):
        if self._validate_case is None:
            from devin.training.validators import validate_case
            self._validate_case = validate_case
        return self._validate_case

    def _get_security(self):
        if self._security_scan is None or self._security_available is None:
            from devin.engine import security_critic
            if self._security_scan is None:
                self._security_scan = security_critic.scan_python_files
            if self._security_available is None:
                self._security_available = security_critic.bandit_available
        return self._security_scan, self._security_available

    def review(self, packet: ReviewPacket, axis: str) -> ReviewVerdict:
        if not self.can_review(axis):
            raise CouncilError(
                f"{self.reviewer_id} non copre l'asse {axis!r}: "
                f"assi supportati {tuple(self.supported_axes)}"
            )
        if axis == AXIS_CONSTRAINTS:
            return self._review_constraints(packet)
        return self._review_security(packet)

    # --- asse 3: aderenza ai vincoli -------------------------------------
    def _review_constraints(self, packet: ReviewPacket) -> ReviewVerdict:
        if not packet.has_execution_evidence():
            return self._verdict(
                AXIS_CONSTRAINTS,
                VERDICT_NEEDS_EVIDENCE,
                "Evidenza d'esecuzione assente (quality gate e/o project_path): i controlli "
                "deterministici non sono eseguibili. Assenza di controllo != conformita'.",
                confidence=1.0,
                proposed_experiment="Rieseguire l'attempt catturando quality_gate e files_written, poi rivalutare.",
            )
        validation = self._get_validate_case()(packet.case, packet.result, packet.project_path)
        overall = str(validation.get("overall") or "unknown")
        signals = validation.get("signals") or {}
        failed = [
            f"{sig}: {info.get('detail', '')}"
            for sig, info in signals.items()
            if info.get("verdict") == VERDICT_FAIL
        ]
        unchecked = [str(s) for s in (validation.get("not_machine_checkable") or [])]
        evidence = {
            "machine_checked": validation.get("machine_checked", 0),
            "not_machine_checkable": unchecked,
            "skipped_files": validation.get("skipped_files") or [],
        }

        if overall == VERDICT_FAIL:
            return self._verdict(
                AXIS_CONSTRAINTS,
                VERDICT_FAIL,
                "Almeno un vincolo macchina-verificabile e' violato: "
                + " | ".join(failed)[:800],
                violations=failed,
                evidence=evidence,
                proposed_experiment=(
                    "Rieseguire i soli segnali falliti dopo la correzione, per confermare "
                    "che la violazione sparisce senza introdurne altre."
                ),
            )
        if overall == VERDICT_PASS:
            checked = validation.get("machine_checked", 0)
            if unchecked and self.strict_coverage:
                # Copertura PARZIALE non e' un verdetto pieno sull'asse: alcuni
                # vincoli del caso non sono stati verificati da nessuno. Dire
                # "pass" li dichiarerebbe rispettati senza averli guardati —
                # e' la stessa fallacia del punteggio calcolato su meta' dei pesi.
                return self._verdict(
                    AXIS_CONSTRAINTS,
                    VERDICT_NEEDS_EVIDENCE,
                    f"I {checked} vincoli macchina-verificabili sono rispettati, ma restano "
                    f"{len(unchecked)} vincoli del caso che nessun controllo deterministico "
                    f"copre ({', '.join(unchecked[:5])}). L'asse NON e' verificato per intero: "
                    "serve un reviewer semantico.",
                    confidence=1.0,
                    evidence=evidence,
                    proposed_experiment=(
                        "Rendere macchina-verificabili i segnali residui (o assegnarli a un "
                        "reviewer semantico) e rivalutare l'asse."
                    ),
                )
            note = ""
            if unchecked:
                note = (
                    f" Restano {len(unchecked)} segnali non macchina-verificabili "
                    f"({', '.join(unchecked[:5])}): fuori dalla portata di questo reviewer."
                )
            return self._verdict(
                AXIS_CONSTRAINTS,
                VERDICT_PASS,
                f"Tutti i {checked} vincoli macchina-verificabili sono rispettati.{note}",
                confidence=0.7 if unchecked else 1.0,
                evidence=evidence,
            )
        return self._verdict(
            AXIS_CONSTRAINTS,
            VERDICT_NEEDS_EVIDENCE,
            "Nessun vincolo era macchina-verificabile per questo caso: il giudizio "
            "richiede un reviewer semantico. Non equivale a conformita'.",
            evidence=evidence,
            proposed_experiment=(
                "Aggiungere al caso almeno un expected_signal verificabile "
                "(es. tests_pass, no_invented_endpoint) e rieseguire."
            ),
        )

    # --- asse 4: sicurezza ------------------------------------------------
    def _review_security(self, packet: ReviewPacket) -> ReviewVerdict:
        if not packet.has_execution_evidence():
            return self._verdict(
                AXIS_SECURITY,
                VERDICT_NEEDS_EVIDENCE,
                "Evidenza d'esecuzione assente: nessun file da scansionare. "
                "Assenza di scansione != assenza di vulnerabilita'.",
                proposed_experiment="Rieseguire l'attempt registrando files_written, poi riscansionare.",
            )
        scan, available = self._get_security()
        if not available():
            # Stesso principio del gate 'proven clean': se lo scanner non c'e',
            # il silenzio NON e' una prova di pulizia.
            return self._verdict(
                AXIS_SECURITY,
                VERDICT_NEEDS_EVIDENCE,
                "Scanner di sicurezza (bandit) non disponibile: nessun finding, ma il "
                "silenzio non e' prova di pulizia. Verdetto sospeso per mancanza di strumento.",
                evidence={"scanner": "bandit", "available": False},
                proposed_experiment="Installare bandit (pip install bandit) e riscansionare gli stessi file.",
            )
        py_files = [f for f in packet.files_written if str(f).lower().endswith(".py")]
        if not py_files:
            return self._verdict(
                AXIS_SECURITY,
                VERDICT_NEEDS_EVIDENCE,
                "Nessun file Python tra quelli scritti: questo reviewer (bandit) non ha "
                "giurisdizione. Altri linguaggi restano non coperti.",
                evidence={"scanner": "bandit", "python_files": 0},
            )
        findings = list(scan(packet.project_path, py_files) or [])
        if findings:
            # Policy anti-rumore esistente (security_critic): i finding NON
            # bocciano da soli — hanno falsi positivi. Vanno escalati, non
            # trasformati in un fail automatico.
            return self._verdict(
                AXIS_SECURITY,
                VERDICT_NEEDS_EVIDENCE,
                f"{len(findings)} finding di sicurezza MEDIUM+ da bandit. I linter hanno falsi "
                "positivi: serve un reviewer semantico o umano per decidere se sono reali.",
                violations=findings,
                confidence=0.6,
                evidence={"scanner": "bandit", "findings": len(findings), "python_files": len(py_files)},
                proposed_experiment=(
                    "Per ogni finding, scrivere un test che dimostri lo sfruttamento reale "
                    "(o la sua impossibilita') nel contesto del progetto."
                ),
            )
        return self._verdict(
            AXIS_SECURITY,
            VERDICT_PASS,
            f"Scansione bandit eseguita su {len(py_files)} file Python: nessun finding MEDIUM+. "
            "Copertura limitata a Python e ai pattern noti dello scanner.",
            confidence=0.8,
            evidence={"scanner": "bandit", "findings": 0, "python_files": len(py_files)},
        )


def coverage_gaps(verdicts: Sequence[ReviewVerdict], axes: Sequence[str] = AXES) -> List[str]:
    """Assi rimasti senza un verdetto CONCLUSIVO (pass/fail).

    Usato dal futuro Aggregator/Budgeter: `needs_evidence` conta come copertura
    mancante, non come esito. Nessuna promozione con copertura incompleta (§4.5).
    """
    decided = {v.axis for v in verdicts if v.conclusive}
    return [axis for axis in axes if axis not in decided]
