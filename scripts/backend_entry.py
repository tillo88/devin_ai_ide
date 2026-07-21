"""Entry point del sidecar backend per PyInstaller (PACKAGING-ROADMAP FASE 1).

Perche' non usare direttamente devin/ui/fast_app.py come entry: PyInstaller
colloca lo script di ingresso alla radice di _internal, quindi
ROOT = Path(__file__).resolve().parents[2] in fast_app punterebbe FUORI dal
bundle. Importando fast_app come modulo del package, __file__ resta
_internal/devin/ui/fast_app.pyc e ROOT = _internal, dove --add-data replica
devin/ui/templates, devin/ui/static e config/settings.json con gli stessi
path relativi del repo.

Follow-up FASE 3 (wizard): config utente in %APPDATA%\\DEVIN, non nel bundle.
"""
from devin.ui.fast_app import run_server

if __name__ == "__main__":
    run_server()
