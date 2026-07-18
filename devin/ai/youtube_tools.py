"""Trascrizioni YouTube (non 'guardare' il video: leggerne i sottotitoli).

Per un agente di coding/ricerca il valore di un video sta nel TESTO (spiegazioni,
comandi, nomi), non nei frame. `youtube-transcript-api` prende i sottotitoli
(quando esistono) senza API key. Vision sui frame = pesante e inutile qui, quindi
volutamente NON fatta.

Install: pip install youtube-transcript-api
"""

import re
from typing import Dict, Any

_ID_PATTERNS = [
    r"(?:v=|/shorts/|/embed/|youtu\.be/)([A-Za-z0-9_-]{11})",
    r"^([A-Za-z0-9_-]{11})$",
]


def extract_video_id(url_or_id: str) -> str:
    s = (url_or_id or "").strip()
    for pat in _ID_PATTERNS:
        m = re.search(pat, s)
        if m:
            return m.group(1)
    return ""


def youtube_transcript_available() -> bool:
    try:
        import youtube_transcript_api  # noqa: F401
        return True
    except Exception:
        return False


def get_transcript(url_or_id: str, languages=("it", "en"), max_chars: int = 12000) -> Dict[str, Any]:
    """Ritorna {"video_id","text","segments","error"}. text vuoto + error se
    non ci sono sottotitoli o il pacchetto manca (fail-soft)."""
    vid = extract_video_id(url_or_id)
    if not vid:
        return {"video_id": "", "text": "", "segments": 0,
                "error": "URL/ID YouTube non valido"}
    if not youtube_transcript_available():
        return {"video_id": vid, "text": "", "segments": 0,
                "error": "youtube-transcript-api non installato (pip install youtube-transcript-api)"}
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        rows = YouTubeTranscriptApi.get_transcript(vid, languages=list(languages))
    except Exception as exc:
        # fallback: qualunque lingua disponibile
        try:
            from youtube_transcript_api import YouTubeTranscriptApi
            listing = YouTubeTranscriptApi.list_transcripts(vid)
            rows = next(iter(listing)).fetch()
        except Exception:
            return {"video_id": vid, "text": "", "segments": 0,
                    "error": f"nessun sottotitolo disponibile: {exc}"}
    text = " ".join((r.get("text") or "").strip() for r in rows if r.get("text"))
    text = " ".join(text.split())[:max_chars]
    return {"video_id": vid, "text": text, "segments": len(rows), "error": ""}
