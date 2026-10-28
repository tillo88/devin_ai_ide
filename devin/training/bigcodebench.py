"""BigCodeBench nella palestra di DEVIN (28/10, scelta B di Alessandro).

BigCodeBench (Apache-2.0, 1140 compiti, v0.1.1): funzioni ``task_func`` che usano
librerie vere (pandas, numpy, requests, ...), con test ``unittest`` ufficiali. Sostituisce
MBPP (saturo e contaminato) come materiale della palestra.

Qui, in un posto solo:

* ``prepara(...)``: il dataset dalla release GitHub ufficiale, sha256 fissato: un file diverso
  da quello calibrato non entra (Hugging Face non e' raggiungibile da tutti i posti, GitHub si);
* ``gold_test(task)``: il test ufficiale come gold del caso. Carica i moduli di DEVIN in un
  modulo vero (``sys.modules``: multiprocessing deve poter ritrovare le funzioni per nome) e
  accoda il codice di test ufficiale a livello di modulo, cosi' pytest raccoglie ``TestCases``.
  E' lo stesso file nel training e nella calibrazione. A differenza dell'harness ufficiale, che
  concatena soluzione e test in un file solo, la soluzione e' un modulo a se': una soluzione che
  usa ``os`` senza importarlo perche' lo importa il test e' sbagliata (la calibrazione esclude i
  pochi compiti la cui soluzione ufficiale fa cosi');
* ``pericoloso(task)``: compiti che toccano processi o sistema (kill, os.system, ...) fuori
  dalla palestra, anche con l'isolamento: seconda linea di difesa;
* ``calibra(...)``: la soluzione ufficiale contro il suo gold, nell'ambiente vero del rig
  (venv-bigcodebench) e isolata. Un compito la cui soluzione ufficiale non passa non entra:
  un rosso per versione di libreria o rete non deve mai diventare "DEVIN ha sbagliato";
* ``casi(...)`` / ``import_bigcodebench_cases(...)``: i casi del training, solo calibrati e
  non pericolosi, con ``metadata.ambiente = "bigcodebench"`` (devin/engine/ambienti.py).

I dati li prepara sul rig un'azione tipizzata (ai-rig-ops) in
``/mnt/ai-rig-shared/palestra/bigcodebench/``: ``tasks.jsonl`` e ``calibrazione.json``.
Uso da riga di comando (sul rig, col python di venv-bigcodebench):

    python bigcodebench.py prepara <tasks.jsonl> [--da BigCodeBench.jsonl.gz]   (senza --da: scarica)
    python bigcodebench.py calibra <tasks.jsonl> <calibrazione.json> [--bwrap /usr/bin/bwrap] [--paralleli 6]
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
from typing import Any, Dict, Iterable, List, Optional

FONTE = "bigcodebench"
VERSIONE = "v0.1.1"
URL = ("https://github.com/bigcode-project/bigcodebench-annotation/releases/download/"
       f"{VERSIONE}/BigCodeBench.jsonl.gz")
SHA256 = "0df3d34c42c70f1eb46377a618be7774ff0da526553ae304e6648b7d77a32204"
RIGHE = 1140
MAX_BYTE = 8 * 1024 * 1024
CARTELLA = Path(os.environ.get("DEVIN_BIGCODEBENCH_DIR", "/mnt/ai-rig-shared/palestra/bigcodebench"))
SECONDI_CALIBRAZIONE = 120
PROXY_CHIUSO = {k: "http://127.0.0.1:9" for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY")} | {
    "no_proxy": "localhost,127.0.0.1", "NO_PROXY": "localhost,127.0.0.1"}
CAMPI = ("task_id", "instruct_prompt", "code_prompt", "canonical_solution", "test", "entry_point", "libs")

# processi, sottoprocessi, shell, sistema: fuori dalla palestra (28/10). Si guarda la soluzione
# ufficiale E il test. Le cancellazioni di file no: i test le usano di continuo per pulire i loro
# file temporanei. Conservativo sui processi: la calibrazione puo' girare in bubblewrap, ma nel training
# DEVIN scrive ed esegue il SUO codice fuori dalla scatola, come l'utente del rig.
PERICOLOSO = re.compile(
    r"\bos\.(kill|killpg|system|popen|exec\w*|spawn\w*|setuid|setgid|chown)\b"
    r"|\bsubprocess\b|\bpsutil\b|\bsignal\.|\.(kill|terminate)\(\)"
    r"|\b(killall|pkill|systemctl|sudo)\b|['\"]/(etc|proc|sys|boot|usr|var)/|\bctypes\b")

_GOLD = '''"""Gold test {task_id} (BigCodeBench) - iniettato dal training runner, NON scritto dal modello."""
import pathlib as _devin_pathlib
import sys as _devin_sys
import types as _devin_types


def _devin_carica():
    # un modulo vero, registrato: pickle (multiprocessing) ritrova le funzioni per nome
    modulo = _devin_types.ModuleType("devin_soluzione")
    _devin_sys.modules["devin_soluzione"] = modulo
    ns = modulo.__dict__
    qui = _devin_pathlib.Path(__file__).parent
    for percorso in sorted(qui.glob("*.py")):
        nome = percorso.name
        if nome.startswith("test") or nome.endswith("_test.py") or nome == "conftest.py":
            continue
        try:
            exec(compile(percorso.read_text(encoding="utf-8"), str(percorso), "exec"), ns)
        except Exception:
            continue
    return ns


globals().update({{k: v for k, v in _devin_carica().items() if not k.startswith("__")}})

# --- test ufficiali di BigCodeBench ---
'''


def numero(task_id: str) -> str:
    trovato = re.fullmatch(r"BigCodeBench/(\d+)", str(task_id or ""))
    if not trovato:
        raise ValueError(f"task_id inatteso: {task_id!r}")
    return trovato.group(1)


def gold_nome(task: Dict[str, Any]) -> str:
    return f"test_gold_bcb_{numero(task['task_id'])}.py"


def gold_test(task: Dict[str, Any]) -> str:
    return _GOLD.format(task_id=task["task_id"]) + str(task["test"]).rstrip() + "\n"


def librerie(task: Dict[str, Any]) -> List[str]:
    """``libs`` e' una lista nel jsonl delle release e una stringa "['a', 'b']" nel parquet."""
    valore = task.get("libs") or []
    if isinstance(valore, str):
        valore = re.findall(r"[A-Za-z_][A-Za-z0-9_.]*", valore)
    return [str(x) for x in valore]


