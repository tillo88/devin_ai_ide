"""Modellazione 3D: codice build123d -> STL/STEP + misure vere."""
import os
import sys

import pytest

from devin.core import modella3d


@pytest.fixture
def config(tmp_path, monkeypatch):
    pytest.importorskip("build123d")
    pytest.importorskip("trimesh")
    python = tmp_path / "venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    os.symlink(sys.executable, python)
    monkeypatch.setattr(modella3d, "disponibile", lambda config=None: (True, "pronto"))
    return {"tools": {"solver_python": str(python)}}


def test_scatola_con_foro_misure_vere(config, tmp_path):
    codice = ("with BuildPart() as p:\n"
              "    Box(20, 30, 10, align=(Align.MIN, Align.MIN, Align.MIN))\n"
              "    with Locations((10, 15, 10)):\n"
              "        Hole(radius=3)\n"
              "risultato = p.part\n")
    testo = modella3d.modella(codice, "scatola foro", tmp_path / "out", config=config)
    assert testo.startswith("pezzo 'scatola_foro': ingombro 20.0 x 30.0 x 10.0 mm"), testo
    assert "mesh: chiusa (stampabile), solido valido" in testo and "1 corpo" in testo
    volume = float(testo.split("volume ")[1].split(" ")[0])
    assert abs(volume - (6000 - 3.14159 * 9 * 10)) < 15
    assert (tmp_path / "out" / "scatola_foro.stl").stat().st_size > 1000
    assert (tmp_path / "out" / "scatola_foro.step").exists()


def test_senza_risultato_lo_dice(config, tmp_path):
    testo = modella3d.modella("x = Box(1, 1, 1)", "p", tmp_path / "o", config=config)
    assert testo.startswith("ERRORE nel codice del pezzo") and "risultato" in testo


def test_errore_di_codice_arriva_al_modello(config, tmp_path):
    testo = modella3d.modella("risultato = Scatola(1)", "p", tmp_path / "o", config=config)
    assert "NameError" in testo


def test_senza_ambiente_non_disponibile(tmp_path):
    config = {"tools": {"solver_python": str(tmp_path / "x" / "python")}}
    assert modella3d.modella("risultato = 1", config=config).startswith("ERRORE: modellazione 3D non disponibile")
