"""Federated Evidence Council — reviewer basato su modello, MODEL-AGNOSTIC.

Design: `docs/devin_federated_council_design_v1.md` §4.1 (`LocalModelReviewer`).

**Nessun modello e' hardcodato qui.** Il reviewer riceve una callable `chat` e
(opzionale) una `model_identity`: quale modello risponda e' una decisione di
configurazione, non di codice. Questo e' deliberato — il modello operativo e' un
*placeholder* finche' la Model Evaluation Suite non avra' scelto il candidato
migliore, e il Council deve sopravvivere a quel cambio senza modifiche.

Tre proprieta' che il cambio-modello rende necessarie:

1. **Provenance del modello.** Ogni verdetto registra *quale* modello ha
   risposto, catturato al momento della review. Un giudizio prodotto dal modello
   X non deve poter essere attribuito al modello Y dopo uno swap: senza questo,
   i `reviews.jsonl` diventano inconfrontabili proprio mentre stai valutando piu'
   candidati.
2. **`family` viene dalla configurazione.** La regola "no duplicati di famiglia
   sullo stesso asse" (router) protegge dagli errori correlati. Se si cambia
   modello ma si lascia la vecchia `family`, quella protezione si rompe **in
   silenzio**: due reviewer che sembrano indipendenti sono lo stesso modello.
   Regola operativa: **la family segue il modello, non il ruolo.**
3. **Fail-soft, mai fail-open.** Modello irraggiungibile, risposta vuota, JSON
   non parsabile o verdetto fuori contratto -> `needs_evidence`. Un modello che
   non risponde non produce un pass.

Nota di prompt design: al reviewer viene detto esplicitamente che
`needs_evidence` e' una risposta **legittima e non penalizzata**. Forzare una
scelta binaria su evidenza insufficiente e' il modo piu' rapido per ottenere
verdetti sicuri e sbagliati.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence

from devin.core.council import (
    AXIS_LENS,
    VERDICT_FAIL,
    VERDICT_NEEDS_EVIDENCE,
    VERDICT_PASS,
    CouncilError,
    ReviewerAdapter,
    ReviewPacket,
    ReviewVerdict,
)

# `chat(messages) -> str | None`
ChatFn = Callable[[List[Dict[str, str]]], Optional[str]]

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)
_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.MULTILINE)

MAX_FIELD_CHARS = 4000       # tetto difensivo sul contesto speso per campo


@dataclass
class ModelReviewSpec:
    """Identita' e portata del reviewer. Tutto configurabile, niente hardcode."""

    reviewer_id: str
    family: str
    supported_axes: Sequence[str]

    def __post_init__(self) -> None:
        if not str(self.reviewer_id).strip():
            raise CouncilError("reviewer_id obbligatorio")
        if not str(self.family).strip():
            raise CouncilError(
                "family obbligatoria: la regola no-duplicati-di-famiglia dipende da questa. "
                "Deve seguire il MODELLO, non il ruolo."
            )
        if not self.supported_axes:
            raise CouncilError("supported_axes non puo' essere vuoto")
        for axis in self.supported_axes:
            if axis not in AXIS_LENS:
                raise CouncilError(f"asse sconosciuto: {axis!r}")


def _clip(value: Any, limit: int = MAX_FIELD_CHARS) -> str:
    text = "" if value is None else str(value)
    return text if len(text) <= limit else text[:limit] + f"\n[...troncato, {len(text)} caratteri]"


def _balanced_objects(text: str):
    """Yield delle sottostringhe `{...}` a graffe bilanciate (stringhe escluse).

    Serve perche' i modelli intercalano prosa e JSON, a volte piu' di un oggetto:
    una regex greedy `\\{.*\\}` prenderebbe dal primo `{` all'ultimo `}` e
    fallirebbe. Qui si scandisce rispettando stringhe ed escape.
    """
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for i, ch in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth:
                depth -= 1
                if depth == 0 and start >= 0:
                    yield text[start:i + 1]
                    start = -1


