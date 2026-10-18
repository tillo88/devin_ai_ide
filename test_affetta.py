"""Slicer: argomenti del profilo, lettura del G-code, errori detti al modello."""
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


def test_argomenti_del_profilo_e_supporti(tmp_path):
    p = affetta.profilo({"tools": {"slicer": {"profilo": {"riempimento_pct": 30, "piatto_mm": [235, 235]}}}})
    argv = affetta.argomenti(tmp_path / "a.stl", tmp_path / "a.gcode", p, supporti=True)
    assert argv[argv.index("--fill-density") + 1] == "30%"
    assert argv[argv.index("--bed-shape") + 1] == "0x0,235x0,235x235,0x235"
    assert argv[argv.index("--center") + 1] == "117.5,117.5"
    assert "--support-material-auto" in argv and argv[-1] == str(tmp_path / "a.stl")
    assert "--support-material" not in affetta.argomenti(tmp_path / "a.stl", tmp_path / "a.gcode", p, False)
    with pytest.raises(ValueError, match="sconosciuta"):
        affetta.profilo({"tools": {"slicer": {"profilo": {"velocita": 300}}}})


def test_errori_detti_e_mai_eccezioni(tmp_path):
    stl = tmp_path / "a.stl"
    stl.write_text(CUBO)
    assert "non installato" in affetta.affetta(stl, config={"tools": {"slicer": {"bin": str(tmp_path / "x")}}})["errore"]
    rotto = _finto(tmp_path, "echo 'Objects could not fit on the bed' >&2\nexit 1\n")
    esito = affetta.affetta(stl, config=rotto)
    assert "exit 1" in esito["errore"] and "fit on the bed" in esito["errore"]
    assert affetta.riga(esito).startswith("slicer: slicer fallito")
    assert "non e' un file STL" in affetta.affetta(tmp_path / "a.step", config=rotto)["errore"]


@pytest.mark.skipif(not os.access(affetta.SLICER, os.X_OK), reason="PrusaSlicer non installato qui")
def test_slicer_vero_su_un_cubo(tmp_path):
    stl = tmp_path / "cubo.stl"
    stl.write_text(CUBO)
    esito = affetta.affetta(stl, supporti=False)
    assert not esito.get("errore"), esito
    assert esito["grammi"] and 2 < esito["grammi"] < 8 and esito["strati"] >= 99
    assert Path(esito["gcode"]).stat().st_size > 1000
