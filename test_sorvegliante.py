"""Il sorvegliante delle ripetizioni (19/10): ferma solo chi gira in tondo.

Fissa le tre regole (chiamata senza niente di nuovo, ricerca quasi uguale, stesso
errore di fila), l'escalation avviso -> stop, e soprattutto il contrario: un lavoro
lungo che porta risultati nuovi non viene mai fermato.
"""
from __future__ import annotations

from devin.core.sorvegliante import (Sorvegliante, firma_chiamata, firma_errore, firma_risultato,
                                     messaggio_avviso, messaggio_stop, simili)


def test_la_firma_ignora_spazi_e_ordine_e_il_risultato_le_cifre() -> None:
    assert firma_chiamata("f", '{"a": "x  y", "b": 1}') == firma_chiamata("f", {"b": 1, "a": "x y"})
    assert firma_chiamata("f", {"a": 1}) != firma_chiamata("g", {"a": 1})
    assert firma_risultato("file: 20261019-092149/pezzo.stl 1284 B") == \
        firma_risultato("file: 20261019-095433/pezzo.stl 1284 B")
    assert firma_risultato("pezzo.stl 1284 B") != firma_risultato("pezzo.stl 1484 B"), "un file diverso e' una novita'"
    assert firma_risultato("pagina caricata alle 10:03:16 in 0.52s") == firma_risultato("pagina caricata alle 10:04:32 in 1.3s")
    # misure e conteggi contano: sono il progresso
    assert firma_risultato("ingombro 70 x 80") != firma_risultato("ingombro 70 x 100")
    assert firma_risultato("3 failed") != firma_risultato("2 failed")
    assert firma_risultato("ok") != firma_risultato("ERRORE: x")


def test_firma_errore() -> None:
    assert firma_errore("tutto bene") is None
    a = firma_errore("ERRORE nel codice del pezzo (exit 1):\nNameError: name 'radians' is not defined\n...")
    b = firma_errore("ERRORE nel codice del pezzo (exit 2):\nNameError: name 'radians' is not defined")
    c = firma_errore("ERRORE nel codice del pezzo (exit 1):\nAttributeError: 'Face' has no attribute 'extrude'")
    assert a == b and a != c
    assert firma_errore("Traceback (most recent call last):\n  ...\nValueError: x 3") == "ValueError: x #"


def test_ricerche_quasi_uguali() -> None:
    assert simili("build123d Polygon Plane.YZ winding order", "build123d polygon plane YZ winding order")
    assert not simili("build123d Polygon winding", "build123d extrude amount")
    s = Sorvegliante()
    assert s.ricerca_gia_fatta("build123d Polygon winding") is None
    assert s.ricerca_gia_fatta("Build123d  polygon WINDING") == "build123d Polygon winding"


def test_un_lavoro_lungo_che_porta_novita_non_si_ferma_mai() -> None:
    s = Sorvegliante()
    for giro in range(40):
        # lo stesso pytest dopo ogni modifica: risultati diversi, nessuna ripetizione
        s.registra("scrivi_file", {"percorso": "calc.py", "contenuto": f"versione {giro} " + "x" * giro}, "ok")
        s.registra("esegui", {"comando": "pytest -q"}, f"{40 - giro} failed" if giro % 2 else f"passi {'.' * giro}")
        assert s.fine_giro().stato == "ok"
    assert s.interventi == []


def test_chiamate_senza_niente_di_nuovo_prima_avviso_poi_stop() -> None:
    s = Sorvegliante()
    assert s.registra("leggi_file", {"percorso": "a.py"}, "contenuto") is False
    assert s.fine_giro().stato == "ok"
    assert s.registra("leggi_file", {"percorso": "a.py"}, "contenuto") is True
    assert s.fine_giro().stato == "ok", "una sola ripetizione: puo' capitare"
    s.registra("leggi_file", {"percorso": "a.py"}, "contenuto")
    v = s.fine_giro()
    assert v.stato == "avviso" and "leggi_file" in v.motivo
    s.registra("leggi_file", {"percorso": "a.py"}, "contenuto")
    assert s.fine_giro().stato == "ok", "dopo l'avviso si riparte da zero"
    s.registra("leggi_file", {"percorso": "a.py"}, "contenuto")
    assert s.fine_giro().stato == "stop"


def test_lo_stesso_errore_di_fila() -> None:
    s = Sorvegliante()
    errore = "ERRORE nel codice del pezzo (exit 1):\nValueError: self-intersecting polygon {}"
    for i in range(2):
        s.registra("modella_3d", {"codice": f"tentativo {i}"}, errore.format(i))
        assert s.fine_giro().stato == "ok"
    s.registra("modella_3d", {"codice": "tentativo 2"}, errore.format(2))
    v = s.fine_giro()
    assert v.stato == "avviso" and "stesso errore" in v.motivo and "self-intersecting" in v.motivo
    # un errore diverso azzera il conto
    s.registra("modella_3d", {"codice": "tentativo 3"}, "ERRORE nel codice del pezzo (exit 1):\nNameError: x")
    assert s.fine_giro().stato == "ok"


def test_i_messaggi_dicono_cosa_e_perche() -> None:
    avviso, stop = messaggio_avviso("ripete la ricerca 'x'"), messaggio_stop("ripete la ricerca 'x'")
    assert avviso["role"] == stop["role"] == "user"
    assert "ripete la ricerca 'x'" in avviso["content"] and "Cambia approccio" in avviso["content"]
    assert "non hai piu' strumenti" in stop["content"] and "continua" in stop["content"]
