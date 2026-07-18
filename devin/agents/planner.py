import ast
import json
import re
from pathlib import PurePosixPath
from dataclasses import dataclass, field
from typing import List, Dict, Any
from devin.ai.client import AIClient
from devin.ai.web_search import search_coding_context, is_searchable_error
from devin.agents.prompts import PLANNER_SYSTEM_PROMPT, SCAFFOLD_PLANNER_SYSTEM_PROMPT


@dataclass
class Plan:
    """Struttura dati per il piano richiesta dall'Orchestrator."""
    steps: List[str] = field(default_factory=list)
    raw_response: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "steps": self.steps,
            "raw_response": self.raw_response
        }


_PLAN_CONTAINER_KEYS = ("files", "file_plan", "plan", "items")


def _safe_plan_filename(value: Any) -> str:
    """Normalizza un path LLM e scarta assoluti/traversal prima dell'Orchestrator."""
    if not isinstance(value, str):
        return ""
    value = value.strip().replace("\\", "/")
    if not value or "\x00" in value or re.match(r"^[A-Za-z]:/", value):
        return ""
    path = PurePosixPath(value)
    if str(path) == "." or path.is_absolute() or ".." in path.parts or value.endswith("/"):
        return ""
    return str(path)


def _normalise_file_plan(data: Any) -> List[Dict[str, str]]:
    """Accetta sia l'array canonico sia wrapper comuni come {\"files\": [...]} ."""
    if isinstance(data, dict):
        for key in _PLAN_CONTAINER_KEYS:
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            return []
    if not isinstance(data, list):
        return []

    out = []
    seen = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        filename = _safe_plan_filename(item.get("filename") or item.get("path"))
        spec = item.get("spec") or item.get("description") or item.get("purpose") or ""
        if not filename or filename in seen or not isinstance(spec, str) or not spec.strip():
            continue
        seen.add(filename)
        out.append({"filename": filename, "spec": spec.strip()})
    return out


def _decode_plan_candidate(candidate: str) -> Any:
    candidate = candidate.strip().lstrip("\ufeff")
    if not candidate:
        return None
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    without_trailing_commas = re.sub(r",\s*([}\]])", r"\1", candidate)
    try:
        return json.loads(without_trailing_commas)
    except json.JSONDecodeError:
        pass

    try:
        return ast.literal_eval(candidate)
    except (ValueError, SyntaxError):
        return None


def _parse_file_plan(raw: str) -> tuple[List[Dict[str, str]], str]:
    """Ritorna (piano, diagnostica) tollerando fence, wrapper e testo extra."""
    if not raw or not raw.strip():
        return [], "risposta vuota"

    text = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL | re.IGNORECASE).strip()
    candidates = [m.group(1) for m in re.finditer(
        r"```(?:json|python)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE
    )]
    candidates.append(text)

    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char not in "[{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        plan = _normalise_file_plan(value)
        if plan:
            return plan, ""

    errors = []
    for candidate in candidates:
        value = _decode_plan_candidate(candidate)
        if value is None:
            errors.append("JSON non decodificabile")
            continue
        plan = _normalise_file_plan(value)
        if plan:
            return plan, ""
        errors.append("schema senza file validi")
    return [], "; ".join(dict.fromkeys(errors)) or "nessun candidato JSON"


def _extract_file_plan(raw: str) -> List[Dict[str, str]]:
    """
    Estrae una lista [{"filename": ..., "spec": ...}, ...] dalla risposta LLM per lo
    Zero-Shot Scaffolding. Tollerante a markdown-fence e testo extra prima/dopo il JSON.
    Ritorna lista vuota se non trova nulla di valido (mai un'eccezione verso il caller).
    """
    return _parse_file_plan(raw)[0]


