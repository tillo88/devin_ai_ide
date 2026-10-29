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

29/10, la SCATOLA (Alessandro: "voglio che arrivi a fare tutto devin ma ci deve essere un
limite ad uccidere la sua casa"). Nella palestra tutto cio' che esegue il codice del
progetto passa da ``protetto(argv, cwd, env)``: con una scatola attiva l'argv diventa
bubblewrap con il sistema in sola lettura, scrivibili solo la cartella del caso e quella
di lavoro (piu' i file temporanei che il comando riceve, es. il report junit), un /tmp
proprio, processi propri (non vede e non puo' fermare backend, modello, rig) e niente
rete. DEVIN resta libero di fare tutto DENTRO il suo progetto. Il training ci entra con
``entra_scatola(progetto)``; se bubblewrap non c'e' o non parte il caso e' un guasto del
rig (runner_error), mai un ripiego silenzioso senza scatola. Fuori dal training (lavoro
normale sui progetti di Alessandro) nessuna scatola: ``protetto`` non cambia nulla.

Niente ``--new-session``: i processi dentro la scatola restano nel gruppo del comando.
Per chiuderli ``termina_gruppo`` manda SIGTERM a loro e non a bubblewrap: bubblewrap che
esce porterebbe con se' la scatola di colpo (``--die-with-parent``, che resta come rete di
sicurezza se il backend muore). Mai SIGKILL.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
from typing import Dict, Iterator, List, Optional, Sequence

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
    # la GPU e' del modello: tensorflow & co. del benchmark non devono aprire un contesto CUDA
    # (VRAM) mentre il modello gira. Senza GPU visibili girano su CPU.
    nuovo["CUDA_VISIBLE_DEVICES"] = ""
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


# ------------------------------------------------------------------ la scatola (29/10)

BWRAP = "/usr/bin/bwrap"
_scatola: ContextVar[Optional[str]] = ContextVar("devin_scatola", default=None)
_scatola_provata: Dict[str, bool] = {}
_PROVA = "touch /tmp/devin-prova && ! touch /usr/.devin-prova 2>/dev/null"


class ScatolaMancante(AmbienteMancante):
    pass


def prova_scatola(bwrap: Optional[str] = None) -> None:
    """bubblewrap c'e', parte come l'utente di DEVIN e tiene. Un successo si ricorda; un
    fallimento no (si riprova al caso dopo, magari e' stato installato nel frattempo)."""
    bwrap = bwrap or BWRAP
    if _scatola_provata.get(bwrap):
        return
    if not Path(bwrap).is_file():
        raise ScatolaMancante(f"scatola non disponibile: bubblewrap non installato ({bwrap})")
    try:
        proc = subprocess.run([bwrap, "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp",
                               "--unshare-pid", "--unshare-net", "--unshare-ipc", "--die-with-parent",
                               "/bin/sh", "-c", _PROVA], capture_output=True, text=True, timeout=30,
                              stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ScatolaMancante(f"scatola non disponibile: {type(exc).__name__}: {exc}") from exc
    if proc.returncode != 0:
        raise ScatolaMancante("scatola non disponibile: bubblewrap non parte o non tiene: "
                              + ((proc.stderr or proc.stdout or "").strip()[-200:] or f"exit {proc.returncode}"))
    _scatola_provata[bwrap] = True


def scatola_attiva() -> Optional[Path]:
    radice = _scatola.get()
    return Path(radice) if radice else None


def entra_scatola(cartella: Path | str) -> Token:
    prova_scatola()
    radice = Path(cartella).resolve()
    if not radice.is_dir():
        raise ScatolaMancante(f"scatola: la cartella del caso non esiste: {radice}")
    return _scatola.set(str(radice))


def esci_scatola(token: Optional[Token]) -> None:
    if token is not None:
        _scatola.reset(token)


@contextmanager
def scatola(cartella: Path | str) -> Iterator[Path]:
    token = entra_scatola(cartella)
    try:
        yield Path(_scatola.get() or "")
    finally:
        _scatola.reset(token)


def termina_gruppo(pgid: int, segnale: int = signal.SIGTERM) -> int:
    """``segnale`` ai processi del gruppo ``pgid`` tranne bubblewrap; quanti ne ha raggiunti.

    Senza scatola e' come ``killpg``. Con la scatola il codice del caso riceve SIGTERM e si
    chiude da se'; bubblewrap esce quando esce lui."""
    raggiunti = 0
    for voce in os.listdir("/proc"):
        if not voce.isdigit():
            continue
        try:
            stat = Path("/proc", voce, "stat").read_text()
        except OSError:
            continue
        nome = stat[stat.find("(") + 1:stat.rfind(")")]
        campi = stat[stat.rfind(")") + 2:].split()
        if len(campi) < 3 or int(campi[2]) != pgid or nome == "bwrap":
            continue
        try:
            os.kill(int(voce), segnale)
            raggiunti += 1
        except ProcessLookupError:
            pass
    if not raggiunti:
        try:
            os.killpg(pgid, segnale)
        except ProcessLookupError:
            pass
    return raggiunti


def _dentro(percorso: Path, radice: Path) -> bool:
    try:
        percorso.resolve().relative_to(radice.resolve())
        return True
    except ValueError:
        return False


def _temporanei(argv: Sequence[str]) -> List[Path]:
    """I file temporanei che il comando riceve sulla riga (l'ini vuoto di pytest_isolato, il
    report junit): dentro la scatola /tmp e' nuovo, questi devono restare visibili."""
    basi = {Path("/tmp").resolve(), Path(tempfile.gettempdir()).resolve()}
    out = []
    for arg in argv:
        valore = arg.split("=", 1)[1] if arg.startswith("--") and "=" in arg else arg
        if not valore.startswith("/"):
            continue
        percorso = Path(valore)
        if percorso.exists() and any(_dentro(percorso, base) for base in basi) and percorso.resolve() not in basi:
            out.append(percorso.resolve())
    return out


def protetto(argv: Sequence[str], cwd: Path | str, env: Optional[Dict[str, str]] = None,
             rete: bool = False) -> List[str]:
    """L'argv con cui eseguire il codice del progetto: nella scatola se ce n'e' una attiva.

    ``rete=True`` solo per un server che la prova dell'app deve raggiungere da fuori
    (tutto il resto della scatola resta)."""
    radice = scatola_attiva()
    if radice is None:
        return list(argv)
    lavoro = Path(cwd).resolve()
    scrivibili: List[Path] = [radice]
    for extra in [lavoro, *_temporanei(argv)]:
        if not any(_dentro(extra, s) for s in scrivibili):
            scrivibili.append(extra)
    out = [BWRAP, "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp"]
    for percorso in scrivibili:
        out += ["--bind", str(percorso), str(percorso)]
    casa = (env or {}).get("HOME") or os.environ.get("HOME", "")
    if not casa or not any(_dentro(Path(casa), s) for s in scrivibili):
        out += ["--setenv", "HOME", "/tmp"]          # la home vera e' in sola lettura
    out += ["--setenv", "TMPDIR", "/tmp", "--unshare-pid", "--unshare-ipc"]
    if not rete:
        out.append("--unshare-net")
    out += ["--die-with-parent", "--chdir", str(lavoro)]
    return out + list(argv)
