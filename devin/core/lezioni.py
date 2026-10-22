"""Lezioni dagli errori e richieste di miglioria: DEVIN impara e chiede (22/10).

Due cose che Alessandro ha chiesto il 19/10, guardando DEVIN sbagliare
``radians`` e ``extrude`` nel modellatore 3D:

- **Lezioni.** Quando DEVIN capisce perche' qualcosa e' fallito e l'ha
  risolto, chiama ``ricorda_lezione`` (causa + regola). La prova non la
  scrive lui: il backend cerca nella conversazione un risultato di errore e un
  successo successivo dello stesso strumento. Con la prova la lezione e'
  *verificata* e da subito entra nelle istruzioni delle risposte successive;
  senza prova resta *da confermare* finche' Alessandro non la conferma
  dall'app. (Memoria anti-contaminazione: entra solo cio' che e' verificato o
  confermato da una persona; vedi AGENTS.md.)
- **Richieste di miglioria.** Se gli manca uno strumento, una libreria o una
  funzione, chiama ``richiedi_miglioria`` con cosa, perche', come installarla
  e le fonti. Le fonti devono essere pagine che ha davvero aperto in questa
  conversazione: un link inventato viene respinto. Alessandro approva o
  rifiuta dall'app; un'approvazione finisce nel journal per chi la realizza.

Archivio: due file JSONL in ``~/.local/share/devin`` (``DEVIN_LEZIONI_DIR``).
"""
from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from devin.core.sorvegliante import firma_errore

MAX_TESTO = 400
MAX_IN_PROMPT = 15
STATI_LEZIONE = {"verificata", "da_confermare", "confermata", "scartata"}
ATTIVE = {"verificata", "confermata"}
STATI_RICHIESTA = {"in_attesa", "approvata", "rifiutata"}
NOMI = {"ricorda_lezione", "richiedi_miglioria"}

STRUMENTI: List[Dict[str, Any]] = [
    {"type": "function", "function": {
        "name": "ricorda_lezione",
        "description": ("Quando hai capito PERCHE' qualcosa e' fallito e l'hai risolto, ricordalo per le "
                        "prossime volte: causa (cosa era sbagliato) e regola (cosa fare d'ora in poi, una "
                        "frase). La prova la trova il sistema nella conversazione (l'errore e il successo "
                        "dopo la correzione). Non usarlo per supposizioni."),
        "parameters": {"type": "object", "properties": {
            "causa": {"type": "string", "description": "perche' e' fallito, in una o due frasi"},
            "regola": {"type": "string", "description": "cosa fare d'ora in poi, una frase"}},
            "required": ["causa", "regola"], "additionalProperties": False}}},
    {"type": "function", "function": {
        "name": "richiedi_miglioria",
        "description": ("Se ti manca uno strumento, una libreria o una funzione per fare bene il lavoro, "
                        "chiedilo ad Alessandro: cos'e' e cosa fa, perche' ti serve (quale limite o errore), "
                        "come si installa o si aggiunge, e le fonti. Le fonti devono essere pagine che hai "
                        "davvero aperto in questa conversazione; non inventare link. Alessandro approva o "
                        "rifiuta dall'app; tu intanto prosegui con quello che hai."),
        "parameters": {"type": "object", "properties": {
            "titolo": {"type": "string"},
            "cosa": {"type": "string", "description": "cos'e' e cosa fa"},
            "perche": {"type": "string", "description": "a cosa ti serve, quale limite hai incontrato"},
            "come": {"type": "string", "description": "come si installa o si aggiunge"},
            "fonti": {"type": "array", "items": {"type": "string"},
                      "description": "URL di pagine aperte in questa conversazione"}},
            "required": ["titolo", "cosa", "perche", "come"], "additionalProperties": False}}},
]

_LOCK = threading.Lock()
_URL = re.compile(r"https?://[^\s<>\"')\]]+")
_EXIT = re.compile(r"^exit\s+(-?\d+)")


def cartella() -> Path:
    return Path(os.environ.get("DEVIN_LEZIONI_DIR") or os.path.expanduser("~/.local/share/devin"))


def _file(nome: str) -> Path:
    return cartella() / nome