def pericoloso(task: Dict[str, Any]) -> bool:
    return bool(PERICOLOSO.search(str(task.get("canonical_solution") or "") + "\n" + str(task.get("test") or "")))


def compito(task: Dict[str, Any]) -> str:
    return (str(task["instruct_prompt"]).strip() + "\n\n"
            "Write the solution as a single Python module named solution.py that defines task_func exactly as "
            "in the code above, plus your own pytest tests. Use only the libraries the task needs.")


def titolo(task: Dict[str, Any]) -> str:
    prima = str(task["instruct_prompt"]).strip().splitlines()[0] if str(task["instruct_prompt"]).strip() else ""
    return f"BigCodeBench {numero(task['task_id'])}: {prima[:70]}".rstrip()


def leggi_tasks(path: Path) -> List[Dict[str, Any]]:
    out = []
    for riga in path.read_text(encoding="utf-8").splitlines():
        if not riga.strip():
            continue
        task = json.loads(riga)
        if not isinstance(task, dict) or any(k not in task for k in CAMPI[:6]):
            raise ValueError("tasks.jsonl: riga senza i campi di BigCodeBench")
        out.append(task)
    return out


def casi(tasks: Iterable[Dict[str, Any]], calibrazione: Dict[str, Any], limit: int = 50,
         offset: int = 0) -> Dict[str, Any]:
    """I casi del training: solo compiti calibrati (soluzione ufficiale verde) e non pericolosi."""
    esiti = calibrazione.get("esiti") if isinstance(calibrazione.get("esiti"), dict) else {}
    buoni, scartati = [], {"non_calibrati": 0, "soluzione_ufficiale_rossa": 0, "pericolosi": 0}
    for task in tasks:
        esito = esiti.get(task["task_id"])
        if pericoloso(task):
            scartati["pericolosi"] += 1
        elif not isinstance(esito, dict):
            scartati["non_calibrati"] += 1
        elif esito.get("ok") is not True:
            scartati["soluzione_ufficiale_rossa"] += 1
        else:
            buoni.append(task)
    scelti = buoni[offset:offset + max(1, limit)]
    return {"scartati": scartati, "idonei": len(buoni), "casi": [{
        "title": titolo(t),
        "kind": "code_generation",
        "prompt": compito(t),
        "expected_signals": ["tests_pass"],
        "tags": ["python", FONTE, *librerie(t)[:8]],
        "bcb_task_id": t["task_id"],
        "ambiente": FONTE,
        "gold_tests": {gold_nome(t): gold_test(t)},
    } for t in scelti]}


