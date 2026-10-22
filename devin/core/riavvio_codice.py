"""Il backend si riavvia da solo quando arriva codice nuovo e DEVIN e' libero (22/10).

Il deploy e' un ``git pull`` in ``/opt/devin-ai-ide-frontend``; il processo
pero' gira col codice che ha caricato all'avvio. Il 21/10 tre correzioni sono
rimaste ferme per ore: il backend non si era piu' riavviato, e DEVIN ha
ripetuto lo stesso errore gia' corretto finche' il sorvegliante l'ha fermato.

Ogni ``CONTROLLO_OGNI`` secondi si confronta il commit della cartella con
quello dell'avvio. Se e' cambiato e DEVIN e' libero (nessuna risposta in
corso, nessun run, nessuna sessione dell'agente locale usata negli ultimi
``SESSIONE_INATTIVA`` secondi) il backend esce con ``CODICE_RIAVVIO``:
l'unita' systemd ha ``Restart=on-failure`` e lo riavvia col codice nuovo. Se
DEVIN sta lavorando aspetta e lo scrive nel journal.

Attivo solo sotto systemd (``INVOCATION_ID``): altrove (Windows, test)
nessuno lo riavvierebbe. ``DEVIN_RIAVVIO_AUTOMATICO=0`` lo spegne.
"""
from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Optional

CODICE_RIAVVIO = 75            # EX_TEMPFAIL: per systemd e' un'uscita da riavviare
CONTROLLO_OGNI = 60
SESSIONE_INATTIVA = 15 * 60
RADICE = Path(__file__).resolve().parents[2]

_richiesto = threading.Event()


def codice_uscita() -> int:
    """Con cosa deve uscire il processo: 75 se il riavvio l'ha chiesto il codice nuovo."""
    return CODICE_RIAVVIO if _richiesto.is_set() else 0


def commit_corrente(radice: Path = RADICE) -> Optional[str]:
    try:
        uscita = subprocess.run(
            ["git", "-c", f"safe.directory={radice}", "-C", str(radice), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL,
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "GIT_OPTIONAL_LOCKS": "0", "LANG": "C"})
    except (OSError, subprocess.SubprocessError):
        return None
    commit = uscita.stdout.strip()
    return commit if uscita.returncode == 0 and len(commit) == 40 else None


def occupato(adesso: Optional[float] = None) -> str:
    """Perche' DEVIN non e' libero, o "" se lo e'."""
    from devin.core import agente_locale, note_utente
    from devin.ui import fast_app

    adesso = time.time() if adesso is None else adesso
    if getattr(fast_app, "active_runs", None) or getattr(fast_app, "starting_runs", None):
        return "un run in corso"
    aperte = note_utente.aperte()
    with agente_locale._LOCK:
        sessioni = list(agente_locale._SESSIONI.values())
    for sessione in sessioni:
        if adesso - sessione.ultimo_uso < SESSIONE_INATTIVA:
            return "l'agente locale sta lavorando"
        # una sessione abbandonata tiene aperte le note ma non e' lavoro
        aperte[sessione.chiave_note] = aperte.get(sessione.chiave_note, 0) - 1
    if any(n > 0 for n in aperte.values()):
        return "una risposta in corso"
    return ""


def controlla(iniziale: str, attuale: Optional[str], motivo: str) -> str:
    """Una decisione: "fermo" (niente di nuovo), "aspetto" o "riavvio"."""
    if not attuale or attuale == iniziale:
        return "fermo"
    return "aspetto" if motivo else "riavvio"


def sorveglia(iniziale: str, esci: Callable[[], None], dormi: Callable[[float], None] = time.sleep,
              commit: Callable[[], Optional[str]] = commit_corrente,
              perche_occupato: Callable[[], str] = occupato) -> None:
    ultimo_avviso = ""
    while True:
        dormi(CONTROLLO_OGNI)
        attuale = commit()
        motivo = perche_occupato() if attuale and attuale != iniziale else ""
        decisione = controlla(iniziale, attuale, motivo)
        if decisione == "aspetto" and motivo != ultimo_avviso:
            print(f"[Riavvio] codice nuovo {iniziale[:12]} -> {attuale[:12]}: aspetto, {motivo}")
            ultimo_avviso = motivo
        elif decisione == "riavvio":
            print(f"[Riavvio] codice nuovo {iniziale[:12]} -> {attuale[:12]}: DEVIN e' libero, mi riavvio")
            _richiesto.set()
            esci()
            return


def avvia() -> bool:
    """Parte il sorvegliante del codice, se ha senso qui. True se e' partito."""
    if os.environ.get("DEVIN_RIAVVIO_AUTOMATICO", "1") == "0" or not os.environ.get("INVOCATION_ID"):
        return False
    iniziale = commit_corrente()
    if not iniziale:
        print("[Riavvio] commit iniziale non leggibile: riavvio automatico spento")
        return False
    print(f"[Riavvio] codice {iniziale[:12]}: mi riavvio da solo quando ne arriva di nuovo e DEVIN e' libero")
    # SIGTERM a se stessi: uvicorn chiude con garbo, poi run_server esce con codice_uscita()
    threading.Thread(target=sorveglia, args=(iniziale, lambda: os.kill(os.getpid(), signal.SIGTERM)),
                     daemon=True, name="riavvio-codice").start()
    return True
