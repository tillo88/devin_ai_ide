"""La vista di DEVIN negli strumenti: screenshot e immagini mostrati al modello.

Roadmap 2 (19/10). Quando il server del rig ha il proiettore della visione
(``AIClient.vede_immagini``), DEVIN non "guarda" piu' solo attraverso il testo:

- dopo ``guarda_pagina`` lo screenshot della pagina gli arriva come immagine;
- dopo ``modella_3d`` l'anteprima del pezzo (isometrica, fronte, alto, destra);
- ``guarda_immagine`` gli mostra un'immagine del progetto (un grafico prodotto,
  un'immagine di riferimento, uno screenshot salvato).

Le immagini entrano come messaggio dell'utente subito dopo i risultati degli
strumenti, con una riga che dice da dove vengono (il sistema, non l'utente). Le
piu' vecchie diventano una riga di testo: un'immagine costa ~400 token a ogni
turno, e il modello l'ha gia' vista.

Solo file dentro cartelle ammesse (il progetto, le cartelle degli screenshot),
regolari, non symlink, PNG/JPEG/GIF/BMP riconosciuti dai byte, al piu' 15 MB.
"""
from __future__ import annotations

import base64
from pathlib import Path
import re
import tempfile
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

MAX_BYTES = 15 * 1024 * 1024
TIENI = 2
MIME = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
)
SCREENSHOT_RE = re.compile(r"^screenshot: (.+\.png)\s*$", re.M)
ANTEPRIMA_RE = re.compile(r"^anteprima: (.+\.png) \(", re.M)
MODELLI = Path("~/.local/share/devin/modelli").expanduser()
PREFISSO_OCCHI = "devin-occhi-"
SEGNO = "[immagini allegate dal sistema, non dall'utente]"

STRUMENTO = {"type": "function", "function": {
    "name": "guarda_immagine",
    "description": ("Ti mostra un'immagine (PNG, JPEG, GIF, BMP) del progetto: la vedi nel messaggio "
                    "successivo. Per controllare un grafico o un'immagine prodotta, un riferimento "
                    "fornito dall'utente o uno screenshot salvato."),
    "parameters": {"type": "object", "properties": {
        "percorso": {"type": "string", "description": "percorso dell'immagine, relativo al progetto"}},
        "required": ["percorso"], "additionalProperties": False}}}

NOTA_PROMPT = ("VISIONE: vedi le immagini. Dopo guarda_pagina ricevi anche lo screenshot della pagina: "
               "controlla com'e' fatta davvero (impaginazione, elementi visibili, errori a schermo). "
               "Dopo modella_3d ricevi l'anteprima del pezzo (isometrica, fronte, alto, destra): "
               "confrontala con la richiesta e correggi il codice se la forma non torna. "
               "Con guarda_immagine puoi guardare un'immagine del progetto. Descrivi solo cio' che vedi.")


def mime(raw: bytes) -> Optional[str]:
    for magia, tipo in MIME:
        if raw.startswith(magia):
            return tipo
    return None


def cartella_screenshot_ammessa(percorso: Path) -> bool:
    """Le cartelle temporanee di ``occhi.guarda`` (``/tmp/devin-occhi-*``)."""
    cartella = percorso.parent
    return (cartella.name.startswith(PREFISSO_OCCHI)
            and cartella.parent.resolve() == Path(tempfile.gettempdir()).resolve())


def carica(percorso: str | Path, radici: Sequence[Path], base: Optional[Path] = None,
           screenshot_temporanei: bool = False) -> Tuple[Optional[str], str]:
    """``(data URL, '')`` oppure ``(None, motivo)``. Mai un'eccezione."""
    testo = str(percorso or "").strip()
    if not testo:
        return None, "percorso vuoto"
    candidato = Path(testo)
    if not candidato.is_absolute() and base is not None:
        candidato = base / candidato
    try:
        if candidato.is_symlink():
            return None, "e' un collegamento simbolico"
        reale = candidato.resolve(strict=True)
    except (OSError, RuntimeError):
        return None, "file non trovato"
    dentro = any(reale == r or r in reale.parents for r in (Path(x).resolve() for x in radici))
    if not dentro and not (screenshot_temporanei and cartella_screenshot_ammessa(reale)):
        return None, "fuori dalle cartelle ammesse"
    if not reale.is_file():
        return None, "non e' un file"
    dimensione = reale.stat().st_size
    if dimensione > MAX_BYTES:
        return None, f"troppo grande ({dimensione} byte, max {MAX_BYTES})"
    dati = reale.read_bytes()
    tipo = mime(dati)
    if tipo is None:
        return None, "non e' un PNG, JPEG, GIF o BMP"
    return f"data:{tipo};base64," + base64.b64encode(dati).decode("ascii"), ""


def screenshot_di(risultato: str) -> Optional[str]:
    """Il percorso dello screenshot scritto da ``occhi.guarda`` nel suo risultato."""
    trovato = SCREENSHOT_RE.search(str(risultato or ""))
    return trovato.group(1) if trovato else None


def anteprima_di(risultato: str) -> Optional[str]:
    """Il percorso dell'anteprima scritta da ``modella_3d`` nel suo risultato."""
    trovato = ANTEPRIMA_RE.search(str(risultato or ""))
    return trovato.group(1) if trovato else None


def messaggio(immagini: Iterable[Tuple[str, str]]) -> Optional[Dict[str, Any]]:
    """Messaggio dell'utente con le immagini e una riga per ciascuna (da dove viene)."""
    elenco = list(immagini)
    if not elenco:
        return None
    righe = [SEGNO] + [f"- {etichetta}" for _, etichetta in elenco]
    parti: List[Dict[str, Any]] = [{"type": "image_url", "image_url": {"url": url}} for url, _ in elenco]
    parti.append({"type": "text", "text": "\n".join(righe)})
    return {"role": "user", "content": parti}


def alleggerisci(messaggi: List[Dict[str, Any]], tieni: int = TIENI) -> int:
    """Le immagini dei messaggi piu' vecchi diventano testo; restano le ultime ``tieni``.

    Restituisce quante immagini ha tolto. Modifica ``messaggi`` sul posto.
    """
    con_immagini = [i for i, m in enumerate(messaggi)
                    if isinstance(m.get("content"), list)
                    and any(p.get("type") == "image_url" for p in m["content"] if isinstance(p, dict))]
    tolte = 0
    for i in con_immagini[:-tieni] if tieni else con_immagini:
        parti = messaggi[i]["content"]
        n = sum(1 for p in parti if isinstance(p, dict) and p.get("type") == "image_url")
        testo = "\n".join(str(p.get("text") or "") for p in parti if isinstance(p, dict) and p.get("type") == "text")
        messaggi[i] = {**messaggi[i], "content": (testo + f"\n[{n} immagine/i gia' viste, non piu' allegate]").strip()}
        tolte += n
    return tolte


def caratteri(contenuto: Any) -> int:
    """Lunghezza per la stima dei token: il testo conta, un'immagine conta come ~400 token."""
    if isinstance(contenuto, list):
        totale = 0
        for parte in contenuto:
            if isinstance(parte, dict) and parte.get("type") == "image_url":
                totale += 400 * 4
            elif isinstance(parte, dict):
                totale += len(str(parte.get("text") or ""))
        return totale
    return len(str(contenuto or ""))
