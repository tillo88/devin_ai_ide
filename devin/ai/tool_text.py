"""Chiamate di strumenti scritte come TESTO invece che come chiamate vere.

Capita (17/10, chat "gestione turni"): il modello, dopo un lungo ragionamento,
scrive nella risposta

    <tool_call>
    <function=cerca_web>
    <parameter=query>
    schema turni ...
    </parameter>
    </function>
    </tool_call>

e il server non lo trasforma in ``tool_calls``: la ricerca non parte e l'utente
vede il markup. Qui si riconoscono i due formati che i modelli usano
(XML stile Qwen-Coder e JSON stile Hermes dentro ``<tool_call>``) e si
ricavano chiamate vere, solo per strumenti effettivamente offerti.

Il modello sbaglia spesso anche il NOME del parametro (``query`` invece di
``domanda``): ``adatta_argomenti`` lo riporta allo schema quando non c'e'
ambiguita' (un solo parametro atteso, un solo valore dato).
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Tuple

APRE = "<tool_call>"
_BLOCCO = re.compile(r"<tool_call>\s*(.*?)\s*(?:</tool_call>|\Z)", re.S)
_FUNZIONE_XML = re.compile(r"<function=([^>\s]+)>\s*(.*?)\s*(?:</function>|\Z)", re.S)
_PARAMETRO_XML = re.compile(r"<parameter=([^>\s]+)>\n?(.*?)\n?\s*(?:</parameter>|(?=<parameter=)|\Z)", re.S)


def _schemi(tools) -> Dict[str, Dict[str, Any]]:
    schemi = {}
    for tool in tools or []:
        funzione = (tool or {}).get("function") or {}
        if funzione.get("name"):
            schemi[funzione["name"]] = funzione.get("parameters") or {}
    return schemi


def _valore(testo: str, schema: Dict[str, Any]) -> Any:
    if (schema or {}).get("type") in (None, "string"):
        return testo
    try:
        return json.loads(testo)
    except ValueError:
        return testo


def adatta_argomenti(argomenti: Dict[str, Any], schema: Dict[str, Any]) -> Dict[str, Any]:
    """Nomi di parametro sbagliati -> quelli dello schema, solo se non c'e' ambiguita'."""
    proprieta = (schema or {}).get("properties") or {}
    if not proprieta or not isinstance(argomenti, dict) or all(k in proprieta for k in argomenti):
        return argomenti
    richiesti = [k for k in (schema.get("required") or []) if k in proprieta]
    attesi = richiesti or list(proprieta)
    estranei = [k for k in argomenti if k not in proprieta]
    mancanti = [k for k in attesi if k not in argomenti]
    if len(estranei) == 1 and len(mancanti) == 1:
        corretti = {k: v for k, v in argomenti.items() if k in proprieta}
        corretti[mancanti[0]] = argomenti[estranei[0]]
        return corretti
    return argomenti


def risolvi_nome(nome: str, schemi: Dict[str, Any]) -> str:
    """Nome esatto, oppure l'unico strumento che corrisponde senza prefisso/maiuscole."""
    if nome in schemi:
        return nome
    corto = nome.split("__")[-1].lower()
    candidati = [n for n in schemi if n.lower() == nome.lower() or n.split("__")[-1].lower() == corto]
    return candidati[0] if len(candidati) == 1 else nome


def estrai_chiamate(testo: str, tools) -> Tuple[str, List[Dict[str, Any]]]:
    """(testo senza i blocchi riconosciuti, chiamate nel formato tool_calls dell'API)."""
    schemi = _schemi(tools)
    if APRE not in (testo or "") or not schemi:
        return testo, []
    chiamate: List[Dict[str, Any]] = []
    tagli: List[Tuple[int, int]] = []
    for blocco in _BLOCCO.finditer(testo):
        corpo = blocco.group(1)
        nome, argomenti = "", None
        funzione = _FUNZIONE_XML.search(corpo)
        if funzione:
            nome = funzione.group(1).strip()
            schema = schemi.get(nome, {})
            proprieta = schema.get("properties") or {}
            argomenti = {m.group(1).strip(): _valore(m.group(2).strip(), proprieta.get(m.group(1).strip()))
                         for m in _PARAMETRO_XML.finditer(funzione.group(2))}
            if not argomenti:
                # <function=x>{"a": 1}</function> o argomenti vuoti scritti come {}
                corpo_json = re.sub(r"</?parameter[^>]*>", "", funzione.group(2)).strip()
                try:
                    dati_json = json.loads(corpo_json) if corpo_json else {}
                    argomenti = dati_json if isinstance(dati_json, dict) else {}
                except ValueError:
                    argomenti = {}
        else:
            try:
                dati = json.loads(corpo)
            except ValueError:
                dati = None
            if isinstance(dati, dict):
                nome = str(dati.get("name") or "")
                argomenti = dati.get("arguments", dati.get("parameters", {}))
                if isinstance(argomenti, str):
                    try:
                        argomenti = json.loads(argomenti)
                    except ValueError:
                        argomenti = None
        if not nome or not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", nome):
            continue
        nome = risolvi_nome(nome, schemi)
        if not isinstance(argomenti, dict):
            argomenti = {}
        # Anche un nome inesistente diventa una chiamata: chi la esegue risponde
        # "non esiste, ecco quelli disponibili" e il modello si corregge, invece
        # di lasciare il markup in chat (17/10: github__get_user al posto di get_me).
        argomenti = adatta_argomenti(argomenti, schemi.get(nome, {}))
        chiamate.append({"id": f"call_testo_{len(chiamate)}", "type": "function",
                         "function": {"name": nome, "arguments": json.dumps(argomenti, ensure_ascii=False)}})
        tagli.append(blocco.span())
    for inizio, fine in reversed(tagli):
        testo = testo[:inizio] + testo[fine:]
    return testo.strip() if tagli else testo, chiamate
