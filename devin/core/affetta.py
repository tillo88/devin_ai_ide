"""Slicer per i pezzi di DEVIN: PrusaSlicer senza schermo sul rig.

Roadmap 4 (18/10): dopo ``modella_3d`` il modello riceve tempo di stampa,
filamento e costo veri invece della stima "se pieno al 100%". Lo slicer e' il
pacchetto di Ubuntu installato da ``devin-tools-system-deps-v1`` (ai-rig-ops).

19/10: i profili non sono piu' generici. ``config/stampante_p2s.json`` contiene la
stampante di Alessandro (Bambu Lab P2S) e i suoi preset di Bambu Studio tradotti
per PrusaSlicer: materiali (PLA Matte/Basic HF, PETG HF, ABS Sunlu, TPU) e
qualita' (standard, robusto, PETG, TPU, HueForge). Il modello sceglie materiale e
qualita' per nome; materiale, qualita' e stampante usati compaiono sempre nel
testo. ``tools.slicer.profili`` nella configurazione puo' indicare un altro file.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import resource
import subprocess
import tempfile
from typing import Any, Dict, Optional

SLICER = "/usr/bin/prusa-slicer"
PROFILI = Path(__file__).resolve().parents[2] / "config" / "stampante_p2s.json"
SECONDI = 180
MEMORIA_BYTE = 4 * 1024 ** 3
SUPPORTI = ("se_servono", "si", "no")

_TEMPO = re.compile(r"^; estimated printing time \(normal mode\) = (.+)$", re.M)
_GRAMMI = re.compile(r"^; total filament used \[g\] = ([0-9.]+)", re.M)
_MM = re.compile(r"^; filament used \[mm\] = ([0-9.]+)", re.M)
_COSTO = re.compile(r"^; total filament cost = ([0-9.]+)", re.M)
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


def profili(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    percorso = Path(str(_config_slicer(config).get("profili") or PROFILI))
    dati = json.loads(percorso.read_text(encoding="utf-8"))
    if dati.get("schema") != "devin_slicer_profiles_v1":
        raise ValueError(f"profili dello slicer: schema inatteso in {percorso}")
    return dati


def scelta(config: Optional[Dict[str, Any]] = None, materiale: str = "", qualita: str = "") -> Dict[str, Any]:
    """Stampante, materiale e qualita'. Un nome sconosciuto e' un errore con l'elenco, non un ripiego."""
    dati = profili(config)
    materiale = materiale or dati["predefiniti"]["materiale"]
    qualita = qualita or dati["predefiniti"]["qualita"]
    if materiale not in dati["materiali"]:
        raise ValueError(f"materiale sconosciuto {materiale!r}; disponibili: {', '.join(dati['materiali'])}")
    if qualita not in dati["qualita"]:
        raise ValueError(f"qualita' sconosciuta {qualita!r}; disponibili: {', '.join(dati['qualita'])}")
    return {"stampante": dati["stampante"], "materiale": materiale, "m": dati["materiali"][materiale],
            "qualita": qualita, "q": dati["qualita"][qualita]}


def nomi(config: Optional[Dict[str, Any]] = None) -> tuple[list[str], list[str]]:
    dati = profili(config)
    return list(dati["materiali"]), list(dati["qualita"])


def _limiti() -> None:  # pragma: no cover - gira nel processo figlio
    try:
        os.setsid()          # gia' sessione propria se lanciato da run_limitato
    except OSError:
        pass
    try:
        resource.setrlimit(resource.RLIMIT_AS, (MEMORIA_BYTE, MEMORIA_BYTE))
    except (ValueError, OSError):
        pass


def argomenti(stl: Path, gcode: Path, s: Dict[str, Any], supporti: bool) -> list[str]:
    st, m, q = s["stampante"], s["m"], s["q"]
    larghezza, profondita = (float(v) for v in st["piatto_mm"])
    v = {**st["velocita_mm_s"], **(q.get("velocita_mm_s") or {})}
    acc = st["accelerazione_mm_s2"]
    motivo = "rectilinear" if int(q["riempimento_pct"]) >= 100 else q["motivo"]
    argv = [
        "--export-gcode", "--output", str(gcode),
        "--nozzle-diameter", str(st["ugello_mm"]),
        "--bed-shape", f"0x0,{larghezza:g}x0,{larghezza:g}x{profondita:g},0x{profondita:g}",
        "--max-print-height", str(st["altezza_mm"]),
        "--center", f"{larghezza / 2:g},{profondita / 2:g}", "--gcode-flavor", "marlin2",
        "--machine-limits-usage", "time_estimate_only",
        "--machine-max-acceleration-extruding", str(st["accelerazione_max_mm_s2"]),
        "--machine-max-acceleration-x", str(st["accelerazione_max_mm_s2"]),
        "--machine-max-acceleration-y", str(st["accelerazione_max_mm_s2"]),
        "--machine-max-feedrate-x", str(st["velocita_max_mm_s"]),
        "--machine-max-feedrate-y", str(st["velocita_max_mm_s"]),
        "--default-acceleration", str(acc["default"]), "--perimeter-acceleration", str(acc["pareti"]),
        "--infill-acceleration", str(acc["riempimento"]),
        "--external-perimeter-speed", str(v["parete_esterna"]), "--perimeter-speed", str(v["pareti"]),
        "--infill-speed", str(v["riempimento"]), "--solid-infill-speed", str(v["pieno"]),
        "--top-solid-infill-speed", str(v["superficie"]), "--travel-speed", str(v["spostamento"]),
        "--first-layer-speed", str(v["primo_strato"]),
        "--temperature", str(m["ugello_c"]), "--first-layer-temperature", str(m["ugello_primo_strato_c"]),
        "--bed-temperature", str(m["piatto_c"]), "--first-layer-bed-temperature", str(m["piatto_c"]),
        "--filament-diameter", "1.75", "--filament-density", str(m["densita_g_cm3"]),
        "--extrusion-multiplier", str(m["flusso"]), "--filament-max-volumetric-speed", str(m["vol_max_mm3_s"]),
        "--min-fan-speed", str(m["ventola_min"]), "--max-fan-speed", str(m["ventola_max"]),
        "--retract-length", str(m["retrazione_mm"]), "--filament-cost", str(m["costo_eur_kg"]),
        "--layer-height", str(q["strato_mm"]), "--first-layer-height", str(q["primo_strato_mm"]),
        "--perimeters", str(int(q["pareti"])), "--top-solid-layers", str(int(q["strati_sopra"])),
        "--bottom-solid-layers", str(int(q["strati_sotto"])),
        "--fill-density", f"{q['riempimento_pct']}%", "--fill-pattern", motivo,
        "--brim-width", str(q["brim_mm"]),
    ]
    if supporti:
        argv += ["--support-material", "--support-material-auto",
                 "--support-material-threshold", str(q["soglia_supporti_gradi"])]
    return argv + [str(stl)]