def import_bigcodebench_cases(store, limit: int = 50, offset: int = 0,
                              cartella: Optional[Path] = None) -> Dict[str, Any]:
    base = Path(cartella or CARTELLA)
    tasks = leggi_tasks(base / "tasks.jsonl")
    calibrazione = json.loads((base / "calibrazione.json").read_text(encoding="utf-8"))
    if calibrazione.get("versione") != VERSIONE:
        raise ValueError(f"calibrazione per {calibrazione.get('versione')!r}, attesa {VERSIONE}")
    scelta = casi(tasks, calibrazione, limit=limit, offset=offset)
    creati = store.seed_cases(scelta["casi"], source=FONTE)
    return {"dataset_rows": len(tasks), "idonei": scelta["idonei"], "scartati": scelta["scartati"],
            "converted": len(scelta["casi"]), "created": len(creati),
            "skipped_existing": len(scelta["casi"]) - len(creati),
            "retired_duplicates": int(getattr(store, "last_seed_retired_duplicates", 0) or 0),
            "source": FONTE, "versione": VERSIONE}


# ---------------------------------------------------------------- calibrazione (sul rig)

def argv_isolato(bwrap: Optional[str], cartella: Path, argv: List[str]) -> List[str]:
    """Con bubblewrap: sistema in sola lettura, solo la cartella del compito scrivibile, processi
    propri (non vede e non uccide gli altri), niente rete. Senza: l'argv cosi' com'e'."""
    if not bwrap:
        return argv
    return [bwrap, "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp",
            "--bind", str(cartella), str(cartella), "--chdir", str(cartella), "--unshare-pid",
            "--unshare-net", "--unshare-ipc", "--die-with-parent", "--new-session", *argv]


def _ferma(proc: "subprocess.Popen[str]", secondi: int, attesa: int = 30) -> str:
    """Allo scadere: SIGTERM a tutto il gruppo (pytest e i processi che ha lanciato, es. un Pool),
    attesa limitata, mai SIGKILL. Se il gruppo non si ferma lo si dice nel motivo."""
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        proc.communicate(timeout=attesa)
        return f"timeout {secondi}s"
    except subprocess.TimeoutExpired:
        return f"timeout {secondi}s, processo ancora vivo dopo SIGTERM (pid {proc.pid})"


def calibra_task(task: Dict[str, Any], python: str, bwrap: Optional[str] = None,
                 secondi: int = SECONDI_CALIBRAZIONE) -> Dict[str, Any]:
    """Soluzione ufficiale (code_prompt + canonical_solution) contro il SUO gold, come nel training."""
    if pericoloso(task):
        return {"ok": False, "motivo": "pericoloso", "secondi": 0.0}
    cartella = Path(tempfile.mkdtemp(prefix="bcb_calibra_"))
    inizio = time.monotonic()
    try:
        (cartella / "solution.py").write_text(str(task["code_prompt"]) + str(task["canonical_solution"]),
                                              encoding="utf-8")
        (cartella / gold_nome(task)).write_text(gold_test(task), encoding="utf-8")
        argv = argv_isolato(bwrap, cartella, [python, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                                              "--rootdir", str(cartella), "-c", os.devnull,
                                              # 28/10: senza, con -c /dev/null pytest risale e scandisce /tmp
                                              # (cartelle di altri utenti: PermissionError). Come pytest_isolato.
                                              "--confcutdir", str(cartella), gold_nome(task)])
        env = {"PATH": str(Path(python).parent) + ":/usr/bin:/bin", "HOME": str(cartella), "LANG": "C.UTF-8",
               "MPLBACKEND": "Agg", "PYTHONDONTWRITEBYTECODE": "1", "TF_CPP_MIN_LOG_LEVEL": "3",
               # i compiti in parallelo non si pestano i file temporanei (senza bubblewrap /tmp e' comune)
               "TMPDIR": str(cartella),
               "CUDA_VISIBLE_DEVICES": "",   # la GPU e' del modello: tensorflow resta su CPU
               # niente rete anche senza bubblewrap: i compiti che scaricano (dati di nltk, URL) non
               # entrano, la palestra resta ripetibile. Un proxy chiuso basta per urllib/requests/nltk.
               **PROXY_CHIUSO}
        proc = subprocess.Popen(argv, cwd=str(cartella), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, env=env, start_new_session=True)
        try:
            uscita, _ = proc.communicate(timeout=secondi)
        except subprocess.TimeoutExpired:
            return {"ok": False, "motivo": _ferma(proc, secondi), "secondi": round(time.monotonic() - inizio, 1)}
        uscita = (uscita or "").strip()
        return {"ok": proc.returncode == 0, "motivo": "" if proc.returncode == 0 else uscita[-400:],
                "secondi": round(time.monotonic() - inizio, 1)}
    finally:
        shutil.rmtree(cartella, ignore_errors=True)


