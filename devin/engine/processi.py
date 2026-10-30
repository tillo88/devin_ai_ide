"""Processi con un tetto di tempo, chiusi con SIGTERM e mai con SIGKILL (30/10).

``subprocess.run(timeout=...)`` allo scadere manda SIGKILL al solo figlio: e' contro il
guardrail del rig ("SIGTERM limitato, mai SIGKILL") e lascia vivi i nipoti (un Pool, un
server avviato dal test). ``run_limitato`` ha la stessa forma di ``subprocess.run`` ma:

* il figlio parte in una sessione sua (``start_new_session``): il suo gruppo e' solo suo,
  quindi lo si puo' fermare tutto senza toccare il backend;
* allo scadere: SIGTERM al gruppo (``ambienti.termina_gruppo``: nella scatola al codice e
  non a bubblewrap), attesa limitata, poi SIGINT, attesa limitata. Se ancora non esce non
  si insiste: si lascia stare e lo si dice nell'eccezione (nella scatola, quando il caso
  finisce, ``--die-with-parent`` chiude comunque tutto);
* alza ``subprocess.TimeoutExpired`` come prima, cosi' chi chiama non cambia.

Senza ``input`` lo stdin e' /dev/null: un programma che aspetta la tastiera non resta
appeso fino al timeout.
"""
from __future__ import annotations

import os
import signal
import subprocess
from typing import Any, Optional, Sequence

GRAZIA_SECONDI = 10.0


def _ferma(proc: subprocess.Popen, grazia: float) -> str:
    from devin.engine.ambienti import termina_gruppo
    for segnale, attesa in ((signal.SIGTERM, grazia), (signal.SIGINT, grazia / 2)):
        if proc.poll() is not None:
            return ""
        try:
            if os.name != "nt":
                termina_gruppo(proc.pid, segnale)
            else:
                proc.terminate()
        except (ProcessLookupError, PermissionError):
            pass
        try:
            proc.wait(timeout=attesa)
            return ""
        except subprocess.TimeoutExpired:
            continue
    return f"processo {proc.pid} ancora vivo dopo SIGTERM e SIGINT (nessun SIGKILL)"


def run_limitato(argv: Sequence[str] | str, *, timeout: float, input: Any = None, capture_output: bool = False,
                 stdin: Any = None, grazia: float = GRAZIA_SECONDI, **kwargs: Any) -> subprocess.CompletedProcess:
    """Come ``subprocess.run(argv, timeout=..., ...)``, ma allo scadere SIGTERM al gruppo, mai SIGKILL."""
    if kwargs.pop("check", False):
        raise ValueError("run_limitato: check=True non supportato")
    if capture_output:
        kwargs.setdefault("stdout", subprocess.PIPE)
        kwargs.setdefault("stderr", subprocess.PIPE)
    if input is not None:
        stdin = subprocess.PIPE
    elif stdin is None:
        stdin = subprocess.DEVNULL
    if os.name != "nt":
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(argv, stdin=stdin, **kwargs)
    try:
        out, err = proc.communicate(input=input, timeout=timeout)
    except subprocess.TimeoutExpired:
        nota = _ferma(proc, grazia)
        out = err = None
        if not nota:
            try:
                out, err = proc.communicate(timeout=grazia)
            except (subprocess.TimeoutExpired, ValueError, OSError):
                pass
        eccezione = subprocess.TimeoutExpired(argv, timeout, output=out, stderr=err)
        eccezione.processo_rimasto = nota          # vuoto se il gruppo si e' chiuso
        raise eccezione
    except BaseException:
        _ferma(proc, grazia)
        raise
    return subprocess.CompletedProcess(argv, proc.returncode, out, err)
