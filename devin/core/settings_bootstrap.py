"""Bootstrap di config/settings.json dal template versionato.

`config/settings.json` e' per-macchina (rig_self_hosted, ui.host, ui.api_token)
e NON e' tracciato in git: su un clone nuovo non esiste. Questo helper lo crea
copiando `config/settings.example.json`, cosi' il backend parte comunque.

Idempotente e non distruttivo: se settings.json esiste gia' non tocca nulla —
quindi sul rig la config locale (rig_self_hosted=true, ecc.) resta intatta.
"""

from __future__ import annotations

import shutil
from pathlib import Path


def ensure_settings(config_path: str | Path) -> str:
    """Se `config_path` non esiste, lo crea dal template accanto
    (`settings.example.json`). Ritorna il path (invariato)."""
    path = Path(config_path)
    if path.exists():
        return str(path)
    example = path.with_name("settings.example.json")
    try:
        if example.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(example, path)
    except OSError:
        # Fail-soft: se non possiamo scrivere, lasciamo che il chiamante gestisca
        # l'assenza del file come prima (default fail-safe gia' presenti).
        pass
    return str(path)
