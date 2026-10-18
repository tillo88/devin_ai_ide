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
    "Crea un pezzo 3D scrivendo codice Python build123d 0.13 (CAD parametrico, 'from build123d import *' e' gia' "
    "fatto, unita' in mm) e assegnando il pezzo finale alla variabile risultato. Lo strumento esporta STL e "
    "STEP e restituisce le misure vere: volume, ingombro X/Y/Z, se la mesh e' chiusa (stampabile), "
    "triangoli, e per la stampa FDM sbalzi, appoggio sul piano, orientamento migliore fra i 6 assi e peso se pieno. "
    "Usalo per oggetti da stampare in 3D o da modellare, poi controlla che le misure "
    "corrispondano alla richiesta e correggi. Forme base: Box(x, y, z, align=(Align.MIN,)*3); spostare e "
    "ruotare: Pos(x, y, z) * Rot(X=gradi) * pezzo; pezzi inclinati: Plane.YZ * Polygon(*punti, align=None) "
    "poi extrude(profilo, amount=larghezza); somma a + b, sottrazione a - b.")

# 18/10: senza esempi il modello ha provato sei volte la sintassi di rotate/Rotation/Box(centered=...)
# prima di arrivare al pezzo. Esempi provati su build123d 0.13.0 (la versione del lock): arrivano
# insieme al primo errore, quando servono.
PROMEMORIA = """PROMEMORIA build123d 0.13 (esempi verificati, unita' in mm):
- parallelepipedo con l'angolo in (0,0,0): Box(70, 80, 4, align=(Align.MIN, Align.MIN, Align.MIN))
- spostare/ruotare (si moltiplica a SINISTRA): Pos(0, 10, 4) * Rot(X=-30) * Box(70, 4, 100, align=(Align.MIN,)*3)
- ruotare un pezzo gia' fatto: pezzo.rotate(Axis.Z, 90)   (asse, gradi)
- pezzo con parti inclinate: disegna il profilo laterale e estrudi:
    profilo = Plane.YZ * Polygon((0, 0), (80, 0), (80, 4), (20, 4), (70, 90), (66, 92), (14, 4), (0, 4), align=None)
    pezzo = extrude(profilo, amount=70)   # spessore lungo X
- unione e sottrazione: a + b, a - b; foro: pezzo - Pos(35, 40, 0) * Cylinder(5, 10)
- raccordo: fillet(pezzo.edges().filter_by(Axis.Z), radius=2)
- NON esistono: Box(..., centered=...), Rotation(asse, angolo), pezzo.rotate(vettore, angolo); is_valid e' una proprieta'.
- alla fine: risultato = pezzo"""

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
import numpy as _np
_n, _a, _c = _m.face_normals, _m.area_faces, _m.triangles_center
_orient = {}
for _nome_o, _giu in (("Z-", (0, 0, -1)), ("Z+", (0, 0, 1)), ("X-", (-1, 0, 0)), ("X+", (1, 0, 0)),
                      ("Y-", (0, -1, 0)), ("Y+", (0, 1, 0))):
    _g = _np.array(_giu, dtype=float)
    _h = -(_c @ _g)
    _fondo = float((-(_m.vertices @ _g)).min())
    _sul_piano = (_n @ _g > 0.9998) & (_h - _fondo < 0.01)
    _sbalzo = (_n @ _g > 0.7071) & ~_sul_piano
    _orient[_nome_o] = {"appoggio_mm2": round(float(_a[_sul_piano].sum()), 1),
                        "sbalzi_mm2": round(float(_a[_sbalzo].sum()), 1)}
print("DEVIN_MISURE " + _json.dumps({
    "volume_mm3": round(float(_m.volume), 2), "area_mm2": round(float(_m.area), 2),
    "ingombro_mm": [round(float(_b[1][i] - _b[0][i]), 3) for i in range(3)],
    "min_mm": [round(float(x), 3) for x in _b[0]], "chiusa": bool(_m.is_watertight),
    "solido_valido": bool(_m.is_volume), "triangoli": int(len(_m.faces)),
    "corpi": int(len(_m.split(only_watertight=False))),
    "volume_cad_mm3": round(float(getattr(_pezzo, "volume", 0.0)), 2),
    "orientamenti": _orient,
    "file": [p for p in (_stl, _step) if p]}))
'''


# Sbalzo: faccia rivolta verso il basso a meno di 45 gradi dall'orizzontale, non appoggiata sul
# piano. E' la soglia comune per stampare senza supporti in FDM; la scelta del valore va detta,
# non nascosta: per questo compare nel testo.
SOGLIA_SBALZO_GRADI = 45
PLA_G_CM3 = 1.24
FILO_MM = 1.75
ORIENTAMENTI = {"Z-": "com'e' (base in basso)", "Z+": "capovolto", "X-": "sul fianco X-", "X+": "sul fianco X+",
                "Y-": "sul fianco Y-", "Y+": "sul fianco Y+"}


def stampabilita(misure: Dict[str, Any]) -> List[str]:
    """Righe sulla stampa FDM: sbalzi e appoggio per orientamento, peso se pieno."""
    orient = misure.get("orientamenti") or {}
    righe: List[str] = []
    attuale = orient.get("Z-")
    if attuale is not None:
        area = float(misure.get("area_mm2") or 0) or 1.0
        righe.append(f"stampa (FDM, sbalzi oltre {SOGLIA_SBALZO_GRADI} gradi): com'e' appoggia "
                     f"{attuale['appoggio_mm2']} mm², sbalzi {attuale['sbalzi_mm2']} mm² "
                     f"({round(100 * attuale['sbalzi_mm2'] / area, 1)}% della superficie)"
                     + (" -> servono supporti o un altro orientamento" if attuale['sbalzi_mm2'] > 0 else ""))
        if attuale["appoggio_mm2"] <= 0:
            righe.append("ATTENZIONE: com'e' nessuna faccia piana appoggia sul piano di stampa")
        candidati = [(v["sbalzi_mm2"], -v["appoggio_mm2"], k) for k, v in orient.items() if v["appoggio_mm2"] > 0]
        if candidati:
            sbalzi, appoggio, migliore = min(candidati)
            if migliore != "Z-" and (sbalzi < attuale["sbalzi_mm2"] or attuale["appoggio_mm2"] <= 0):
                righe.append(f"orientamento migliore fra i 6 assi: {ORIENTAMENTI[migliore]} "
                             f"(sbalzi {sbalzi} mm², appoggio {-appoggio} mm²)")
    volume = float(misure.get("volume_mm3") or 0)
    if volume > 0:
        grammi = volume / 1000 * PLA_G_CM3
        metri = volume / (3.141592653589793 * (FILO_MM / 2) ** 2) / 1000
        righe.append(f"materiale se pieno al 100% (limite superiore, PLA {PLA_G_CM3} g/cm³): "
                     f"{round(grammi, 1)} g, {round(metri, 2)} m di filo da {FILO_MM} mm; "
                     "con riempimento parziale serve meno, il valore vero lo da' lo slicer")
    return righe


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
            return f"ERRORE nel codice del pezzo (exit {proc.returncode}):\n{errore}\n\n{PROMEMORIA}"
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
    righe_testo.extend(stampabilita(misure))
    if avviso:
        righe_testo.append(f"nota: {avviso}")
    return "\n".join(righe_testo)
