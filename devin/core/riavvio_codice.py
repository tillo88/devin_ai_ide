"""Il backend si riavvia da solo quando arriva codice nuovo e DEVIN e' libero (22/10).

Il deploy e' un ``git pull`` in ``/opt/devin-ai-ide-frontend``; il processo
pero' gira col codice che ha caricato all'avvio. Il 21/10 tre correzioni sono
rimaste ferme per ore: il backend non si era piu' riavviato, e DEVIN ha
ripetuto lo stesso errore gia' corretto finche' il sorvegliante l'ha fermato.

Ogni ``CONTROLLO_OGNI`` secondi si confronta il commit della cartella con
quello dell'avvio. Se e' cambiato e DEVIN e' libero (nessuna risposta in
corso, nessun run, nessuna sessione dell'agente locale usata negli ultimi
``SESSIONE_INATTIVA`` secondi) il backend chiude il server con garbo e si
rilancia **nello stesso processo** (``os.execv``) col codice nuovo. Se DEVIN
sta lavorando aspetta e lo scrive nel journal.

Perche' nello stesso processo (22/10). La prima versione usciva con 75 e
lasciava il riavvio a systemd: funzionava, ma la sessione DEVIN controlla
ogni 2 secondi che il backend sia attivo, lo vedeva fermo, si chiudeva e
spegneva anche il modello. Il riavvio diventava un giro completo di sessione
(modello ricaricato, cache del prefisso persa). Con ``execv`` l'unita' resta
attiva con lo stesso PID: si ricarica solo il backend.

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


def chiudi_o_rilancia(esci: Callable[[int], None] = os._exit,
                      rilancia: Optional[Callable[[str, list], None]] = None) -> None:
    """Fine del server: col codice nuovo si rilancia nello stesso processo, se no esce.

    Se il rilancio fallisce si esce con 75 e ci pensa systemd (Restart=on-failure).
    """
    import sys

    sys.stdout.flush()
    sys.stderr.flush()
    if not _richiesto.is_set():
        esci(0)
        return
    try:
        (rilancia or os.execv)(sys.executable, [sys.executable, *sys.argv])
    except OSError as exc:
        print(f"[Riavvio] rilancio non riuscito ({exc}): esco con {CODICE_RIAVVIO}", flush=True)
    esci(CODICE_RIAVVIO)


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
            print(f"[Riavvio] codice nuovo {iniziale[:12]} -> {attuale[:12]}: aspetto, {motivo}", flush=True)
            ultimo_avviso = motivo
        elif decisione == "riavvio":
            print(f"[Riavvio] codice nuovo {iniziale[:12]} -> {attuale[:12]}: DEVIN e' libero, mi riavvio", flush=True)
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
    print(f"[Riavvio] codice {iniziale[:12]}: mi riavvio da solo quando ne arriva di nuovo e DEVIN e' libero",
          flush=True)
    # SIGTERM a se stessi: uvicorn chiude con garbo, poi run_server chiama chiudi_o_rilancia()
    threading.Thread(target=sorveglia, args=(iniziale, lambda: os.kill(os.getpid(), signal.SIGTERM)),
                     daemon=True, name="riavvio-codice").start()
    return True
