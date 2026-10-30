"""Risolutore di vincoli: DEVIN scrive il modello, un programma lo risolve.

17/10: una turnazione (4 squadre, 20 turni, blocchi di 4-6 giorni) ha fatto
ragionare DEVIN, ChatGPT e Gemini per decine di minuti senza risultato.
Scritte le regole come modello CP-SAT, la soluzione (e la prova che con un
ciclo di 4 settimane non esiste) arriva in meno di un secondo. Problemi del
genere — turni, orari, incastri, assegnazioni, pianificazioni — non vanno
ragionati a mano: si descrivono e si fanno risolvere.

Il codice gira con l'interprete di un ambiente separato (``venv-strumenti``,
installato sul rig da un'operazione tipizzata dal lock
``config/venv-strumenti.lock``: OR-Tools, numpy, pandas), in una cartella
temporanea, con un tempo massimo e, se c'e' bwrap, senza rete. Il backend non
importa nulla di quell'ambiente: se manca, lo strumento non viene offerto e lo
stato dice perche'.
"""
from __future__ import annotations

import os
from pathlib import Path
import resource
import shutil
import subprocess
import tempfile
from typing import Any, Dict, Optional, Tuple

PYTHON_DEFAULT = "~/.local/share/devin/venv-strumenti/bin/python"
MAX_SECONDI = 180
MAX_USCITA = 12000
MEMORIA_BYTE = 8 * 1024 ** 3

DESCRIZIONE = (
    "Esegue un programma Python con OR-Tools (CP-SAT), numpy e pandas, in un ambiente isolato "
    "senza rete, al massimo 3 minuti; restituisce cio' che il programma stampa. USALO per problemi "
    "di vincoli e combinatoria (turni, orari, incastri, assegnazioni, calendari, pianificazione, "
    "puzzle): scrivi le regole come modello (from ortools.sat.python import cp_model), fai stampare "
    "la soluzione in una tabella e i controlli che la verificano. Se il risultato e' INFEASIBLE "
    "vuol dire che con quelle regole non esiste soluzione: dillo e indica quale regola allentare. "
    "Non provare a risolvere questi problemi ragionando a mano.")

STRUMENTO = {"type": "function", "function": {
    "name": "risolvi_vincoli",
    "description": DESCRIZIONE,
    "parameters": {"type": "object", "properties": {
        "codice": {"type": "string", "description": "programma Python completo, che stampa il risultato"},
        "secondi": {"type": "integer", "description": "tempo massimo, default 120"}},
        "required": ["codice"], "additionalProperties": False}}}

NOTA_PROMPT = (
    "PROBLEMI DI VINCOLI: per turni, orari, incastri, assegnazioni o pianificazioni NON ragionare a "
    "mano per minuti: usa lo strumento risolvi_vincoli scrivendo un modello OR-Tools CP-SAT con le "
    "regole dell'utente, e mostra la tabella che il solver trova (o di' che non esiste e perche').")


def interprete(config: Optional[Dict[str, Any]] = None) -> Path:
    percorso = ((config or {}).get("tools") or {}).get("solver_python") or PYTHON_DEFAULT
    return Path(os.path.expanduser(str(percorso)))


def disponibile(config: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
    python = interprete(config)
    if not python.exists():
        return False, f"ambiente strumenti non installato ({python})"
    if not os.access(python, os.X_OK):
        return False, f"interprete non eseguibile ({python})"
    return True, "pronto"


def _limiti() -> None:  # pragma: no cover - gira nel processo figlio
    try:
        os.setsid()          # gia' sessione propria se lanciato da run_limitato
    except OSError:
        pass
    try:
        resource.setrlimit(resource.RLIMIT_AS, (MEMORIA_BYTE, MEMORIA_BYTE))
    except (ValueError, OSError):
        pass


def _comando(python: Path, script: Path, cartella: Path) -> Tuple[list, str]:
    bwrap = shutil.which("bwrap")
    if not bwrap:
        return [str(python), "-I", str(script)], "senza isolamento di rete (bwrap assente)"
    return ([bwrap, "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
             "--bind", str(cartella), str(cartella), "--unshare-net", "--die-with-parent",
             "--chdir", str(cartella), str(python), "-I", str(script)], "")


def esegui(codice: str, secondi: int = 120, config: Optional[Dict[str, Any]] = None) -> str:
    """Esegue il programma e restituisce un testo per il modello (mai un'eccezione)."""
    pronto, motivo = disponibile(config)
    if not pronto:
        return f"ERRORE: risolutore non disponibile: {motivo}"
    if not str(codice or "").strip():
        return "ERRORE: 'codice' vuoto"
    limite = max(5, min(MAX_SECONDI, int(secondi or 120)))
    with tempfile.TemporaryDirectory(prefix="devin-risolutore-") as tmp:
        cartella = Path(tmp)
        script = cartella / "modello.py"
        script.write_text(str(codice), encoding="utf-8")
        argv, avviso = _comando(interprete(config), script, cartella)
        env = {"PATH": "/usr/bin:/bin", "HOME": str(cartella), "LANG": "C.UTF-8",
               "PYTHONIOENCODING": "utf-8", "TMPDIR": str(cartella), "PYTHONDONTWRITEBYTECODE": "1", "OMP_NUM_THREADS": "8"}
        try:
            from devin.engine.processi import run_limitato
            proc = run_limitato(argv, cwd=str(cartella), env=env, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=limite,
                                  stdin=subprocess.DEVNULL, preexec_fn=_limiti)
        except subprocess.TimeoutExpired:
            return (f"TIMEOUT: nessun risultato in {limite} s. Imposta un limite al solver "
                    "(solver.parameters.max_time_in_seconds) e stampa la soluzione migliore trovata.")
        if proc.returncode != 0 and "bwrap:" in (proc.stderr or "")[:300]:
            return f"ERRORE: l'isolamento (bwrap) non parte: {proc.stderr.strip()[:300]}"
    uscita = (proc.stdout or "").strip()
    if proc.stderr and proc.stderr.strip():
        uscita += "\n[stderr]\n" + proc.stderr.strip()[-4000:]
    if len(uscita) > MAX_USCITA:
        uscita = uscita[:MAX_USCITA] + f"\n... (tagliato: {len(uscita)} caratteri)"
    testa = f"exit {proc.returncode}" + (f" ({avviso})" if avviso else "")
    return f"{testa}\n{uscita or '(il programma non ha stampato nulla: stampa la soluzione)'}"
