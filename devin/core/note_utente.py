"""Note dell'utente mentre DEVIN lavora: «aspetta, hai frainteso», «aggiungi anche...».

Una coda per conversazione (chat) o per run (agente). Si accettano note solo
mentre qualcuno le puo' leggere: una chat che sta rispondendo, un run con
l'agente in corso. Fuori da li' la nota sarebbe persa in silenzio, quindi si
rifiuta e lo si dice.
"""
from __future__ import annotations

import threading
from typing import Dict, List

MAX_NOTA = 4000
MAX_IN_CODA = 10

_LOCK = threading.Lock()
_CODE: Dict[str, List[str]] = {}
_ATTIVE: Dict[str, int] = {}


class NotaRifiutata(ValueError):
    pass


def chiave_chat(progetto: str, chat_id: str | None) -> str:
    return f"chat:{progetto}:{chat_id or ''}"


def chiave_run(run_id: str) -> str:
    return f"run:{run_id}"


def apri(chiave: str) -> None:
    with _LOCK:
        _ATTIVE[chiave] = _ATTIVE.get(chiave, 0) + 1
        _CODE.setdefault(chiave, [])


def chiudi(chiave: str) -> None:
    with _LOCK:
        rimaste = _ATTIVE.get(chiave, 0) - 1
        if rimaste > 0:
            _ATTIVE[chiave] = rimaste
        else:
            _ATTIVE.pop(chiave, None)
            _CODE.pop(chiave, None)


def attiva(chiave: str) -> bool:
    with _LOCK:
        return chiave in _ATTIVE


def aggiungi(chiave: str, testo: str) -> int:
    testo = str(testo or "").strip()
    if not testo:
        raise NotaRifiutata("nota vuota")
    with _LOCK:
        if chiave not in _ATTIVE:
            raise NotaRifiutata("DEVIN non sta lavorando qui adesso: scrivi un messaggio normale")
        coda = _CODE.setdefault(chiave, [])
        if len(coda) >= MAX_IN_CODA:
            raise NotaRifiutata(f"troppe note in attesa ({MAX_IN_CODA}): aspetta che DEVIN le legga")
        coda.append(testo[:MAX_NOTA])
        return len(coda)


def prendi(chiave: str) -> List[str]:
    with _LOCK:
        coda = _CODE.get(chiave)
        if not coda:
            return []
        note = list(coda)
        coda.clear()
        return note


def formatta(note: List[str]) -> str:
    """Come la nota arriva al modello."""
    corpo = "\n".join(f"- {n}" for n in note)
    return ("[NOTA DELL'UTENTE, scritta mentre stavi lavorando: tienine conto subito. Se cambia "
            "qualcosa di quello che stavi facendo o pensando, correggi la rotta; poi prosegui.]\n" + corpo)
