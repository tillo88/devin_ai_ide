"""Ambienti Python in cui DEVIN prova il codice di un progetto (test, comandi, prova dell'app).

28/10 (BigCodeBench, scelta B di Alessandro): i compiti di BigCodeBench usano oltre 70
librerie a versioni precise, che non vanno nel venv del backend. Stanno in un venv
dedicato, installato sul rig da un lock tracciato. Quando il training lavora su quei
compiti, TUTTO cio' che esegue il codice del progetto (gate dei test, verifica dei gold,
comandi dell'agente, prova dell'app) deve usare quell'interprete, non sys.executable:
un test rosso perche' manca pandas non e' un errore di DEVIN.

Un solo punto decide: ``python_corrente()``. Il training ci entra con
``with usa("bigcodebench"):`` attorno al caso; fuori resta sys.executable. Il valore e'
un ContextVar: gate, agente e prova dell'app girano nello stesso thread del caso.
Gli ambienti sono un elenco chiuso di NOMI, mai percorsi presi dai dati di un caso.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
import os
from pathlib import Path
import sys
from typing import Dict, Iterator, Optional

# nome -> venv (relativo alla home dell'utente che fa girare DEVIN)
AMBIENTI: Dict[str, str] = {
    "bigcodebench": ".local/share/devin/venv-bigcodebench",
}

_corrente: ContextVar[Optional[str]] = ContextVar("devin_ambiente_di_prova", default=None)


class AmbienteMancante(RuntimeError):
    pass


def cartella(nome: str, home: Optional[Path] = None) -> Path:
    if nome not in AMBIENTI:
        raise AmbienteMancante(f"ambiente sconosciuto: {nome!r}")
    return Path(home or Path.home()) / AMBIENTI[nome]


def interprete(nome: Optional[str], home: Optional[Path] = None) -> str:
    """Il python di un ambiente (nessun nome: quello di DEVIN). Mai un ripiego silenzioso."""
    if not nome:
        return sys.executable
    python = cartella(nome, home) / "bin" / "python"
    if not python.is_file():
        raise AmbienteMancante(f"ambiente {nome!r} non installato: manca {python}")
    return str(python)


def nome_corrente() -> Optional[str]:
    return _corrente.get()


def python_corrente() -> str:
    return interprete(_corrente.get())


def env_corrente(env: Dict[str, str]) -> Dict[str, str]:
    """L'ambiente dei processi: con un venv attivo, il suo bin/ in testa al PATH (pytest, pip...)."""
    nome = _corrente.get()
    if not nome:
        return env
    venv = cartella(nome)
    nuovo = dict(env)
    nuovo["VIRTUAL_ENV"] = str(venv)
    nuovo["PATH"] = str(venv / "bin") + os.pathsep + env.get("PATH", os.environ.get("PATH", ""))
    return nuovo


def entra(nome: Optional[str]) -> Token:
    """Come ``usa``, per chi non puo' usare un blocco with: restituisce il token per ``esci``."""
    interprete(nome)
    return _corrente.set(nome or None)


def esci(token: Optional[Token]) -> None:
    if token is not None:
        _corrente.reset(token)


@contextmanager
def usa(nome: Optional[str]) -> Iterator[str]:
    """Dentro il blocco il codice del progetto gira nell'ambiente ``nome`` (None: quello di DEVIN)."""
    python = interprete(nome)          # se manca, si dice subito, prima di far lavorare il modello
    token = _corrente.set(nome or None)
    try:
        yield python
    finally:
        _corrente.reset(token)
