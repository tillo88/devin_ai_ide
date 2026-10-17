"""Modellazione 3D di DEVIN: build123d (CAD a codice) + controlli della mesh.

Passo 4 della roadmap. DEVIN scrive il pezzo come codice build123d (o
CadQuery/OCP, stesso nucleo OpenCascade) e assegna il risultato a
``risultato``; qui lo si esporta in STL e STEP e lo si misura con trimesh:
volume, ingombro, chiusura (watertight), triangoli. Il modello riceve numeri
veri da confrontare con quello che gli e' stato chiesto (misure, fori,
spessori) invece di immaginare la forma.

Gira con l'interprete dell'ambiente strumenti (venv-strumenti), senza rete
(bwrap), con tempo e memoria limitati, come il risolutore.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from typing import Any, Dict, List, Optional

from devin.core.risolutore import _comando, _limiti, interprete

MAX_SECONDI = 300
MAX_USCITA = 6000

DESCRIZIONE = (
    "Crea un pezzo 3D scrivendo codice Python build123d (CAD parametrico, 'from build123d import *' e' gia' "
    "fatto, unita' in mm) e assegnando il pezzo finale alla variabile risultato. Lo strumento esporta STL e "
    "STEP e restituisce le misure vere: volume, ingombro X/Y/Z, se la mesh e' chiusa (stampabile), "
    "triangoli. Usalo per oggetti da stampare in 3D o da modellare, poi controlla che le misure "
    "corrispondano alla richiesta e correggi.")

STRUMENTO = {"type": "function", "function": {
    "name": "modella_3d",
    "description": DESCRIZIONE,
    "parameters": {"type": "object", "properties": {
        "codice": {"type": "string", "description": "codice build123d che assegna il pezzo a 'risultato'"},
        "nome": {"type": "string", "description": "nome del file, es. supporto_telefono"}},
        "required": ["codice"], "additionalProperties": False}}}

PRELUDIO = "from build123d import *\n"

POSTLUDIO = r'''

import json as _json, os as _os
import trimesh as _trimesh
_out = _os.environ["DEVIN_OUT"]
_nome = _os.environ["DEVIN_NOME"]
try:
    _pezzo = risultato
except NameError:
    raise SystemExit("ERRORE: il codice deve assegnare il pezzo finale alla variabile 'risultato'")
_stl = _os.path.join(_out, _nome + ".stl")
_step = _os.path.join(_out, _nome + ".step")
export_stl(_pezzo, _stl)
try:
    export_step(_pezzo, _step)
except Exception as _e:
    _step = None
_m = _trimesh.load(_stl, force="mesh")
_b = _m.bounds
print("DEVIN_MISURE " + _json.dumps({
    "volume_mm3": round(float(_m.volume), 2), "area_mm2": round(float(_m.area), 2),
    "ingombro_mm": [round(float(_b[1][i] - _b[0][i]), 3) for i in range(3)],
    "min_mm": [round(float(x), 3) for x in _b[0]], "chiusa": bool(_m.is_watertight),
    "solido_valido": bool(_m.is_volume), "triangoli": int(len(_m.faces)),
    "corpi": int(len(_m.split(only_watertight=False))),
    "volume_cad_mm3": round(float(getattr(_pezzo, "volume", 0.0)), 2),
    "file": [p for p in (_stl, _step) if p]}))
'''


def disponibile(config: Optional[Dict[str, Any]] = None) -> tuple[bool, str]:
    python = interprete(config)
    if not python.exists():
        return False, f"ambiente strumenti non installato ({python})"
    pacchetti = list(python.parent.parent.glob("lib/python3*/site-packages"))
    for modulo in ("build123d", "trimesh"):
        if not any((sp / modulo).is_dir() for sp in pacchetti):
            return False, f"{modulo} non installato nell'ambiente strumenti"
    return True, "pronto"


def _nome_sicuro(nome: str) -> str:
    pulito = re.sub(r"[^A-Za-z0-9_-]+", "_", str(nome or "").strip())[:60].strip("_")
    return pulito or "pezzo"


def modella(codice: str, nome: str = "pezzo", destinazione: Optional[Path] = None,
            config: Optional[Dict[str, Any]] = None, secondi: int = 180) -> str:
    """Testo per il modello (mai un'eccezione). I file finiscono in ``destinazione``."""
    pronto, motivo = disponibile(config)
    if not pronto:
        return f"ERRORE: modellazione 3D non disponibile: {motivo}"
    if not str(codice or "").strip():
        return "ERRORE: 'codice' vuoto"
    nome = _nome_sicuro(nome)
    limite = max(10, min(MAX_SECONDI, int(secondi or 180)))
    destinazione = Path(destinazione or Path(os.path.expanduser("~/.local/share/devin/modelli"))
                        / time.strftime("%Y%m%d-%H%M%S"))
    with tempfile.TemporaryDirectory(prefix="devin-3d-") as tmp:
        cartella = Path(tmp)
        uscita_dir = cartella / "out"
        uscita_dir.mkdir()
        script = cartella / "pezzo.py"
        script.write_text(PRELUDIO + str(codice) + POSTLUDIO, encoding="utf-8")
        argv, avviso = _comando(interprete(config), script, cartella)
        env = {"PATH": "/usr/bin:/bin", "HOME": str(cartella), "LANG": "C.UTF-8", "TMPDIR": str(cartella),
               "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1",
               "DEVIN_OUT": str(uscita_dir), "DEVIN_NOME": nome}
        try:
            proc = subprocess.run(argv, cwd=str(cartella), env=env, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=limite,
                                  stdin=subprocess.DEVNULL, preexec_fn=_limiti)
        except subprocess.TimeoutExpired:
            return f"TIMEOUT: il modello non e' stato generato in {limite} s (semplifica le operazioni)"
        righe = [r for r in (proc.stdout or "").splitlines() if r.startswith("DEVIN_MISURE ")]
        if proc.returncode != 0 or not righe:
            errore = (proc.stderr or proc.stdout or "").strip()[-MAX_USCITA:]
            return f"ERRORE nel codice del pezzo (exit {proc.returncode}):\n{errore}"
        misure = json.loads(righe[-1][len("DEVIN_MISURE "):])
        destinazione.mkdir(parents=True, exist_ok=True)
        salvati: List[str] = []
        for file in misure.get("file") or []:
            origine = Path(file)
            if origine.is_file() and origine.parent == uscita_dir:
                shutil.copy2(origine, destinazione / origine.name)
                salvati.append(str(destinazione / origine.name))
    x, y, z = misure["ingombro_mm"]
    righe_testo = [
        f"pezzo '{nome}': ingombro {x} x {y} x {z} mm (X x Y x Z), volume {misure['volume_mm3']} mm³"
        f" (CAD {misure['volume_cad_mm3']}), superficie {misure['area_mm2']} mm²",
        f"mesh: {'chiusa (stampabile)' if misure['chiusa'] else 'NON chiusa: buchi o facce aperte'}, "
        f"{'solido valido' if misure['solido_valido'] else 'solido NON valido'}, {misure['triangoli']} triangoli, "
        f"{misure['corpi']} {'corpo' if misure['corpi'] == 1 else 'corpi separati'}",
        f"punto minimo: {misure['min_mm']} (per la stampa il pezzo dovrebbe appoggiare su Z=0)",
        "file: " + ", ".join(salvati),
    ]
    if avviso:
        righe_testo.append(f"nota: {avviso}")
    return "\n".join(righe_testo)