def calibra(tasks: List[Dict[str, Any]], python: str, bwrap: Optional[str] = None,
            paralleli: int = 4) -> Dict[str, Any]:
    with ThreadPoolExecutor(max_workers=max(1, paralleli)) as pool:
        risultati = list(pool.map(lambda t: (t["task_id"], calibra_task(t, python, bwrap)), tasks))
    esiti = dict(risultati)
    return {"versione": VERSIONE, "python": python, "isolamento": "bwrap" if bwrap else "nessuno",
            "totali": len(esiti), "ok": sum(1 for e in esiti.values() if e["ok"]),
            "pericolosi": sum(1 for e in esiti.values() if e["motivo"] == "pericoloso"), "esiti": esiti}


def _scarica(url: str = URL, secondi: int = 120) -> bytes:
    richiesta = urllib.request.Request(url, headers={"User-Agent": "devin-ai-ide-training/1.0"})
    with urllib.request.urlopen(richiesta, timeout=secondi) as risposta:
        dati = risposta.read(MAX_BYTE + 1)
    if len(dati) > MAX_BYTE:
        raise ValueError(f"BigCodeBench: download oltre {MAX_BYTE} byte, scartato")
    return dati


def prepara(destinazione: Path, sorgente: Optional[Path] = None) -> Dict[str, Any]:
    """Il .jsonl.gz ufficiale (scaricato, o gia' presente in ``sorgente``) verificato e ridotto a
    tasks.jsonl. Hash diverso, righe mancanti o campi mancanti: niente file, errore."""
    dati = Path(sorgente).read_bytes() if sorgente else _scarica()
    impronta = hashlib.sha256(dati).hexdigest()
    if impronta != SHA256:
        raise ValueError(f"BigCodeBench {VERSIONE}: sha256 {impronta}, atteso {SHA256}")
    righe = [json.loads(r) for r in gzip.decompress(dati).decode("utf-8").splitlines() if r.strip()]
    if len(righe) != RIGHE or any(not isinstance(r, dict) or any(k not in r for k in CAMPI) for r in righe):
        raise ValueError(f"BigCodeBench {VERSIONE}: {len(righe)} righe o campi inattesi")
    destinazione.parent.mkdir(parents=True, exist_ok=True)
    provvisorio = destinazione.with_name(destinazione.name + ".parziale")
    with provvisorio.open("w", encoding="utf-8") as out:
        for riga in righe:
            out.write(json.dumps({k: riga[k] for k in CAMPI}, ensure_ascii=False) + "\n")
    provvisorio.replace(destinazione)
    return {"versione": VERSIONE, "sha256": impronta, "righe": len(righe), "file": str(destinazione)}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="comando", required=True)
    c = sub.add_parser("prepara")
    c.add_argument("destinazione", type=Path)
    c.add_argument("--da", type=Path, default=None)
    k = sub.add_parser("calibra")
    k.add_argument("tasks", type=Path)
    k.add_argument("destinazione", type=Path)
    k.add_argument("--bwrap", default=None)
    k.add_argument("--paralleli", type=int, default=4)
    args = parser.parse_args(argv)
    if args.comando == "prepara":
        print(json.dumps(prepara(args.destinazione, args.da)))
        return 0
    esito = calibra(leggi_tasks(args.tasks), sys.executable, args.bwrap, args.paralleli)
    args.destinazione.write_text(json.dumps(esito, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                                 encoding="utf-8")
    print(json.dumps({k: esito[k] for k in ("versione", "isolamento", "totali", "ok", "pericolosi")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
