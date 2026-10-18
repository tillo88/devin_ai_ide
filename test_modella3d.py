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
    assert "PROMEMORIA build123d 0.13" in testo   # gli esempi verificati arrivano col primo errore


def test_promemoria_e_descrizione_con_la_sintassi_giusta():
    for frammento in ("Box(70, 80, 4, align=(Align.MIN, Align.MIN, Align.MIN))", "Pos(0, 10, 4) * Rot(X=-30)",
                      "Plane.YZ * Polygon(", "extrude(profilo, amount=70)", "risultato = pezzo"):
        assert frammento in modella3d.PROMEMORIA
    assert "Pos(x, y, z) * Rot(X=gradi) * pezzo" in modella3d.DESCRIZIONE
    assert "centered=" in modella3d.PROMEMORIA and "NON esistono" in modella3d.PROMEMORIA


def test_senza_ambiente_non_disponibile(tmp_path):
    config = {"tools": {"solver_python": str(tmp_path / "x" / "python")}}
    assert modella3d.modella("risultato = 1", config=config).startswith("ERRORE: modellazione 3D non disponibile")


def test_scatola_appoggia_tutta_e_non_ha_sbalzi(config, tmp_path):
    testo = modella3d.modella("risultato = Box(20, 30, 10, align=(Align.MIN,)*3)", "s", tmp_path / "o",
                              config=config)
    assert "com'e' appoggia 600.0 mm², sbalzi 0.0 mm² (0.0% della superficie)" in testo, testo
    assert "servono supporti" not in testo and "orientamento migliore" not in testo
    assert "materiale se pieno al 100%" in testo and "7.4 g" in testo   # 6 cm³ x 1,24


def test_sbalzo_a_bandiera_e_orientamento_migliore(config, tmp_path):
    codice = ("colonna = Box(10, 10, 20, align=(Align.MIN,)*3)\n"
              "braccio = Pos(0, 0, 20) * Box(30, 10, 5, align=(Align.MIN,)*3)\n"
              "risultato = colonna + braccio\n")
    testo = modella3d.modella(codice, "bandiera", tmp_path / "o", config=config)
    assert "com'e' appoggia 100.0 mm², sbalzi 200.0 mm²" in testo, testo
    assert "servono supporti o un altro orientamento" in testo
    assert "orientamento migliore fra i 6 assi: sul fianco Y" in testo and "(sbalzi 0.0 mm²" in testo


def test_stampabilita_senza_appoggio_lo_dice():
    misure = {"area_mm2": 100, "volume_mm3": 0,
              "orientamenti": {"Z-": {"appoggio_mm2": 0.0, "sbalzi_mm2": 3.0},
                               "Z+": {"appoggio_mm2": 50.0, "sbalzi_mm2": 0.0}}}
    righe = modella3d.stampabilita(misure)
    assert any("nessuna faccia piana appoggia" in r for r in righe)
    assert any("orientamento migliore fra i 6 assi: capovolto" in r for r in righe)
    assert not any("materiale" in r for r in righe)