def affetta(stl: Path, sbalzi: bool = False, config: Optional[Dict[str, Any]] = None,
            destinazione: Optional[Path] = None, secondi: int = SECONDI,
            materiale: str = "", qualita: str = "") -> Dict[str, Any]:
    """Affetta ``stl``. Restituisce le misure o ``{"errore": ...}``, mai un'eccezione."""
    pronto, motivo = disponibile(config)
    if not pronto:
        return {"errore": motivo}
    try:
        s = scelta(config, materiale, qualita)
    except (OSError, ValueError, KeyError) as exc:
        return {"errore": str(exc)}
    stl = Path(stl)
    if stl.suffix.lower() != ".stl" or not stl.is_file():
        return {"errore": f"non e' un file STL: {stl}"}
    regola = s["q"].get("supporti", "se_servono")
    supporti = regola == "si" or (regola == "se_servono" and sbalzi)
    destinazione = Path(destinazione or stl.parent)
    gcode = destinazione / (stl.stem + ".gcode")
    with tempfile.TemporaryDirectory(prefix="devin-slicer-") as casa:
        env = {"PATH": "/usr/bin:/bin", "HOME": casa, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}
        try:
            from devin.engine.processi import run_limitato
            proc = run_limitato([str(binario(config)), *argomenti(stl, gcode, s, supporti)],
                                  capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  timeout=secondi, stdin=subprocess.DEVNULL, env=env, cwd=casa,
                                  preexec_fn=_limiti)
        except subprocess.TimeoutExpired:
            return {"errore": f"lo slicer non ha finito in {secondi} s"}
        except OSError as exc:
            return {"errore": f"slicer non avviabile: {exc}"}
    if proc.returncode != 0 or not gcode.is_file():
        dettaglio = (proc.stderr or proc.stdout or "").strip()[-600:]
        return {"errore": f"slicer fallito (exit {proc.returncode}): {dettaglio}"}
    testo = gcode.read_text(encoding="utf-8", errors="replace")
    tempo, grammi, mm, costo = _TEMPO.search(testo), _GRAMMI.search(testo), _MM.search(testo), _COSTO.search(testo)
    if tempo is None:
        return {"errore": "lo slicer non ha scritto la stima del tempo nel G-code"}
    return {
        "tempo": tempo.group(1).strip(),
        "grammi": float(grammi.group(1)) if grammi else None,
        "metri": round(float(mm.group(1)) / 1000, 2) if mm else None,
        "costo_eur": float(costo.group(1)) if costo else None,
        "strati": len(_STRATI.findall(testo)),
        "supporti": supporti,
        "stampante": s["stampante"]["nome"],
        "materiale": s["materiale"],
        "m": s["m"],
        "qualita": s["qualita"],
        "q": s["q"],
        "gcode": str(gcode),
    }


def riga(esito: Dict[str, Any]) -> str:
    """Una riga di testo per il modello."""
    if esito.get("errore"):
        return f"slicer: {esito['errore']}"
    m, q = esito["m"], esito["q"]
    return (f"slicer (PrusaSlicer, {esito['stampante']}, {esito['materiale']} {m['ugello_c']}°C piatto "
            f"{m['piatto_c']}°C, qualita' '{esito['qualita']}': strato {q['strato_mm']} mm, {q['pareti']} pareti, "
            f"{q['riempimento_pct']}% {q['motivo']}, supporti {'si' if esito['supporti'] else 'no'}): "
            f"tempo {esito['tempo']} (stima), filamento {esito['grammi']} g ({esito['metri']} m), "
            f"costo {esito['costo_eur']} EUR, {esito['strati']} strati; G-code {esito['gcode']}")
