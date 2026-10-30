"""Prova dell'app: DEVIN usa il programma che ha appena scritto.

I test dicono che le funzioni fanno quello che i test chiedono; non dicono se
il programma parte, se il comando che l'utente scrivera' funziona, se la
pagina si apre. Qui il modello legge README e interfacce, propone pochi
controlli concreti (comandi da terminale, una richiesta HTTP a un server
avviato, una finestra che resta aperta) e li si esegue davvero, in una COPIA
della sandbox: un'app che salva dati non deve sporcare quello che poi verra'
applicato al progetto.

Il piano e' dato, non istruzioni: si accetta solo una forma stretta (python
come programma, niente shell, solo localhost, tempi e numero di passi
limitati). Cio' che qui non si puo' provare (una finestra senza display, un
browser assente) si dichiara, non si fa finta.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional

MAX_PASSI = 6
MAX_SECONDI = 60
MAX_USCITA = 4000
TIPI = ("comando", "http", "finestra")
IGNORA_COPIA = shutil.ignore_patterns(".git", ".devin", ".devin_chat", ".devin_state",
                                      "__pycache__", ".pytest_cache", "node_modules", ".venv")


class PianoNonValido(ValueError):
    """Il piano proposto dal modello non ha la forma ammessa."""


# --- che cosa e' ------------------------------------------------------------

def tipo_progetto(root: Path) -> Dict[str, Any]:
    """Indizi su che programma e' (CLI, web, finestra), dai sorgenti."""
    indizi = {"cli": False, "web": False, "finestra": False, "html": False, "python": False}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or any(p.startswith(".") for p in path.relative_to(root).parts):
            continue
        if path.suffix == ".html":
            indizi["html"] = True
        if path.suffix != ".py" or path.name.startswith("test_") or path.name.endswith("_test.py"):
            continue
        indizi["python"] = True
        try:
            testo = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if re.search(r"\b(argparse|click|typer|sys\.argv)\b", testo) or "__main__" in testo:
            indizi["cli"] = True
        if re.search(r"\b(flask|fastapi|uvicorn|http\.server|aiohttp|bottle|streamlit)\b", testo):
            indizi["web"] = True
        if re.search(r"\b(tkinter|PySide6|PyQt5|PyQt6|kivy|pygame)\b", testo):
            indizi["finestra"] = True
    return indizi


# --- il piano -----------------------------------------------------------------

def richiesta_piano(task: str, file_scritti: List[str], contesti: Dict[str, str],
                    indizi: Dict[str, Any]) -> str:
    sorgenti = "\n\n".join(f"# FILE: {nome}\n{testo}" for nome, testo in contesti.items())
    return (
        "Hai appena scritto questo progetto. Ora PROVALO come lo userebbe l'utente: "
        "proponi da 1 a 6 controlli concreti, eseguiti davvero in una copia del progetto.\n\n"
        f"COMPITO ORIGINALE (estratto):\n{task[:2500]}\n\n"
        f"FILE: {', '.join(file_scritti)}\n"
        f"INDIZI: {json.dumps(indizi)}\n\n"
        f"SORGENTI (interfacce o testo):\n{sorgenti[:12000]}\n\n"
        "Rispondi SOLO con un oggetto JSON, senza testo attorno:\n"
        '{"passi": [\n'
        '  {"nome": "aggiungi una scadenza", "tipo": "comando",\n'
        '   "argv": ["python", "scadenze.py", "aggiungi", "Bollo", "2026-12-01"],\n'
        '   "stdin": "", "exit": 0, "contiene": ["Bollo"], "non_contiene": ["Traceback"], "secondi": 20},\n'
        '  {"nome": "la home risponde", "tipo": "http",\n'
        '   "argv": ["python", "app.py", "--port", "{port}"],\n'
        '   "path": "/", "status": 200, "contiene": ["<title>"], "secondi": 20},\n'
        '  {"nome": "la finestra si apre", "tipo": "finestra", "argv": ["python", "gui.py"], "secondi": 5}\n'
        "]}\n"
        "Regole:\n"
        "- argv[0] e' sempre \"python\"; niente shell, pipe o redirezioni.\n"
        "- I passi girano in ordine nella STESSA copia: un passo puo' usare dati creati da quello prima.\n"
        "- Per un server usa il segnaposto {port} dove il programma riceve la porta "
        "(argomento o variabile: la porta e' anche in PORT).\n"
        "- \"contiene\" sono pezzi di testo che l'uscita (o la pagina) deve avere: "
        "solo cose che il programma scrive davvero secondo i sorgenti.\n"
        "- Prova i casi che contano per l'utente: prima gli esempi e i test di riferimento del compito, "
        "se ci sono, con i risultati che il compito indica.\n"
        "- Verifica SOLO comportamenti che il compito chiede. Niente casi d'errore inventati: un'eccezione "
        "(ValueError, TypeError, ...) su input vuoti, negativi o di tipo sbagliato si prova solo se il "
        "compito la chiede esplicitamente.\n"
        f"- Al massimo {MAX_PASSI} passi, ognuno al massimo {MAX_SECONDI} secondi."
    )


