"""Modellazione 3D di DEVIN: build123d (CAD a codice) + controlli della mesh.

Passo 4 della roadmap. DEVIN scrive il pezzo come codice build123d (o
CadQuery/OCP, stesso nucleo OpenCascade) e assegna il risultato a
``risultato``; qui lo si esporta in STL e STEP e lo si misura con trimesh:
volume, ingombro, chiusura (watertight), triangoli. Il modello riceve numeri
veri da confrontare con quello che gli e' stato chiesto (misure, fori,
spessori) invece di immaginare la forma.

Gira con l'interprete dell'ambiente strumenti (venv-strumenti), senza rete
(bwrap), con tempo e memoria limitati, come il risolutore.

23/10: in alternativa il pezzo si scrive in OpenSCAD (``linguaggio='openscad'``),
la miglioria che DEVIN aveva chiesto e Alessandro approvato: OpenSCAD lo esporta
in STL nella stessa sandbox e le misure sono le stesse (niente STEP: OpenSCAD
lavora a mesh).
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
    "fatto, come radians, degrees, sin, cos, tan, atan2, sqrt, hypot e pi di math; unita' in mm) e assegnando il pezzo finale alla variabile risultato. Lo strumento esporta STL e "
    "STEP e restituisce le misure vere: volume, ingombro X/Y/Z, se la mesh e' chiusa (stampabile), "
    "triangoli, e per la stampa FDM sbalzi, appoggio sul piano, orientamento migliore fra i 6 assi, peso se pieno "
    "e, se lo slicer e' installato, tempo di stampa, filamento e costo veri (G-code) per la stampante di "
    "Alessandro (Bambu Lab P2S) col materiale e la qualita' scelti. "
    "Usalo per oggetti da stampare in 3D o da modellare, poi controlla che le misure "
    "corrispondano alla richiesta e correggi. Forme base: Box(x, y, z, align=(Align.MIN,)*3); spostare e "
    "ruotare: Pos(x, y, z) * Rot(X=gradi) * pezzo; pezzi inclinati: Plane.YZ * Polygon(*punti, align=None) "
    "poi extrude(profilo, amount=larghezza); somma a + b, sottrazione a - b. "
    "In alternativa linguaggio='openscad': il codice e' OpenSCAD (cube, cylinder, sphere, translate, rotate, "
    "difference, union, hull, linear_extrude, moduli e variabili), esportato in STL con le stesse misure; "
    "senza STEP.")

OPENSCAD = "/usr/bin/openscad"
LINGUAGGI = ("build123d", "openscad")

PROMEMORIA_SCAD = """PROMEMORIA OpenSCAD (unita' in mm):
- parallelepipedo con l'angolo in (0,0,0): cube([70, 80, 4]);   centrato: cube([70, 80, 4], center=true);
- cilindro: cylinder(h=10, r=5, $fn=64);   cono: cylinder(h=10, r1=5, r2=2, $fn=64);
- spostare/ruotare (si applica a quello che segue): translate([0, 10, 4]) rotate([-30, 0, 0]) cube([70, 4, 100]);
- foro: difference() { cube([70, 80, 4]); translate([35, 40, -1]) cylinder(h=6, r=5, $fn=64); }
- profilo estruso: linear_extrude(height=70) polygon([[0,0],[80,0],[80,4],[0,4]]);
- parametri e moduli: spessore = 4; module staffa(l) { cube([l, 20, spessore]); } staffa(60);
- ogni istruzione finisce con ';'; il pezzo e' quello che resta disegnato al livello piu' alto"""

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
- misure dentro il codice: bb = pezzo.bounding_box(); bb.min.X, bb.max.Z, bb.size.Y; corpi: len(pezzo.solids())
- extrude e' una FUNZIONE: extrude(profilo, amount=70); profilo.extrude(...) non esiste
- trigonometria gia' importata da math: radians, degrees, sin, cos, tan, atan2, sqrt, hypot, pi
- NON esistono: Box(..., centered=...), Rotation(asse, angolo), pezzo.rotate(vettore, angolo), is_watertight();
  is_valid e' una proprieta' (pezzo.is_valid, senza parentesi). Le misure le restituisce gia' lo strumento.
- alla fine: risultato = pezzo"""

def _parametri_stampa() -> Dict[str, Any]:
    """Materiale e qualita' dai profili della stampante (config/stampante_p2s.json), se leggibili."""
    from devin.core import affetta

    try:
        materiali, qualita = affetta.nomi()
        predefiniti = affetta.profili()["predefiniti"]
    except (OSError, ValueError, KeyError):
        return {}
    return {
        "materiale": {"type": "string", "enum": materiali,
                      "description": f"filamento per lo slicer (predefinito {predefiniti['materiale']})"},
        "qualita": {"type": "string", "enum": qualita,
                    "description": f"profilo di stampa (predefinito {predefiniti['qualita']}; "
                                   "hueforge per le stampe HueForge, TPU per i flessibili)"},
    }


STRUMENTO = {"type": "function", "function": {
    "name": "modella_3d",
    "description": DESCRIZIONE,
    "parameters": {"type": "object", "properties": {
        "codice": {"type": "string", "description": "codice build123d che assegna il pezzo a 'risultato'"},
        "nome": {"type": "string", "description": "nome del file, es. supporto_telefono"},
        "linguaggio": {"type": "string", "enum": list(LINGUAGGI),
                       "description": "build123d (predefinito, Python) oppure openscad"},
        **_parametri_stampa()},
        "required": ["codice"], "additionalProperties": False}}}

# 19/10: DEVIN ha scritto radians(60) e lo strumento ha risposto NameError: il preludio
# importava solo build123d. Le funzioni di math servono a ogni pezzo inclinato.
PRELUDIO = ("from build123d import *\n"
            "from math import radians, degrees, sin, cos, tan, asin, acos, atan, atan2, sqrt, hypot, pi\n")

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
#DEVIN_MISURE_DA_QUI
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
def _anteprima(_m, _percorso, _lato=380):
    """Quattro viste (isometrica, fronte, alto, destra) in un PNG: per farsi guardare dal modello."""
    from PIL import Image as _Img, ImageDraw as _Draw
    _V = _np.asarray(_m.vertices, dtype=float)
    _F = _np.asarray(_m.faces)
    _N = _np.asarray(_m.face_normals, dtype=float)
    _centro = (_V.max(0) + _V.min(0)) / 2
    _V = _V - _centro

    def _rot(_ax, _gradi):
        _t = _np.radians(_gradi); _c, _s = _np.cos(_t), _np.sin(_t)
        if _ax == "x":
            return _np.array([[1, 0, 0], [0, _c, -_s], [0, _s, _c]])
        if _ax == "y":
            return _np.array([[_c, 0, _s], [0, 1, 0], [-_s, 0, _c]])
        return _np.array([[_c, -_s, 0], [_s, _c, 0], [0, 0, 1]])
    # base: guardare lungo -Y verso +Y significa x a destra, z in alto -> ruotare -90 su X
    _fronte = _rot("x", -90)
    _viste = [
        ("isometrica", _rot("x", -90 + 35.264) @ _rot("z", -45)),
        ("fronte (da -Y)", _fronte),
        ("alto (da +Z)", _np.eye(3)),
        ("destra (da +X)", _rot("x", -90) @ _rot("z", -90)),
    ]
    _luce = _np.array([0.35, 0.45, 1.0]); _luce /= _np.linalg.norm(_luce)
    # spigoli vivi (facce adiacenti a piu' di 20 gradi): si disegnano con la loro faccia,
    # cosi' le facce davanti li coprono come coprono il resto
    _spigoli = {}
    if len(_m.face_adjacency):
        _vivi = _m.face_adjacency_angles > _np.radians(20)
        for (_fa, _fb), _e in zip(_m.face_adjacency[_vivi], _m.face_adjacency_edges[_vivi]):
            _spigoli.setdefault(int(_fa), []).append(_e)
            _spigoli.setdefault(int(_fb), []).append(_e)
    _img = _Img.new("RGB", (_lato * 2, _lato * 2 + 24), "white")
    _d = _Draw.Draw(_img)
    for _k, (_titolo, _R) in enumerate(_viste):
        _P = _V @ _R.T
        _Nr = _N @ _R.T
        _ox, _oy = (_k % 2) * _lato, (_k // 2) * _lato
        _span = float(max(_np.ptp(_P[:, 0]), _np.ptp(_P[:, 1]), 1e-9))
        _sc = (_lato - 50) / _span
        _xy = _np.stack([_ox + _lato / 2 + _P[:, 0] * _sc, _oy + _lato / 2 + 10 - _P[:, 1] * _sc], 1)
        _vis = _Nr[:, 2] > 1e-6
        _prof = _P[_F][:, :, 2].mean(1)
        _pmin, _pmax = float(_prof.min()), float(_prof.max())
        for _i in _np.argsort(_prof):
            if not _vis[_i]:
                continue
            # profondita' solo per le facce viste di fronte (viste ortogonali): un piano
            # inclinato coi triangoli a profondita' diverse mostrerebbe la triangolazione
            _vicino = (float(_prof[_i]) - _pmin) / ((_pmax - _pmin) or 1.0) if _Nr[_i, 2] > 0.999 else 1.0
            _l = (0.25 + 0.75 * max(0.0, float(_Nr[_i] @ _luce))) * (0.7 + 0.3 * _vicino)
            _col = (int(60 + 160 * _l), int(95 + 135 * _l), int(140 + 105 * _l))
            _pts = [tuple(p) for p in _xy[_F[_i]]]
            _d.polygon(_pts, fill=_col, outline=_col)
            for _a, _b2 in _spigoli.get(int(_i), ()):
                _d.line([tuple(_xy[_a]), tuple(_xy[_b2])], fill=(35, 45, 60), width=1)
        _d.rectangle((_ox, _oy, _ox + _lato - 1, _oy + _lato - 1), outline=(200, 200, 200))
        _d.text((_ox + 8, _oy + 6), _titolo, fill=(30, 30, 30))
    _b = _m.bounds
    _d.text((8, _lato * 2 + 6), "ingombro %.1f x %.1f x %.1f mm (X x Y x Z)" % tuple(_b[1] - _b[0]), fill=(30, 30, 30))
    _img.save(_percorso, optimize=True)


_anteprima_file = _os.path.join(_out, _nome + "-anteprima.png")
try:
    if len(_m.faces) > 200000:
        raise ValueError("troppi triangoli per l'anteprima")
    _anteprima(_m, _anteprima_file)
except Exception as _e:
    print("DEVIN_ANTEPRIMA_ERRORE " + type(_e).__name__ + ": " + str(_e)[:200])
    _anteprima_file = None
print("DEVIN_MISURE " + _json.dumps({
    "volume_mm3": round(float(_m.volume), 2), "area_mm2": round(float(_m.area), 2),
    "ingombro_mm": [round(float(_b[1][i] - _b[0][i]), 3) for i in range(3)],
    "min_mm": [round(float(x), 3) for x in _b[0]], "chiusa": bool(_m.is_watertight),
    "solido_valido": bool(_m.is_volume), "triangoli": int(len(_m.faces)),
    "corpi": int(len(_m.split(only_watertight=False))),
    "volume_cad_mm3": round(float(getattr(_pezzo, "volume", 0.0)), 2),
    "orientamenti": _orient,
    "file": [p for p in (_stl, _step, _anteprima_file) if p]}))
'''


# Sbalzo: faccia rivolta verso il basso a meno di 45 gradi dall'orizzontale, non appoggiata sul
# piano. E' la soglia comune per stampare senza supporti in FDM; la scelta del valore va detta,
# non nascosta: per questo compare nel testo.
SOGLIA_SBALZO_GRADI = 45
PLA_G_CM3 = 1.24
FILO_MM = 1.75
ORIENTAMENTI = {"Z-": "com'e' (base in basso)", "Z+": "capovolto", "X-": "sul fianco X-", "X+": "sul fianco X+",
                "Y-": "sul fianco Y-", "Y+": "sul fianco Y+"}


ESPORTA_B123D, _MISURE = POSTLUDIO.split("#DEVIN_MISURE_DA_QUI\n")

# OpenSCAD: si esporta in STL dalla stessa sandbox, poi le stesse misure del build123d
ESPORTA_SCAD = r'''
import json as _json, os as _os, subprocess as _subprocess
import trimesh as _trimesh
_out = _os.environ["DEVIN_OUT"]
_nome = _os.environ["DEVIN_NOME"]
_stl = _os.path.join(_out, _nome + ".stl")
_step = None
_pezzo = None
_scad = _subprocess.run([_os.environ["DEVIN_OPENSCAD"], "-o", _stl, "pezzo.scad"], capture_output=True, text=True,
                        env={"PATH": "/usr/bin:/bin", "HOME": _os.environ["HOME"], "LANG": "C.UTF-8",
                             "QT_QPA_PLATFORM": "offscreen"})
if _scad.returncode != 0 or not _os.path.isfile(_stl) or _os.path.getsize(_stl) == 0:
    raise SystemExit("ERRORE OpenSCAD (exit %s):\n%s" % (_scad.returncode, (_scad.stderr or _scad.stdout)[-4000:]))
'''


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


def _affettato(misure: Dict[str, Any], salvati: List[str], config: Optional[Dict[str, Any]],
               materiale: str = "", qualita: str = "") -> List[str]:
    """Se lo slicer c'e', tempo, filamento e costo veri col profilo della stampante."""
    from devin.core import affetta

    if not affetta.disponibile(config)[0]:
        return []
    stl = next((Path(f) for f in salvati if f.lower().endswith(".stl")), None)
    if stl is None or not misure.get("chiusa"):
        return ["slicer: saltato (mesh non chiusa o STL mancante)"]
    sbalzi = float(((misure.get("orientamenti") or {}).get("Z-") or {}).get("sbalzi_mm2") or 0)
    return [affetta.riga(affetta.affetta(stl, sbalzi=sbalzi > 0, config=config,
                                         materiale=materiale, qualita=qualita))]


def openscad(config: Optional[Dict[str, Any]] = None) -> Path:
    return Path(str((((config or {}).get("tools") or {}).get("openscad") or {}).get("bin") or OPENSCAD))


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
            config: Optional[Dict[str, Any]] = None, secondi: int = 180,
            materiale: str = "", qualita: str = "", linguaggio: str = "build123d") -> str:
    """Testo per il modello (mai un'eccezione). I file finiscono in ``destinazione``."""
    pronto, motivo = disponibile(config)
    if not pronto:
        return f"ERRORE: modellazione 3D non disponibile: {motivo}"
    if not str(codice or "").strip():
        return "ERRORE: 'codice' vuoto"
    linguaggio = str(linguaggio or "build123d").strip().lower()
    if linguaggio not in LINGUAGGI:
        return f"ERRORE: linguaggio {linguaggio!r} sconosciuto (build123d o openscad)"
    scad = linguaggio == "openscad"
    if scad and not (openscad(config).is_file() and os.access(openscad(config), os.X_OK)):
        return f"ERRORE: OpenSCAD non installato sul rig ({openscad(config)}): usa build123d"
    nome = _nome_sicuro(nome)
    limite = max(10, min(MAX_SECONDI, int(secondi or 180)))
    destinazione = Path(destinazione or Path(os.path.expanduser("~/.local/share/devin/modelli"))
                        / time.strftime("%Y%m%d-%H%M%S"))
    with tempfile.TemporaryDirectory(prefix="devin-3d-") as tmp:
        cartella = Path(tmp)
        uscita_dir = cartella / "out"
        uscita_dir.mkdir()
        script = cartella / "pezzo.py"
        if scad:
            (cartella / "pezzo.scad").write_text(str(codice), encoding="utf-8")
            script.write_text(ESPORTA_SCAD + _MISURE, encoding="utf-8")
        else:
            script.write_text(PRELUDIO + str(codice) + POSTLUDIO, encoding="utf-8")
        argv, avviso = _comando(interprete(config), script, cartella)
        env = {"PATH": "/usr/bin:/bin", "HOME": str(cartella), "LANG": "C.UTF-8", "TMPDIR": str(cartella),
               "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1",
               "DEVIN_OUT": str(uscita_dir), "DEVIN_NOME": nome, "DEVIN_OPENSCAD": str(openscad(config))}
        try:
            from devin.engine.processi import run_limitato
            proc = run_limitato(argv, cwd=str(cartella), env=env, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=limite,
                                  stdin=subprocess.DEVNULL, preexec_fn=_limiti)
        except subprocess.TimeoutExpired:
            return f"TIMEOUT: il modello non e' stato generato in {limite} s (semplifica le operazioni)"
        righe = [r for r in (proc.stdout or "").splitlines() if r.startswith("DEVIN_MISURE ")]
        if proc.returncode != 0 or not righe:
            errore = (proc.stderr or proc.stdout or "").strip()[-MAX_USCITA:]
            return (f"ERRORE nel codice del pezzo (exit {proc.returncode}):\n{errore}\n\n"
                    f"{PROMEMORIA_SCAD if scad else PROMEMORIA}")
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
        + ("" if scad else f" (CAD {misure['volume_cad_mm3']})") + f", superficie {misure['area_mm2']} mm²"
        + (" (OpenSCAD)" if scad else ""),
        f"mesh: {'chiusa (stampabile)' if misure['chiusa'] else 'NON chiusa: buchi o facce aperte'}, "
        f"{'solido valido' if misure['solido_valido'] else 'solido NON valido'}, {misure['triangoli']} triangoli, "
        f"{misure['corpi']} {'corpo' if misure['corpi'] == 1 else 'corpi separati'}",
        f"punto minimo: {misure['min_mm']} (per la stampa il pezzo dovrebbe appoggiare su Z=0)",
        "file: " + ", ".join(s for s in salvati if not s.endswith("-anteprima.png")),
    ]
    anteprima = next((s for s in salvati if s.endswith("-anteprima.png")), None)
    if anteprima:
        righe_testo.append(f"anteprima: {anteprima} (vista isometrica, fronte, alto, destra)")
    righe_testo.extend(stampabilita(misure))
    righe_testo.extend(_affettato(misure, salvati, config, str(materiale or ""), str(qualita or "")))
    if avviso:
        righe_testo.append(f"nota: {avviso}")
    return "\n".join(righe_testo)
