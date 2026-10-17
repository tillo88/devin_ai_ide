"""Risolutore di vincoli: il modello scrive, un programma risolve."""
import os
import sys

import pytest

from devin.core import risolutore


def _config(tmp_path, collegato=True):
    python = tmp_path / "venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    if collegato:
        os.symlink(sys.executable, python)
    return {"tools": {"solver_python": str(python)}}


def test_senza_ambiente_non_e_disponibile_e_lo_dice(tmp_path):
    config = _config(tmp_path, collegato=False)
    assert risolutore.disponibile(config) == (False, f"ambiente strumenti non installato ({tmp_path}/venv/bin/python)")
    assert risolutore.esegui("print(1)", config=config).startswith("ERRORE: risolutore non disponibile")


def test_esegue_e_restituisce_l_uscita(tmp_path):
    esito = risolutore.esegui("print(6 * 7)", config=_config(tmp_path))
    assert esito.splitlines()[0].startswith("exit 0") and "42" in esito


def test_timeout_spiega_cosa_fare(tmp_path):
    esito = risolutore.esegui("while True: pass", secondi=5, config=_config(tmp_path))
    assert esito.startswith("TIMEOUT") and "max_time_in_seconds" in esito


def test_errore_del_programma_arriva_al_modello(tmp_path):
    esito = risolutore.esegui("raise ValueError('regola impossibile')", config=_config(tmp_path))
    assert esito.startswith("exit 1") and "regola impossibile" in esito


def test_niente_stampa_si_dice(tmp_path):
    assert "non ha stampato nulla" in risolutore.esegui("x = 1", config=_config(tmp_path))


def test_turnazione_reale_del_17_10(tmp_path):
    pytest.importorskip("ortools")
    codice = '''
from ortools.sat.python import cp_model
m = cp_model.CpModel(); D = 28
x = {(d, s): m.NewBoolVar("") for d in range(D) for s in "MPNR"}
for d in range(D): m.AddExactlyOne(x[d, s] for s in "MPNR")
for w in range(7):
    for s in "MPNR": m.Add(sum(x[7*k+w, s] for k in range(4)) == 1)
for d in range(D):
    for a in "MPN":
        for b in "MPN":
            if a != b: m.AddBoolOr([x[d, a].Not(), x[(d+1) % D, b].Not()])
    ini = m.NewBoolVar("")
    m.AddBoolAnd([x[(d-1) % D, "R"], x[d, "R"].Not()]).OnlyEnforceIf(ini)
    m.AddBoolOr([x[(d-1) % D, "R"].Not(), x[d, "R"], ini])
    for k in range(1, 4): m.Add(x[(d+k) % D, "R"] == 0).OnlyEnforceIf(ini)
    m.AddBoolOr([x[(d+k) % D, "R"] for k in range(8)])
s = cp_model.CpSolver(); s.parameters.max_time_in_seconds = 30
print(s.StatusName(s.Solve(m)))
'''
    esito = risolutore.esegui(codice, config=_config(tmp_path))
    assert esito.startswith("exit 0") and "OPTIMAL" in esito


def test_lo_strumento_e_nell_agente_solo_se_c_e(tmp_path):
    from devin.agents.tool_agent import AgenteStrumenti
    senza = AgenteStrumenti(None, tmp_path, log=lambda *a: None)
    con = AgenteStrumenti(None, tmp_path, log=lambda *a: None, risolvi=lambda c, s=120: "exit 0\nok")
    nomi = lambda a: [t["function"]["name"] for t in a.strumenti]
    assert "risolvi_vincoli" not in nomi(senza) and "risolvi_vincoli" in nomi(con)
    assert "risolvi_vincoli" in con.messaggi[0]["content"]
    assert con._esegui_strumento("risolvi_vincoli", {"codice": "x"}) == "exit 0\nok"


def test_lock_con_hash_per_ogni_pacchetto():
    from pathlib import Path
    righe = [r for r in Path("config/venv-strumenti.lock").read_text().splitlines()
             if r.strip() and not r.startswith("#")]
    assert any(r.startswith("ortools==") for r in righe)
    assert all("==" in r and "--hash=sha256:" in r for r in righe)
