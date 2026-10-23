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
    # lo slicer di sistema, se c'e' su questa macchina, non entra nei test di modellazione
    return {"tools": {"solver_python": str(python), "slicer": {"bin": str(tmp_path / "nessuno-slicer")}}}


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



def test_con_lo_slicer_arrivano_tempo_e_filamento(config, tmp_path):
    finto = tmp_path / "finto-slicer"
    finto.write_text("#!/bin/sh\nfor a in \"$@\"; do case $prev in --output) out=$a;; esac; prev=$a; done\n"
                     "printf ';LAYER_CHANGE\\n; total filament used [g] = 7.10\\n; filament used [mm] = 2380.5\\n"
                     "; total filament cost = 0.18\\n; estimated printing time (normal mode) = 41m 5s\\n' > \"$out\"\n")
    finto.chmod(0o755)
    config["tools"]["slicer"] = {"bin": str(finto)}
    testo = modella3d.modella("risultato = Box(20, 30, 10, align=(Align.MIN,)*3)", "s", tmp_path / "o",
                              config=config)
    assert "slicer (PrusaSlicer, Bambu Lab P2S, PLA Matte HF 220°C piatto 55°C, qualita' 'standard 0.20'" in testo, testo
    assert "supporti no): tempo 41m 5s (stima), filamento 7.1 g (2.38 m), costo 0.18 EUR, 1 strati" in testo
    assert str(tmp_path / "o" / "s.gcode") in testo



def test_lo_schema_offre_materiali_e_qualita_della_p2s():
    proprieta = modella3d.STRUMENTO["function"]["parameters"]["properties"]
    assert "PETG HF" in proprieta["materiale"]["enum"] and "hueforge 0.08" in proprieta["qualita"]["enum"]
    assert "Bambu Lab P2S" in modella3d.DESCRIZIONE
    assert "bounding_box()" in modella3d.PROMEMORIA and "is_watertight()" in modella3d.PROMEMORIA


def test_l_anteprima_si_vede_e_mostra_il_foro(config, tmp_path):
    """Roadmap 2: quattro viste in un PNG, che DEVIN guarda quando la visione e' accesa."""
    pytest.importorskip("PIL")
    from PIL import Image
    codice = ("with BuildPart() as p:\n"
              "    Box(20, 30, 10, align=(Align.MIN, Align.MIN, Align.MIN))\n"
              "    with Locations((10, 15, 10)):\n"
              "        Hole(radius=3)\n"
              "risultato = p.part\n")
    testo = modella3d.modella(codice, "foro", tmp_path / "out", config=config)
    png = tmp_path / "out" / "foro-anteprima.png"
    assert f"anteprima: {png} (vista isometrica, fronte, alto, destra)" in testo
    assert "foro-anteprima.png" not in testo.split("file: ")[1].split("\n")[0]
    immagine = Image.open(png).convert("RGB")
    assert immagine.size == (760, 784)
    # vista dall'alto (riquadro in basso a sinistra): al centro del pezzo il foro passante e'
    # bianco (sfondo), sul pieno accanto il colore del pezzo
    lato = 380
    centro = immagine.getpixel((lato // 2, lato + lato // 2 + 10))
    assert centro == (255, 255, 255), centro
    pieno = immagine.getpixel((lato // 2, lato + lato // 2 + 10 + 60))
    assert pieno != (255, 255, 255) and pieno[2] > pieno[0]


def test_math_e_gia_importato_e_extrude_e_una_funzione(config, tmp_path):
    """19/10: radians(60) dava NameError e profilo.extrude(...) AttributeError."""
    codice = ("altezza = 100 * sin(radians(60))\n"
              "profilo = Plane.YZ * Polygon((0, 0), (80, 0), (80, 4), (80 - 100 * cos(radians(60)), altezza), "
              "(76 - 100 * cos(radians(60)), altezza), (76, 4), (0, 4), align=None)\n"
              "risultato = extrude(profilo, amount=70)\n")
    testo = modella3d.modella(codice, "schienale", tmp_path / "out", config=config)
    assert testo.startswith("pezzo 'schienale': ingombro 70.0 x 80.0 x 86.603 mm"), testo
    assert "mesh: chiusa (stampabile), solido valido" in testo
    assert "radians, degrees, sin, cos" in modella3d.DESCRIZIONE
    assert "extrude e' una FUNZIONE" in modella3d.PROMEMORIA


# --- 23/10: OpenSCAD come secondo linguaggio --------------------------------

def _scad(config):
    import shutil
    vero = shutil.which("openscad")
    if not vero:
        pytest.skip("OpenSCAD non installato qui")
    config["tools"]["openscad"] = {"bin": vero}
    return config


def test_openscad_scatola_con_foro_misure_vere(config, tmp_path):
    _scad(config)
    codice = ("difference() {\n  cube([20, 30, 10]);\n"
              "  translate([10, 15, -1]) cylinder(h=12, r=3, $fn=96);\n}\n")
    testo = modella3d.modella(codice, "scatola scad", tmp_path / "out", config=config, linguaggio="openscad")
    assert testo.startswith("pezzo 'scatola_scad': ingombro 20.0 x 30.0 x 10.0 mm"), testo
    assert "(CAD" not in testo and "(OpenSCAD)" in testo
    assert "mesh: chiusa (stampabile), solido valido" in testo and "1 corpo" in testo
    volume = float(testo.split("volume ")[1].split(" ")[0])
    assert abs(volume - (6000 - 3.14159 * 9 * 10)) < 15
    assert (tmp_path / "out" / "scatola_scad.stl").stat().st_size > 1000
    assert not (tmp_path / "out" / "scatola_scad.step").exists(), "OpenSCAD lavora a mesh: niente STEP"
    assert "stampa (FDM" in testo


def test_openscad_errore_arriva_col_promemoria(config, tmp_path):
    _scad(config)
    testo = modella3d.modella("cube([10, 10, 10]) translate", "p", tmp_path / "o", config=config,
                              linguaggio="openscad")
    assert testo.startswith("ERRORE nel codice del pezzo") and "ERRORE OpenSCAD" in testo, testo
    assert "PROMEMORIA OpenSCAD" in testo and "PROMEMORIA build123d" not in testo


def test_openscad_assente_o_linguaggio_sconosciuto(config, tmp_path):
    config["tools"]["openscad"] = {"bin": str(tmp_path / "nessuno")}
    testo = modella3d.modella("cube(1);", "p", tmp_path / "o", config=config, linguaggio="openscad")
    assert testo.startswith("ERRORE: OpenSCAD non installato sul rig") and "build123d" in testo
    assert modella3d.modella("x", "p", tmp_path / "o", config=config, linguaggio="fortran").startswith(
        "ERRORE: linguaggio 'fortran' sconosciuto")


def test_lo_schema_offre_i_due_linguaggi_e_build123d_resta_com_era():
    proprieta = modella3d.STRUMENTO["function"]["parameters"]["properties"]
    assert proprieta["linguaggio"]["enum"] == ["build123d", "openscad"]
    assert "linguaggio='openscad'" in modella3d.DESCRIZIONE
    assert modella3d.ESPORTA_B123D + modella3d._MISURE == modella3d.POSTLUDIO.replace("#DEVIN_MISURE_DA_QUI\n", "")
    assert "export_stl(_pezzo, _stl)" in modella3d.ESPORTA_B123D and "export_stl" not in modella3d.ESPORTA_SCAD
