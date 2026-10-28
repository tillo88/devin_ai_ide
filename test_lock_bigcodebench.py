"""Lock dell'ambiente BigCodeBench (28/10): formato che l'installazione sul rig accetta, versioni chiave."""
from pathlib import Path
import re

RIGA = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([A-Za-z0-9][A-Za-z0-9._+!-]*) --hash=sha256:[0-9a-f]{64}$")
LOCK = Path("config/venv-bigcodebench.lock")


def pin():
    out = {}
    for riga in LOCK.read_text(encoding="utf-8").splitlines():
        riga = riga.strip()
        if not riga or riga.startswith("#"):
            continue
        trovato = RIGA.fullmatch(riga)
        assert trovato, f"riga non 'nome==versione --hash=sha256:...': {riga}"
        nome = re.sub(r"[-_.]+", "-", trovato.group(1)).lower()
        assert nome not in out, f"{nome} due volte"
        out[nome] = trovato.group(2)
    return out


def test_formato_e_versioni_chiave():
    p = pin()
    assert len(p) >= 150
    # le scelte documentate in testa al lock (scelta B, Python 3.12)
    assert p["numpy"] == "1.26.0" and p["pandas"] == "2.1.1" and p["tensorflow"] == "2.16.1"
    assert p["scikit-learn"] == "1.3.1" and p["matplotlib"] == "3.7.3"
    assert "wikipedia" not in p and "wordninja" not in p, "solo sorgenti: fuori (--only-binary)"
    assert "pyarrow" not in p, "il dataset arriva in jsonl.gz: pyarrow non serve"
    # i test ufficiali ne hanno bisogno (faker, pytz, pyfakefs) e il gate gira con pytest del venv
    assert {"pytest", "faker", "pytz", "pyfakefs"} <= set(p)


def test_l_ambiente_del_lock_e_quello_che_devin_usa():
    from devin.engine import ambienti
    assert ambienti.AMBIENTI["bigcodebench"] == ".local/share/devin/venv-bigcodebench"
    assert "venv-bigcodebench" in LOCK.read_text(encoding="utf-8").split("\n", 3)[0]