def extract_json(raw: str) -> Optional[Dict[str, Any]]:
    """Estrae il primo oggetto JSON valido da una risposta, tollerando fence e prosa."""
    if not raw:
        return None
    cleaned = _FENCE.sub("", raw).strip()
    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, ValueError):
        pass
    for candidate in _balanced_objects(cleaned):
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(data, dict):
            return data
    return None


class LocalModelReviewer(ReviewerAdapter):
    """Reviewer semantico servito da un modello, qualunque esso sia.

    `chat` riceve una lista di messaggi in stile OpenAI e ritorna il testo della
    risposta (o None). `model_identity` ritorna il nome del modello REALMENTE
    servito, per la provenance; se assente, si registra "unknown".
    """

    def __init__(
        self,
        chat: ChatFn,
        spec: ModelReviewSpec,
        *,
        model_identity: Optional[Callable[[], Optional[str]]] = None,
        include_mechanical_status: bool = True,
    ):
        if not callable(chat):
            raise CouncilError("chat deve essere una callable(messages) -> str|None")
        self._chat = chat
        self._spec = spec
        self._model_identity = model_identity
        # Anti-anchoring: l'esito meccanico e' evidenza legittima, ma mostrarlo
        # spinge il reviewer ad allinearsi (piu' pass se il gate diceva success).
        # Mettere False per un giudizio davvero indipendente — utile soprattutto
        # quando si CONFRONTANO modelli: un bias comune falserebbe il confronto.
        self.include_mechanical_status = bool(include_mechanical_status)
        self.reviewer_id = spec.reviewer_id
        self.family = spec.family
        self.supported_axes = tuple(spec.supported_axes)

    # --- provenance -------------------------------------------------------
    def _identity(self) -> str:
        if not self._model_identity:
            return "unknown"
        try:
            return str(self._model_identity() or "unknown")
        except Exception:
            return "unknown"

    # --- prompt -----------------------------------------------------------
    def build_messages(self, packet: ReviewPacket, axis: str) -> List[Dict[str, str]]:
        case = packet.case or {}
        attempt = packet.attempt or {}
        system = (
            "Sei un reviewer indipendente in un Council di valutazione. Giudichi UN SOLO asse.\n"
            f"ASSE: {axis}\n"
            f"LENTE: {AXIS_LENS.get(axis, '')}\n\n"
            "Regole:\n"
            "- Ragiona prima sul CONCETTO, poi guarda il codice.\n"
            "- Giudica SOLO il tuo asse: se noti problemi su altri assi, ignorali.\n"
            "- 'needs_evidence' e' una risposta LEGITTIMA e non penalizzata: usala quando "
            "l'evidenza non basta per decidere. Non inventare certezza.\n"
            "- Non conosci i giudizi degli altri reviewer e non devi ipotizzarli.\n"
            "- L'esito meccanico (test passati/falliti) e' un DATO, non un giudizio: i test "
            "possono passare su codice concettualmente sbagliato e fallire per motivi "
            "ambientali. Non allinearti a quell'esito per inerzia.\n\n"
            "Rispondi SOLO con un oggetto JSON:\n"
            '{"verdict": "pass|fail|needs_evidence", "reasoning": "<il tuo ragionamento>", '
            '"violations": ["..."], "confidence": 0.0, "proposed_experiment": "<test che '
            'proverebbe o smentirebbe il tuo verdetto, oppure null>"}'
        )
        user = (
            f"COMPITO ASSEGNATO:\n{_clip(case.get('task') or attempt.get('prompt'))}\n\n"
            f"VINCOLI DICHIARATI: {_clip(case.get('expected_signals'), 500)}\n"
            f"TAG: {_clip(case.get('tags'), 300)}\n\n"
            f"RISPOSTA PRODOTTA:\n{_clip(attempt.get('response'))}\n\n"
            f"FILE SCRITTI: {_clip(packet.files_written, 500)}\n"
        )
        if self.include_mechanical_status:
            user += (
                f"\nESITO MECCANICO (dato grezzo, non un verdetto): "
                f"{_clip(attempt.get('status'), 100)}\n"
                f"MOTIVO ERRORE (se presente): {_clip(attempt.get('error_reason'), 800)}\n"
            )
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    # --- review -----------------------------------------------------------
    def review(self, packet: ReviewPacket, axis: str) -> ReviewVerdict:
        if not self.can_review(axis):
            raise CouncilError(
                f"{self.reviewer_id} non copre l'asse {axis!r}: supportati {self.supported_axes}"
            )
        model = self._identity()
        base_evidence: Dict[str, Any] = {"model": model, "axis": axis}

        try:
            raw = self._chat(self.build_messages(packet, axis))
        except Exception as exc:
            return self._verdict(
                axis, VERDICT_NEEDS_EVIDENCE,
                f"Modello non raggiungibile o in errore ({str(exc)[:200]}): nessun giudizio "
                "possibile. Un modello che non risponde non produce un pass.",
                confidence=0.0,
                evidence={**base_evidence, "error": str(exc)[:300]},
            )

        if not raw or not str(raw).strip():
            return self._verdict(
                axis, VERDICT_NEEDS_EVIDENCE,
                "Risposta vuota dal modello: nessun giudizio possibile.",
                confidence=0.0, evidence={**base_evidence, "empty_response": True},
            )

        data = extract_json(raw)
        if data is None:
            return self._verdict(
                axis, VERDICT_NEEDS_EVIDENCE,
                "Risposta del modello non parsabile come JSON: verdetto scartato invece di "
                "essere indovinato.",
                confidence=0.0,
                evidence={**base_evidence, "raw_excerpt": _clip(raw, 500)},
            )

        verdict = str(data.get("verdict") or "").strip().lower()
        reasoning = str(data.get("reasoning") or "").strip()
        if verdict not in (VERDICT_PASS, VERDICT_FAIL, VERDICT_NEEDS_EVIDENCE):
            return self._verdict(
                axis, VERDICT_NEEDS_EVIDENCE,
                f"Verdetto fuori contratto ({verdict!r}): scartato.",
                confidence=0.0,
                evidence={**base_evidence, "raw_verdict": verdict[:100]},
            )
        if not reasoning:
            # Il Council valuta il ragionamento: un verdetto nudo non e' evidenza.
            return self._verdict(
                axis, VERDICT_NEEDS_EVIDENCE,
                "Il modello ha emesso un verdetto senza ragionamento: non utilizzabile "
                "(il Council valuta il ragionamento, non l'etichetta).",
                confidence=0.0,
                evidence={**base_evidence, "raw_verdict": verdict},
            )

        try:
            confidence = float(data.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        confidence = max(0.0, min(1.0, confidence))

        violations = data.get("violations") or []
        if not isinstance(violations, list):
            violations = [str(violations)]
        violations = [str(v)[:300] for v in violations][:20]

        experiment = data.get("proposed_experiment")
        experiment = str(experiment)[:1000] if experiment else None

        return self._verdict(
            axis, verdict, reasoning[:4000],
            confidence=confidence,
            violations=violations,
            proposed_experiment=experiment,
            evidence=base_evidence,
        )


def build_model_reviewer(
    spec: ModelReviewSpec,
    *,
    client: Any = None,
    mode: str = "reasoning",
    config_path: Optional[str] = None,
) -> LocalModelReviewer:
    """Costruisce il reviewer sopra l'`AIClient` esistente, senza hardcode.

    Il modello effettivo lo decide il client (che gia' scopre da `/v1/models`
    quale modello e' realmente servito): qui non si nomina nessun modello.
    """
    if client is None:
        from devin.ai.client import AIClient
        client = AIClient(config_path) if config_path else AIClient()

    def chat(messages: List[Dict[str, str]]) -> Optional[str]:
        return client.ask(messages, mode=mode)

    def identity() -> Optional[str]:
        # nome realmente servito dall'endpoint; fallback all'hint di config
        return (
            getattr(client, "remote_model_actual", None)
            or getattr(client, "remote_reasoning_model", None)
            or None
        )

    return LocalModelReviewer(chat, spec, model_identity=identity)