def _adesso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _leggi(nome: str) -> List[Dict[str, Any]]:
    percorso = _file(nome)
    if not percorso.exists():
        return []
    voci = []
    for riga in percorso.read_text(encoding="utf-8").splitlines():
        try:
            voce = json.loads(riga)
        except ValueError:
            continue
        if isinstance(voce, dict) and voce.get("id"):
            voci.append(voce)
    return voci


def _scrivi(nome: str, voci: Iterable[Dict[str, Any]]) -> None:
    percorso = _file(nome)
    percorso.parent.mkdir(parents=True, exist_ok=True)
    temporaneo = percorso.with_suffix(".tmp")
    temporaneo.write_text("".join(json.dumps(v, ensure_ascii=False) + "\n" for v in voci), encoding="utf-8")
    os.replace(temporaneo, percorso)


def _breve(testo: Any, limite: int = MAX_TESTO) -> str:
    return " ".join(str(testo or "").split())[:limite]


def _normale(testo: str) -> str:
    return re.sub(r"\W+", " ", testo.lower()).strip()


# --- la prova --------------------------------------------------------------

def e_errore(risultato: str) -> bool:
    testo = str(risultato or "").strip()
    uscita = _EXIT.match(testo)
    return firma_errore(testo) is not None or bool(uscita and uscita.group(1) != "0")


def risultati_strumenti(messaggi: List[Dict[str, Any]]) -> List[Tuple[str, str]]:
    """(nome strumento, risultato) in ordine, dalla conversazione in formato API."""
    nomi: Dict[str, str] = {}
    uscita = []
    for messaggio in messaggi:
        for chiamata in messaggio.get("tool_calls") or []:
            nomi[str(chiamata.get("id"))] = str((chiamata.get("function") or {}).get("name") or "")
        if messaggio.get("role") == "tool":
            nome = nomi.get(str(messaggio.get("tool_call_id")), "")
            if nome and nome not in NOMI:
                uscita.append((nome, str(messaggio.get("content") or "")))
    return uscita


def trova_prova(messaggi: List[Dict[str, Any]]) -> Optional[Dict[str, str]]:
    """L'ultimo errore seguito da un successo dello stesso strumento, o None."""
    ultimo_errore: Dict[str, str] = {}
    prova = None
    for nome, risultato in risultati_strumenti(messaggi):
        if e_errore(risultato):
            ultimo_errore[nome] = risultato
        elif nome in ultimo_errore:
            prova = {"strumento": nome, "errore": ultimo_errore.pop(nome)[:600], "successo": risultato[:300]}
    return prova


# --- lezioni ---------------------------------------------------------------

def ricorda(argomenti: Dict[str, Any], messaggi: List[Dict[str, Any]], origine: str) -> str:
    causa, regola = _breve(argomenti.get("causa")), _breve(argomenti.get("regola"))
    if not causa or not regola:
        return "ERRORE: servono causa e regola"
    prova = trova_prova(messaggi)
    with _LOCK:
        lezioni = _leggi("lezioni.jsonl")
        for lezione in lezioni:
            if lezione.get("stato") != "scartata" and _normale(lezione.get("regola", "")) == _normale(regola):
                return "Questa regola la conosci gia': non serve ricordarla due volte."
        lezione = {"id": uuid.uuid4().hex[:12], "quando": _adesso(), "causa": causa, "regola": regola,
                   "stato": "verificata" if prova else "da_confermare", "origine": origine, **(prova or {})}
        lezioni.append(lezione)
        _scrivi("lezioni.jsonl", lezioni)
    print(f"[Lezioni] {lezione['stato']}: {regola[:160]}")
    if prova:
        return (f"Lezione verificata e ricordata (prova: errore e poi successo di {prova['strumento']}). "
                "Da ora la trovi nelle tue istruzioni.")
    return ("Lezione salvata come DA CONFERMARE: nella conversazione non c'e' un errore seguito da un "
            "successo dello stesso strumento. Entra nelle tue istruzioni solo se Alessandro la conferma.")


def blocco_prompt(limite: int = MAX_IN_PROMPT) -> str:
    """Le lezioni attive, per le istruzioni: vuoto se non ce ne sono."""
    attive = [l for l in _leggi("lezioni.jsonl") if l.get("stato") in ATTIVE][-limite:]
    if not attive:
        return ""
    righe = "\n".join(f"- {l['regola']} (perche': {l['causa']})" for l in attive)
    return ("LEZIONI DAI TUOI ERRORI PASSATI (ognuna nasce da un errore vero e dalla correzione che "
            "l'ha risolto, o e' confermata da Alessandro). Applicale:\n" + righe)


