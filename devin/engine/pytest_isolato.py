"""pytest confinato al progetto: rootdir, configurazione e conftest del progetto, mai degli antenati.

23/10 (palestra #1856, 0/7 "gold test non eseguiti" con pytest exit 0): i progetti del
training vivono in ``<repo DEVIN>/workspace/_training_runs/...``. Senza un file di
configurazione proprio, pytest risaliva fino al ``pytest.ini`` del repo DEVIN e lo
prendeva come rootdir: i nodeid (e i classname del report junit) diventavano
``workspace._training_runs....test_gold_x`` invece di ``test_gold_x``, e la verifica
dei gold non li riconosceva. Di passaggio caricava anche il ``conftest.py`` del repo e
ne applicava le opzioni dentro la sandbox del modello.

Qui: ``--rootdir`` e ``--confcutdir`` sul progetto, e ``-c`` sul file di
configurazione del progetto se ne ha uno, altrimenti su un ini vuoto temporaneo
fuori dal progetto (niente file scritti nella sandbox).
"""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import tempfile
from typing import Iterator, List

# (file, sezione che lo rende un file di configurazione pytest; None = basta che esista)
_CONFIG = (("pytest.ini", None), (".pytest.ini", None), ("pyproject.toml", "[tool.pytest.ini_options]"),
           ("tox.ini", "[pytest]"), ("setup.cfg", "[tool:pytest]"))


def config_del_progetto(root: Path | str) -> Path | None:
    """Il file di configurazione pytest del progetto stesso, nell'ordine in cui lo cerca pytest."""
    root = Path(root)
    for nome, sezione in _CONFIG:
        path = root / nome
        if not path.is_file() or path.is_symlink():
            continue
        if sezione is None:
            return path
        try:
            if sezione in path.read_text(encoding="utf-8", errors="replace"):
                return path
        except OSError:
            continue
    return None


@contextmanager
def argomenti_isolati(root: Path | str) -> Iterator[List[str]]:
    """Argomenti da aggiungere a ``python -m pytest`` lanciato con cwd=root."""
    root = Path(root).resolve()
    proprio = config_del_progetto(root)
    vuoto = None
    if proprio is None:
        fd, nome = tempfile.mkstemp(prefix="devin_pytest_", suffix=".ini")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write("[pytest]\n")
        vuoto = Path(nome)
    try:
        yield ["-c", str(proprio or vuoto), "--rootdir", str(root), "--confcutdir", str(root)]
    finally:
        if vuoto is not None:
            try:
                vuoto.unlink()
            except OSError:
                pass