def leggi_piano(testo: str) -> List[Dict[str, Any]]:
    """Dal testo del modello alla lista di passi, o PianoNonValido col motivo."""
    if not testo or not testo.strip():
        raise PianoNonValido("risposta vuota")
    grezzo = testo.strip()
    recinto = re.search(r"```(?:json)?\s*(\{.*\})\s*```", grezzo, re.DOTALL)
    if recinto:
        grezzo = recinto.group(1)
    else:
        inizio, fine = grezzo.find("{"), grezzo.rfind("}")
        if inizio < 0 or fine <= inizio:
            raise PianoNonValido("nessun oggetto JSON")
        grezzo = grezzo[inizio:fine + 1]
    try:
        dati = json.loads(grezzo)
    except json.JSONDecodeError as exc:
        raise PianoNonValido(f"JSON non valido: {exc}") from exc
    passi = dati.get("passi") if isinstance(dati, dict) else None
    if not isinstance(passi, list) or not passi:
        raise PianoNonValido("manca la lista 'passi'")
    if len(passi) > MAX_PASSI:
        raise PianoNonValido(f"{len(passi)} passi, al massimo {MAX_PASSI}")
    return [_valida_passo(i, passo) for i, passo in enumerate(passi, 1)]


def _testi(valore: Any, campo: str, i: int) -> List[str]:
    if valore in (None, ""):
        return []
    if isinstance(valore, str):
        valore = [valore]
    if not isinstance(valore, list) or not all(isinstance(v, str) for v in valore):
        raise PianoNonValido(f"passo {i}: '{campo}' deve essere una lista di testi")
    return [v for v in valore if v]


def _valida_passo(i: int, passo: Any) -> Dict[str, Any]:
    if not isinstance(passo, dict):
        raise PianoNonValido(f"passo {i}: non e' un oggetto")
    tipo = passo.get("tipo")
    if tipo not in TIPI:
        raise PianoNonValido(f"passo {i}: tipo {tipo!r} (ammessi: {', '.join(TIPI)})")
    argv = passo.get("argv")
    if (not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv)
            or len(argv) > 40):
        raise PianoNonValido(f"passo {i}: argv deve essere una lista di testi")
    if argv[0] != "python":
        raise PianoNonValido(f"passo {i}: argv[0] deve essere \"python\", non {argv[0]!r}")
    if len(argv) < 2:
        raise PianoNonValido(f"passo {i}: manca il programma da eseguire")
    try:
        secondi = int(passo.get("secondi") or 20)
    except (TypeError, ValueError) as exc:
        raise PianoNonValido(f"passo {i}: 'secondi' non e' un numero") from exc
    pulito = {
        "nome": str(passo.get("nome") or f"passo {i}")[:120],
        "tipo": tipo,
        "argv": argv,
        "secondi": max(2, min(MAX_SECONDI, secondi)),
        "contiene": _testi(passo.get("contiene"), "contiene", i),
        "non_contiene": _testi(passo.get("non_contiene"), "non_contiene", i) or ["Traceback (most recent call last)"],
    }
    if tipo == "comando":
        stdin = passo.get("stdin") or ""
        if not isinstance(stdin, str):
            raise PianoNonValido(f"passo {i}: stdin deve essere testo")
        pulito["stdin"] = stdin[:4000]
        exit_code = passo.get("exit", 0)
        pulito["exit"] = None if exit_code is None else int(exit_code)
    elif tipo == "http":
        path = str(passo.get("path") or "/")
        if not path.startswith("/") or "//" in path or "\n" in path:
            raise PianoNonValido(f"passo {i}: path HTTP non valido {path!r}")
        pulito["path"] = path
        pulito["status"] = int(passo.get("status") or 200)
    return pulito


