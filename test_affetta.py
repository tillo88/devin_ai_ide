"""Slicer: profili della P2S di Alessandro, argomenti, lettura del G-code, errori detti al modello."""
import json
import os
from pathlib import Path

import pytest

from devin.core import affetta

CUBO = """solid c
""" + "".join(
    f"facet normal 0 0 0\n outer loop\n  vertex {a}\n  vertex {b}\n  vertex {c}\n endloop\nendfacet\n"
    for a, b, c in [
        ("0 0 0", "0 20 20", "0 20 0"), ("0 0 0", "0 0 20", "0 20 20"),
        ("20 0 0", "20 20 0", "20 20 20"), ("20 0 0", "20 20 20", "20 0 20"),
        ("0 0 0", "20 0 20", "0 0 20"), ("0 0 0", "20 0 0", "20 0 20"),
        ("0 20 0", "0 20 20", "20 20 20"), ("0 20 0", "20 20 20", "20 20 0"),
        ("0 0 0", "20 20 0", "20 0 0"), ("0 0 0", "0 20 0", "20 20 0"),
        ("0 0 20", "20 0 20", "20 20 20"), ("0 0 20", "20 20 20", "0 20 20"),
    ]) + "endsolid c\n"


def _finto(tmp_path, corpo):
    script = tmp_path / "slicer"
    script.write_text("#!/bin/sh\n" + corpo)
    script.chmod(0o755)
    return {"tools": {"slicer": {"bin": str(script)}}}


def test_profili_della_p2s_completi():
    dati = affetta.profili()
    assert dati["stampante"]["nome"] == "Bambu Lab P2S" and dati["stampante"]["piatto_mm"] == [256, 256]
    chiavi_m = {"tipo", "ugello_c", "ugello_primo_strato_c", "piatto_c", "densita_g_cm3", "flusso", "vol_max_mm3_s",
                "ventola_min", "ventola_max", "retrazione_mm", "costo_eur_kg", "preset"}
    chiavi_q = {"strato_mm", "primo_strato_mm", "pareti", "strati_sopra", "strati_sotto", "riempimento_pct", "motivo",
                "supporti", "soglia_supporti_gradi", "brim_mm", "velocita_mm_s", "preset"}
    assert all(set(m) == chiavi_m for m in dati["materiali"].values())
    assert all(set(q) == chiavi_q and q["supporti"] in affetta.SUPPORTI for q in dati["qualita"].values())
    assert {"PLA Matte HF", "PETG HF", "ABS Sunlu", "TPU for AMS"} <= set(dati["materiali"])
    assert {"standard 0.20", "hueforge 0.08", "TPU 0.20"} <= set(dati["qualita"])
    assert dati["predefiniti"]["materiale"] in dati["materiali"] and dati["predefiniti"]["qualita"] in dati["qualita"]


def test_argomenti_da_materiale_e_qualita(tmp_path):
    s = affetta.scelta(None, "ABS Sunlu", "robusto 0.20")
    argv = affetta.argomenti(tmp_path / "a.stl", tmp_path / "a.gcode", s, supporti=True)
    valore = lambda flag: argv[argv.index(flag) + 1]   # noqa: E731
    assert valore("--temperature") == "255" and valore("--bed-temperature") == "100"
    assert valore("--fill-density") == "50%" and valore("--fill-pattern") == "adaptivecubic"
    assert valore("--bed-shape") == "0x0,256x0,256x256,0x256" and valore("--center") == "128,128"
    assert valore("--external-perimeter-speed") == "80" and valore("--travel-speed") == "500"
    assert "--support-material-auto" in argv and argv[-1] == str(tmp_path / "a.stl")
    hf = affetta.argomenti(tmp_path / "a.stl", tmp_path / "a.gcode", affetta.scelta(None, "", "hueforge 0.08"), False)
    assert hf[hf.index("--fill-pattern") + 1] == "rectilinear" and hf[hf.index("--layer-height") + 1] == "0.08"
    assert "--support-material" not in hf


def test_nomi_sconosciuti_dicono_cosa_ce():
    with pytest.raises(ValueError, match="disponibili: .*PETG HF"):
        affetta.scelta(None, "Nylon")
    with pytest.raises(ValueError, match="qualita' sconosciuta"):
        affetta.scelta(None, "", "ultra")


def test_regola_dei_supporti(tmp_path):
    stl = tmp_path / "a.stl"
    stl.write_text(CUBO)
    args = tmp_path / "args"
    cfg = _finto(tmp_path, f'echo "$@" > {args}\nexit 1\n')
    affetta.affetta(stl, sbalzi=False, config=cfg, qualita="TPU 0.20")       # TPU: sempre
    assert "--support-material-auto" in args.read_text()
    affetta.affetta(stl, sbalzi=True, config=cfg, qualita="hueforge 0.08")   # hueforge: mai
    assert "--support-material" not in args.read_text()
    affetta.affetta(stl, sbalzi=True, config=cfg)                            # standard: se servono
    assert "--support-material-auto" in args.read_text()


def test_errori_detti_e_mai_eccezioni(tmp_path):
    stl = tmp_path / "a.stl"
    stl.write_text(CUBO)
    assert "non installato" in affetta.affetta(stl, config={"tools": {"slicer": {"bin": str(tmp_path / "x")}}})["errore"]
    rotto = _finto(tmp_path, "echo 'Objects could not fit on the bed' >&2\nexit 1\n")
    esito = affetta.affetta(stl, config=rotto)
    assert "exit 1" in esito["errore"] and "fit on the bed" in esito["errore"]
    assert affetta.riga(esito).startswith("slicer: slicer fallito")
    assert "materiale sconosciuto" in affetta.affetta(stl, config=rotto, materiale="Nylon")["errore"]
    assert "non e' un file STL" in affetta.affetta(tmp_path / "a.step", config=rotto)["errore"]


@pytest.mark.skipif(not os.access(affetta.SLICER, os.X_OK), reason="PrusaSlicer non installato qui")
def test_slicer_vero_su_un_cubo_col_profilo_p2s(tmp_path):
    stl = tmp_path / "cubo.stl"
    stl.write_text(CUBO)
    for materiale, qualita in (("PLA Matte HF", "standard 0.20"), ("PETG HF", "PETG 0.16"),
                               ("TPU for AMS", "TPU 0.20"), ("PLA Basic HF", "hueforge 0.08")):
        esito = affetta.affetta(stl, config=None, materiale=materiale, qualita=qualita)
        assert not esito.get("errore"), (materiale, qualita, esito)
        assert esito["grammi"] and esito["costo_eur"] is not None and esito["strati"] >= 99
    riga = affetta.riga(esito)
    assert "Bambu Lab P2S" in riga and "PLA Basic HF" in riga and "hueforge 0.08" in riga and "EUR" in riga
