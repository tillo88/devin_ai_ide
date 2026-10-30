"""LiveCodeBench come ESAME di DEVIN (30/10): mai insegnato, mai nel Council, mai in memoria.

I compiti li prepara sul rig ``devin-livecodebench-setup-v1`` (ai-rig-ops) in
``/mnt/ai-rig-shared/esame/livecodebench/compiti.jsonl``: solo il testo, il codice di partenza e
il tipo. **I test non arrivano mai qui**: restano in una cartella di root e li esegue un giudice
del rig dopo il giro, sul codice finale di DEVIN.

Un caso d'esame (``metadata.esame``) nel training:

* niente gold e niente "tentativo precedente" nel prompt;
* memoria in sola lettura e niente web (``Orchestrator.modalita_esame``);
* nessuna lezione dal ripasso, nessuna riga negli export (teacher packet, SFT);
* l'esito del run e' solo "dichiarato" da DEVIN: il voto e' quello del giudice.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

FONTE = "livecodebench"
ESAMI = frozenset({FONTE})
COMPITI = Path(os.environ.get("DEVIN_LIVECODEBENCH_COMPITI",
                              "/mnt/ai-rig-shared/esame/livecodebench/compiti.jsonl"))
CAMPI = ("id", "piattaforma", "data", "difficolta", "titolo", "testo", "starter_code", "tipo", "func_name")
MAX_TESTO = 20_000


def e_esame(caso: Dict[str, Any]) -> bool:
    meta = caso.get("metadata") if isinstance(caso.get("metadata"), dict) else {}
    return bool(meta.get("esame")) or caso.get("source") in ESAMI


def compito(c: Dict[str, Any]) -> str:
    testo = str(c["testo"]).strip()[:MAX_TESTO]
    if c["tipo"] == "functional":
        return (f"{testo}\n\nStarter code:\n```python\n{str(c['starter_code']).rstrip()}\n```\n\n"
                "Write the solution in a Python module named solution.py: the class Solution with the method "
                f"`{c['func_name']}` exactly as in the starter code (it will be called as "
                f"Solution().{c['func_name']}(...)). Standard library only. Also write your own pytest tests, "
                "for example with the examples in the statement.")
    return (f"{testo}\n\nWrite a complete Python program in solution.py that reads the input from standard input "
            "and writes the answer to standard output, exactly in the format of the statement. Do not hardcode the "
            "sample inputs. Standard library only. Also write your own pytest tests, for example running "
            "solution.py on the examples in the statement.")


def titolo(c: Dict[str, Any]) -> str:
    return f"LiveCodeBench {c['id']}: {str(c['titolo'])[:70]}".rstrip()


def leggi_compiti(path: Path) -> List[Dict[str, Any]]:
    out = []
    for riga in path.read_text(encoding="utf-8").splitlines():
        if not riga.strip():
            continue
        c = json.loads(riga)
        if not isinstance(c, dict) or any(k not in c for k in CAMPI) or c["tipo"] not in {"stdin", "functional"}:
            raise ValueError("compiti.jsonl: riga senza i campi dell'esame")
        if "test" in c:
            raise ValueError("compiti.jsonl contiene test: l'esame non e' piu' cieco")
        out.append(c)
    return out


def casi(compiti: Iterable[Dict[str, Any]], dal: str, al: Optional[str] = None, limit: int = 50, offset: int = 0,
         piattaforme: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    scelte = set(piattaforme or ())
    buoni, scartati = [], {"prima_della_data": 0, "dopo_la_data": 0, "errata": 0, "piattaforma": 0}
    for c in sorted(compiti, key=lambda x: (x["data"], x["id"])):
        if c["data"][:10] < dal:
            scartati["prima_della_data"] += 1
        elif al and c["data"][:10] > al:
            scartati["dopo_la_data"] += 1
        elif c.get("errata"):
            scartati["errata"] += 1
        elif scelte and c["piattaforma"] not in scelte:
            scartati["piattaforma"] += 1
        else:
            buoni.append(c)
    return {"idonei": len(buoni), "scartati": scartati, "casi": [{
        "title": titolo(c),
        "kind": "code_generation",
        "prompt": compito(c),
        "expected_signals": ["tests_pass"],
        "tags": ["python", FONTE, "esame", c["piattaforma"], c["difficolta"]],
        "esame": True,
        "lcb_id": c["id"],
        "tipo": c["tipo"],
        "func_name": c["func_name"],
        "data": c["data"],
        "piattaforma": c["piattaforma"],
        "difficolta": c["difficolta"],
    } for c in buoni[offset:offset + max(1, limit)]]}


def import_livecodebench_cases(store, dal: str, al: Optional[str] = None, limit: int = 50, offset: int = 0,
                               piattaforme: Optional[Iterable[str]] = None,
                               path: Optional[Path] = None) -> Dict[str, Any]:
    compiti = leggi_compiti(Path(path or COMPITI))
    scelta = casi(compiti, dal, al, limit=limit, offset=offset, piattaforme=piattaforme)
    creati = store.seed_cases(scelta["casi"], source=FONTE)
    return {"dataset_rows": len(compiti), "idonei": scelta["idonei"], "scartati": scelta["scartati"],
            "converted": len(scelta["casi"]), "created": len(creati),
            "skipped_existing": len(scelta["casi"]) - len(creati), "source": FONTE, "dal": dal, "al": al}