def piano_html_statico(root: Path) -> List[Dict[str, Any]]:
    """Una pagina senza server suo: la si serve e la si apre."""
    pagine = sorted(p.relative_to(root).as_posix() for p in root.rglob("*.html")
                    if not any(part.startswith(".") for part in p.relative_to(root).parts))
    if not pagine:
        return []
    principale = "index.html" if "index.html" in pagine else pagine[0]
    return [{
        "nome": f"la pagina {principale} si apre", "tipo": "http",
        "argv": ["python", "-m", "http.server", "{port}", "--bind", "127.0.0.1"],
        "path": "/" + principale, "status": 200, "contiene": [], "secondi": 15,
        "non_contiene": [], "browser": True,
    }]


# --- esecuzione -----------------------------------------------------------------

def _porta_libera() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _ambiente(cartella: Path, porta: Optional[int] = None) -> Dict[str, str]:
    env = dict(os.environ)
    casa = cartella / ".casa"
    casa.mkdir(exist_ok=True)
    env.update({
        "HOME": str(casa), "USERPROFILE": str(casa),
        "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8",
        "QT_QPA_PLATFORM": "offscreen", "MPLBACKEND": "Agg", "SDL_VIDEODRIVER": "dummy",
        "BROWSER": "true", "NO_COLOR": "1",
    })
    if porta:
        env["PORT"] = str(porta)
    from devin.engine.ambienti import env_corrente
    return env_corrente(env)


def _argv(passo: Dict[str, Any], porta: Optional[int]) -> List[str]:
    from devin.engine.ambienti import python_corrente
    argv = [python_corrente()] + list(passo["argv"][1:])
    if porta:
        argv = [a.replace("{port}", str(porta)) for a in argv]
    return argv


def _chiudi(proc: subprocess.Popen) -> str:
    """SIGTERM al gruppo e attesa. Niente SIGKILL: se non si chiude, si dice."""
    if proc.poll() is not None:
        return ""
    from devin.engine.ambienti import termina_gruppo
    try:
        if os.name != "nt":
            termina_gruppo(proc.pid, signal.SIGTERM)    # nella scatola: al programma, non a bubblewrap
        else:
            proc.terminate()
        proc.wait(timeout=10)
        return ""
    except subprocess.TimeoutExpired:
        try:
            if os.name != "nt":
                termina_gruppo(proc.pid, signal.SIGINT)
            proc.wait(timeout=5)
            return ""
        except (subprocess.TimeoutExpired, ProcessLookupError):
            return "il programma non si e' chiuso con SIGTERM/SIGINT"
    except ProcessLookupError:
        return ""


