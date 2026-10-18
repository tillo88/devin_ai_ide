"""Slicer per i pezzi di DEVIN: PrusaSlicer senza schermo sul rig.

Roadmap 4 (18/10): dopo ``modella_3d`` il modello riceve tempo di stampa e
filamento veri invece della stima "se pieno al 100%". Lo slicer e' il pacchetto
di Ubuntu installato da ``devin-tools-system-deps-v1`` (ai-rig-ops); qui lo si
chiama da riga di comando su un STL gia' generato, con un profilo FDM generico
dichiarato (ugello, strato, riempimento, filo) che la configurazione puo'
cambiare in ``tools.slicer``. Il profilo usato compare sempre nel testo: il
modello e l'utente sanno su che ipotesi e' fatto il conto.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import resource
import subprocess
import tempfile
from typing import Any, Dict, Optional

SLICER = "/usr/bin/prusa-slicer"
SECONDI = 180
MEMORIA_BYTE = 4 * 1024 ** 3
PROFILO = {
    "ugello_mm": 0.4,
    "strato_mm": 0.2,
    "riempimento_pct": 15,
    "perimetri": 2,
    "filo_mm": 1.75,
    "densita_g_cm3": 1.24,       # PLA
    "piatto_mm": [220, 220],
    "altezza_mm": 250,
}

_TEMPO = re.compile(r"^; estimated printing time \(normal mode\) = (.+)$", re.M)
_GRAMMI = re.compile(r"^; total filament used \[g\] = ([0-9.]+)", re.M)
_MM = re.compile(r"^; filament used \[mm\] = ([0-9.]+)", re.M)
_STRATI = re.compile(r"^;LAYER_CHANGE$", re.M)


def _config_slicer(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return ((config or {}).get("tools") or {}).get("slicer") or {}


def binario(config: Optional[Dict[str, Any]] = None) -> Path:
    return Path(str(_config_slicer(config).get("bin") or SLICER))


def disponibile(config: Optional[Dict[str, Any]] = None) -> tuple[bool, str]:
    percorso = binario(config)
    if not percorso.is_file() or not os.access(percorso, os.X_OK):
        return False, f"slicer non installato ({percorso})"
    return True, "pronto"


def profilo(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Il profilo generico con le sole chiavi note cambiate dalla configurazione."""
    scelto = dict(PROFILO)
    for chiave, valore in (_config_slicer(config).get("profilo") or {}).items():
        if chiave not in PROFILO:
            raise ValueError(f"tools.slicer.profilo: chiave sconosciuta {chiave!r}")
        scelto[chiave] = valore
    return scelto


def _limiti() -> None:  # pragma: no cover - gira nel processo figlio
    os.setsid()
    try:
        resource.setrlimit(resource.RLIMIT_AS, (MEMORIA_BYTE, MEMORIA_BYTE))
    except (ValueError, OSError):
        pass


def argomenti(stl: Path, gcode: Path, p: Dict[str, Any], supporti: bool) -> list[str]:
    larghezza, profondita = (float(v) for v in p["piatto_mm"])
    argv = [
        "--export-gcode", "--output", str(gcode),
        "--nozzle-diameter", str(p["ugello_mm"]),
        "--layer-height", str(p["strato_mm"]), "--first-layer-height", str(p["strato_mm"]),
        "--fill-density", f"{p['riempimento_pct']}%", "--perimeters", str(int(p["perimetri"])),
        "--filament-diameter", str(p["filo_mm"]), "--filament-density", str(p["densita_g_cm3"]),
        "--bed-shape", f"0x0,{larghezza:g}x0,{larghezza:g}x{profondita:g},0x{profondita:g}",
        "--max-print-height", str(p["altezza_mm"]),
        "--center", f"{larghezza / 2:g},{profondita / 2:g}", "--gcode-flavor", "marlin2",
    ]
    if supporti:
        argv += ["--support-material", "--support-material-auto"]
    return argv + [str(stl)]


def affetta(stl: Path, supporti: bool = False, config: Optional[Dict[str, Any]] = None,
            destinazione: Optional[Path] = None, secondi: int = SECONDI) -> Dict[str, Any]:
    """Affetta ``stl``. Restituisce le misure o ``{"errore": ...}``, mai un'eccezione."""
    pronto, motivo = disponibile(config)
    if not pronto:
        return {"errore": motivo}
    try:
        p = profilo(config)
    except ValueError as exc:
        return {"errore": str(exc)}
    stl = Path(stl)
    if stl.suffix.lower() != ".stl" or not stl.is_file():
        return {"errore": f"non e' un file STL: {stl}"}
    destinazione = Path(destinazione or stl.parent)
    gcode = destinazione / (stl.stem + ".gcode")
    with tempfile.TemporaryDirectory(prefix="devin-slicer-") as casa:
        env = {"PATH": "/usr/bin:/bin", "HOME": casa, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}
        try:
            proc = subprocess.run([str(binario(config)), *argomenti(stl, gcode, p, supporti)],
                                  capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  timeout=secondi, stdin=subprocess.DEVNULL, env=env, cwd=casa,
                                  preexec_fn=_limiti)
        except subprocess.TimeoutExpired:
            return {"errore": f"lo slicer non ha finito in {secondi} s"}
    if proc.returncode != 0 or not gcode.is_file():
        dettaglio = (proc.stderr or proc.stdout or "").strip()[-600:]
        return {"errore": f"slicer fallito (exit {proc.returncode}): {dettaglio}"}
    testo = gcode.read_text(encoding="utf-8", errors="replace")
    tempo, grammi, mm = _TEMPO.search(testo), _GRAMMI.search(testo), _MM.search(testo)
    if tempo is None:
        return {"errore": "lo slicer non ha scritto la stima del tempo nel G-code"}
    return {
        "tempo": tempo.group(1).strip(),
        "grammi": float(grammi.group(1)) if grammi else None,
        "metri": round(float(mm.group(1)) / 1000, 2) if mm else None,
        "strati": len(_STRATI.findall(testo)),
        "supporti": bool(supporti),
        "profilo": p,
        "gcode": str(gcode),
    }


def riga(esito: Dict[str, Any]) -> str:
    """Una riga di testo per il modello."""
    if esito.get("errore"):
        return f"slicer: {esito['errore']}"
    p = esito["profilo"]
    return (f"slicer (PrusaSlicer, profilo generico: ugello {p['ugello_mm']} mm, strato {p['strato_mm']} mm, "
            f"riempimento {p['riempimento_pct']}%, {p['perimetri']} perimetri, filo {p['filo_mm']} mm, "
            f"supporti {'si' if esito['supporti'] else 'no'}): tempo {esito['tempo']}, "
            f"filamento {esito['grammi']} g ({esito['metri']} m), {esito['strati']} strati; G-code {esito['gcode']}")