class Planner:
    def __init__(self, ai_client: AIClient, config: Dict[str, Any] = None):
        """Inizializza il Planner usando il client gestito dall'orchestratore."""
        self.client = ai_client
        self.config = config or {}
        self.last_scaffold_attempts: List[Dict[str, Any]] = []

    def plan(self, task: str, context: str) -> Plan:
        """
        Genera un piano di esecuzione step-by-step basato su task e contesto.
        Usato in Modalità 1 (Mantenimento): patching su codice esistente.
        """
        messages = [
            {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"""
TASK RICHIESTO:
{task}

CONTESTO DEL PROGETTO (file esistenti, se presenti):
{context if context.strip() else "(nessun file presente — progetto vuoto, da costruire da zero)"}

Crea un piano di esecuzione step-by-step per soddisfare il task richiesto.
"""
            }
        ]

        # Utilizza l'istanza del client passata (configurata dinamicamente con i giusti URL)
        response = self.client.local(messages, mode="reasoning", timeout=60)

        steps = []
        raw_text = ""

        # Gestione della risposta per estrarre la lista degli step
        if isinstance(response, str):
            raw_text = response
            try:
                # Se l'LLM risponde in JSON strutturato
                data = json.loads(response)
                if isinstance(data, dict):
                    steps = data.get("steps", [raw_text])
                elif isinstance(data, list):
                    steps = data
            except json.JSONDecodeError:
                # Fallback: Se risponde in testo/Markdown, estrae le righe puntate o numerate
                steps = [
                    line.strip().lstrip("-*0123456789. ")
                    for line in response.split("\n")
                    if line.strip() and (line.strip().startswith("-") or line.strip().startswith("*") or line.strip()[0].isdigit())
                ]
                if not steps:
                    steps = [response]
        elif isinstance(response, dict):
            steps = response.get("steps", [])
            raw_text = json.dumps(response)
        else:
            raw_text = str(response)
            steps = [raw_text]

        return Plan(steps=steps, raw_response=raw_text)

    def plan_scaffold(self, task: str) -> List[Dict[str, str]]:
        """
        Modalità 2 (Zero-Shot Scaffolding): genera l'elenco dei file da creare da zero,
        in ordine di creazione, con una spec concreta per ciascuno.
        Ritorna lista vuota (mai eccezione) se il parsing fallisce — il caller
        (Orchestrator.run_scaffold) deve gestire questo caso come errore recuperabile.
        
        2026-07-14: Aggiunta Web Search prima della pianificazione per evitare
        che il modello inventi API o fonti non verificate (come visto nel caso Steam Checker).
        """
        # Ricerca web se il task richiede documentazione/API esterne
        web_context = ""
        task_lower = task.lower()
        search_triggers = [
            "api", "documentation", "official", "docs", "endpoint", "sdk",
            "steam", "github", "library", "framework", "service", "integration"
        ]
        if any(trigger in task_lower for trigger in search_triggers):
            try:
                ws_cfg = self.config.get("web_search", {})
                if ws_cfg.get("agent_search", {}).get("enabled", True):
                    # Estrai termini di ricerca chiave dal task
                    search_terms = task.split()[:5]  # Prime 5 parole come query base
                    search_query = " ".join(search_terms)
                    web_context = search_coding_context(search_query, self.config, max_chars=3000)
                    if web_context:
                        web_context = f"\n\nWEB RESEARCH (verified sources):\n{web_context}\n"
            except Exception as e:
                # Fail-soft: se la ricerca fallisce, procedi senza
                pass
        
        messages = [
            {"role": "system", "content": SCAFFOLD_PLANNER_SYSTEM_PROMPT},
            {"role": "user", "content": f"TASK: {task}{web_context}\n\nGenera l'elenco file in formato JSON."}
        ]
        self.last_scaffold_attempts = []
        raw = self.client.local(messages, mode="reasoning", timeout=150) or ""
        plan, error = _parse_file_plan(raw)
        self.last_scaffold_attempts.append({"attempt": 1, "raw": raw, "error": error})
        if plan:
            return plan

        repair_messages = [
            {
                "role": "system",
                "content": (
                    "You repair a file plan. Output ONLY valid JSON as an array of "
                    "objects with non-empty string fields filename and spec. Preserve "
                    "the intended files, remove prose, markdown and unsafe paths."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"ORIGINAL TASK:\n{task[:5000]}\n\n"
                    f"INVALID OR INCOMPLETE OUTPUT:\n{raw[:10000] or '(empty response)'}"
                ),
            },
        ]
        repaired = self.client.local(repair_messages, mode="reasoning", timeout=150) or ""
        plan, repair_error = _parse_file_plan(repaired)
        self.last_scaffold_attempts.append({"attempt": 2, "raw": repaired, "error": repair_error})
        return plan