def _avvia(argv: List[str], cartella: Path, env: Dict[str, str], rete: bool = False):
    """Avvia in background; l'uscita va su file (una pipe piena bloccherebbe il server)."""
    from devin.engine.ambienti import protetto
    registro = Path(tempfile.mkstemp(prefix="uscita-", suffix=".txt", dir=str(cartella / ".casa"))[1])
    with registro.open("w", encoding="utf-8") as scrittura:
        proc = subprocess.Popen(
            protetto(argv, cartella, env, rete=rete), cwd=str(cartella), env=env, stdin=subprocess.DEVNULL,
            stdout=scrittura, stderr=subprocess.STDOUT, start_new_session=(os.name != "nt"),
        )
    return proc, registro


def _leggi(registro: Path) -> str:
    try:
        return registro.read_text(encoding="utf-8", errors="replace")[-MAX_USCITA * 2:]
    except OSError:
        return ""


def _controlla_testo(testo: str, passo: Dict[str, Any]) -> List[str]:
    problemi = [f"manca {atteso!r}" for atteso in passo["contiene"] if atteso not in testo]
    problemi += [f"compare {vietato!r}" for vietato in passo["non_contiene"] if vietato in testo]
    return problemi


def _esegui_comando(passo, cartella):
    from devin.engine.ambienti import protetto
    env = _ambiente(cartella)
    try:
        from devin.engine.processi import run_limitato
        proc = run_limitato(
            protetto(_argv(passo, None), cartella, env), cwd=str(cartella), env=env,
            input=passo.get("stdin", ""), capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=passo["secondi"],
        )
    except subprocess.TimeoutExpired:
        return False, f"non ha finito in {passo['secondi']} s", ""
    uscita = ((proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")).strip()
    problemi = _controlla_testo(uscita, passo)
    if passo.get("exit") is not None and proc.returncode != passo["exit"]:
        problemi.insert(0, f"exit {proc.returncode} (atteso {passo['exit']})")
    return not problemi, "; ".join(problemi), uscita


def _esegui_http(passo, cartella, browser):
    porta = _porta_libera()
    env = _ambiente(cartella, porta)
    # il server deve rispondere da fuori: nella scatola condivide la rete, il resto resta chiuso
    proc, registro = _avvia(_argv(passo, porta), cartella, env, rete=True)
    url = f"http://127.0.0.1:{porta}{passo['path']}"
    limite = time.time() + passo["secondi"]
    corpo, stato, errore = "", None, ""
    try:
        while time.time() < limite:
            if proc.poll() is not None:
                errore = f"il server e' uscito (exit {proc.returncode}) prima di rispondere"
                break
            try:
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with opener.open(url, timeout=3) as risposta:
                    stato = risposta.status
                    corpo = risposta.read(200_000).decode("utf-8", errors="replace")
                break
            except urllib.error.HTTPError as exc:
                stato = exc.code
                corpo = exc.read(200_000).decode("utf-8", errors="replace")
                break
            except (urllib.error.URLError, ConnectionError, socket.timeout, OSError):
                time.sleep(0.5)
        else:
            errore = f"nessuna risposta su {passo['path']} in {passo['secondi']} s"
        console = []
        if stato is not None and passo.get("browser") and browser:
            console = browser(url)
    finally:
        chiusura = _chiudi(proc)
        log = _leggi(registro)
    problemi = [errore] if errore else []
    if stato is not None and stato != passo["status"]:
        problemi.append(f"HTTP {stato} (atteso {passo['status']})")
    if stato is not None:
        problemi += _controlla_testo(corpo, passo)
        problemi += [f"errore nella console del browser: {riga}" for riga in console[:3]]
    problemi += _controlla_testo(log, {"contiene": [], "non_contiene": passo["non_contiene"]})
    if chiusura:
        problemi.append(chiusura)
    return not problemi, "; ".join(p for p in problemi if p), (log + "\n" + corpo[:1500]).strip()


def _esegui_finestra(passo, cartella):
    argv = _argv(passo, None)
    env = _ambiente(cartella)
    usa_tk = any("tkinter" in p.read_text(encoding="utf-8", errors="replace")
                 for p in cartella.rglob("*.py") if p.is_file())
    if os.name != "nt" and not env.get("DISPLAY") and usa_tk:
        xvfb = shutil.which("xvfb-run")
        if not xvfb:
            return None, "finestra tkinter senza display e senza xvfb-run: qui non si puo' aprire", ""
        argv = [xvfb, "-a"] + argv
    proc, registro = _avvia(argv, cartella, env)
    try:
        limite = time.time() + passo["secondi"]
        while time.time() < limite and proc.poll() is None:
            time.sleep(0.25)
        uscito = proc.poll()
    finally:
        chiusura = _chiudi(proc)
        log = _leggi(registro)
    problemi = []
    if uscito is not None:
        problemi.append(f"la finestra si e' chiusa da sola (exit {uscito}) entro {passo['secondi']} s")
    problemi += _controlla_testo(log, {"contiene": passo["contiene"], "non_contiene": passo["non_contiene"]})
    if chiusura:
        problemi.append(chiusura)
    return not problemi, "; ".join(problemi), log


def browser_playwright() -> Optional[Callable[[str], List[str]]]:
    """Un browser vero per le pagine, se c'e'. Restituisce gli errori della console."""
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except Exception:
        return None

    def apri(url: str) -> List[str]:
        from playwright.sync_api import sync_playwright
        errori: List[str] = []
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch()
                pagina = browser.new_page()
                pagina.on("console", lambda m: errori.append(m.text) if m.type == "error" else None)
                pagina.on("pageerror", lambda e: errori.append(str(e)))
                pagina.goto(url, wait_until="load", timeout=15000)
                pagina.wait_for_timeout(1000)
                browser.close()
        except Exception as exc:
            errori.append(f"browser non utilizzabile: {type(exc).__name__}: {exc}"[:300])
        return errori

    return apri


def esegui_piano(root: Path, passi: List[Dict[str, Any]],
                 browser: Optional[Callable[[str], List[str]]] = None) -> Dict[str, Any]:
    """Esegue i passi in una copia di ``root``; root non viene toccata."""
    esiti: List[Dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="devin-prova-") as tmp:
        copia = Path(tmp) / "app"
        shutil.copytree(root, copia, ignore=IGNORA_COPIA)
        for passo in passi:
            inizio = time.time()
            try:
                if passo["tipo"] == "comando":
                    ok, motivo, uscita = _esegui_comando(passo, copia)
                elif passo["tipo"] == "http":
                    ok, motivo, uscita = _esegui_http(passo, copia, browser)
                else:
                    ok, motivo, uscita = _esegui_finestra(passo, copia)
            except Exception as exc:
                ok, motivo, uscita = False, f"{type(exc).__name__}: {exc}", ""
            esiti.append({
                "nome": passo["nome"], "tipo": passo["tipo"],
                "comando": " ".join(["python"] + passo["argv"][1:]),
                "esito": "ok" if ok else ("non_verificabile" if ok is None else "fallito"),
                "motivo": motivo, "uscita": (uscita or "")[-MAX_USCITA:],
                "secondi": round(time.time() - inizio, 1),
            })
    falliti = [e for e in esiti if e["esito"] == "fallito"]
    return {
        "stato": "fallita" if falliti else ("non_verificabile" if all(
            e["esito"] == "non_verificabile" for e in esiti) else "riuscita"),
        "passi": esiti,
        "errori": [f"{e['nome']} (`{e['comando']}`): {e['motivo']}" for e in falliti],
    }


def riassunto(prova: Optional[Dict[str, Any]]) -> str:
    """Una riga per la chat."""
    if not prova:
        return ""
    if prova.get("stato") == "saltata":
        return f"app non provata: {prova.get('motivo')}"
    passi = prova.get("passi") or []
    ok = sum(1 for p in passi if p["esito"] == "ok")
    testo = f"app provata: {ok}/{len(passi)} controlli ok"
    non_verificabili = [p["nome"] for p in passi if p["esito"] == "non_verificabile"]
    if non_verificabili:
        testo += f" ({len(non_verificabili)} non verificabili qui)"
    return testo