# --- richieste di miglioria --------------------------------------------------

def url_aperti(messaggi: List[Dict[str, Any]]) -> set:
    aperti = set()
    for _nome, risultato in risultati_strumenti(messaggi):
        aperti.update(u.rstrip(".,;:") for u in _URL.findall(risultato))
    return aperti


def richiedi(argomenti: Dict[str, Any], messaggi: List[Dict[str, Any]], origine: str) -> str:
    campi = {k: _breve(argomenti.get(k), 200 if k == "titolo" else 800) for k in ("titolo", "cosa", "perche", "come")}
    mancanti = [k for k, v in campi.items() if not v]
    if mancanti:
        return f"ERRORE: mancano {', '.join(mancanti)}"
    fonti = [str(f).strip().rstrip("/") for f in (argomenti.get("fonti") or []) if str(f).strip()][:8]
    aperti = {u.rstrip("/") for u in url_aperti(messaggi)}
    non_aperte = [f for f in fonti if f not in aperti]
    if non_aperte:
        return ("ERRORE: queste fonti non le hai aperte in questa conversazione: " + ", ".join(non_aperte)
                + ". Aprile prima (cerca_web) o toglile: si citano solo pagine lette davvero.")
    with _LOCK:
        richieste = _leggi("richieste_miglioria.jsonl")
        for richiesta in richieste:
            if richiesta.get("stato") == "in_attesa" and _normale(richiesta.get("titolo", "")) == _normale(campi["titolo"]):
                return "Questa richiesta e' gia' in attesa della risposta di Alessandro."
        richieste.append({"id": uuid.uuid4().hex[:12], "quando": _adesso(), **campi, "fonti": fonti,
                          "stato": "in_attesa", "origine": origine})
        _scrivi("richieste_miglioria.jsonl", richieste)
    print(f"[Miglioria] richiesta: {campi['titolo'][:160]}")
    return ("Richiesta inviata ad Alessandro" + ("" if fonti else " (senza fonti)")
            + ": la vede nell'app e la approva o la rifiuta. Intanto prosegui con quello che hai.")


# --- per l'app ---------------------------------------------------------------

def elenco() -> Dict[str, Any]:
    return {"lezioni": list(reversed(_leggi("lezioni.jsonl"))),
            "richieste": list(reversed(_leggi("richieste_miglioria.jsonl")))}


def cambia_stato(tipo: str, voce_id: str, stato: str) -> Dict[str, Any]:
    nome, stati = (("lezioni.jsonl", STATI_LEZIONE) if tipo == "lezione"
                   else ("richieste_miglioria.jsonl", STATI_RICHIESTA) if tipo == "richiesta" else (None, None))
    if nome is None:
        return {"error": f"tipo sconosciuto: {tipo!r}"}
    if stato not in stati:
        return {"error": f"stato non valido per {tipo}: {stato!r}"}
    with _LOCK:
        voci = _leggi(nome)
        trovata = next((v for v in voci if v.get("id") == voce_id), None)
        if trovata is None:
            return {"error": "voce non trovata"}
        trovata["stato"] = stato
        trovata["deciso"] = _adesso()
        _scrivi(nome, voci)
    etichetta = trovata.get("titolo") or trovata.get("regola") or ""
    print(f"[{'Miglioria' if tipo == 'richiesta' else 'Lezioni'}] {stato}: {etichetta[:200]}")
    return {"status": "ok", "voce": trovata}


def esegui(nome: str, argomenti: Dict[str, Any], messaggi: List[Dict[str, Any]], origine: str) -> str:
    """Esegue uno dei due strumenti sul rig (non sul PC)."""
    if not isinstance(argomenti, dict):
        return "ERRORE: argomenti non validi"
    if nome == "ricorda_lezione":
        return ricorda(argomenti, messaggi, origine)
    if nome == "richiedi_miglioria":
        return richiedi(argomenti, messaggi, origine)
    return f"ERRORE: strumento sconosciuto {nome}"
