"""Token gate a segreto condiviso per client NON-loopback (2026-07-18).

Threat model: quando la UI viene esposta fuori da localhost (ui.host=0.0.0.0
in settings.json, es. accesso da LAN o via Tailscale), TUTTI gli endpoint —
lettura file, avvio run, stop modelli — erano aperti a chiunque raggiungesse
la porta. Questo middleware richiede un segreto condiviso ai client
non-loopback; il loopback (GUI desktop locale) resta senza attriti e senza
token.

Design (approvato dall'owner):
- Segreto da env DEVIN_API_TOKEN (precedenza) oppure settings.json
  ui.api_token. Non configurato/vuoto -> gate DISABILITATO (comportamento
  precedente preservato: i test esistenti restano verdi senza saperlo).
- Loopback (127.0.0.1, ::1) sempre esente.
- Tre canali di autenticazione, ne basta UNO:
  1. header `Authorization: Bearer <secret>`;
  2. cookie `devin_token` (HttpOnly, SameSite=Lax; niente flag Secure: la
     app e' servita in HTTP piano da uvicorn, vedi run_server);
  3. query param `?token=<secret>` — necessario perche' EventSource (SSE,
     /api/run/{id}/events/stream) non puo' impostare header e la SPA ha
     decine di fetch/EventSource hardcoded. Su auth via query la risposta
     IMPOSTA il cookie: la SPA fa bootstrap da un solo URL con ?token= e
     ogni fetch/EventSource successivo si autentica via cookie.
- Confronto a tempo costante (hmac.compare_digest); il segreto non viene
  MAI loggato da questo modulo.
- Fallimento -> 401 JSON {"error": "unauthorized"}, nessun dettaglio sul
  perche'.

Limiti noti (vedi docs/CONTINUITY_2026-07-18.md):
- l'access log di uvicorn registra le URL complete: con auth via query il
  token finisce nei log del server. Preferire il bootstrap una-tantum +
  cookie.
- l'IP client e' quello della connessione diretta: dietro un reverse proxy
  tutte le richieste apparirebbero provenire dal proxy (se il proxy e' in
  loopback, il gate risulta bypassato). Supportate solo connessioni dirette.
- un solo segreto condiviso: niente token per-device, niente revoca.
"""

import hmac
import json
import os
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import parse_qs

from starlette.responses import JSONResponse

ROOT = Path(__file__).resolve().parents[2]
# Stesso file letto da fast_app (che fissa CONFIG_PATH in modo identico);
# duplicato qui per non importare fast_app dal middleware (import circolare).
CONFIG_PATH = str(ROOT / "config" / "settings.json")

ENV_TOKEN_VAR = "DEVIN_API_TOKEN"
SETTINGS_SECTION = "ui"
SETTINGS_KEY = "api_token"
COOKIE_NAME = "devin_token"
QUERY_PARAM = "token"

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "::ffff:127.0.0.1"})


def resolve_api_token(config_path=None) -> str:
    """Ritorna il segreto configurato, o "" se il gate e' disabilitato.

    Precedenza: env DEVIN_API_TOKEN > settings.json ui.api_token.
    Risolto ad OGNI richiesta: cambiare env/file non richiede restart e i
    test possono monkeypatchare env/CONFIG_PATH liberamente.
    """
    env = (os.environ.get(ENV_TOKEN_VAR) or "").strip()
    if env:
        return env
    try:
        cfg = json.loads(Path(config_path or CONFIG_PATH).read_text(encoding="utf-8"))
    except Exception:
        return ""
    section = cfg.get(SETTINGS_SECTION) or {}
    return str(section.get(SETTINGS_KEY) or "").strip()


def _extract_presented_token(scope) -> tuple:
    """Ritorna (token_presentato, via_query). ("", False) se assente.

    Ordine: Bearer header, poi cookie, poi query param. Il token non viene
    mai loggato ne' incluso in eccezioni.
    """
    authorization = ""
    cookie_headers = []
    for name, value in scope.get("headers", []):
        key = name.decode("latin-1").lower()
        if key == "authorization":
            authorization = value.decode("latin-1")
        elif key == "cookie":
            cookie_headers.append(value.decode("latin-1"))

    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip(), False

    if cookie_headers:
        jar = SimpleCookie()
        try:
            jar.load("; ".join(cookie_headers))
        except Exception:
            jar = SimpleCookie()
        morsel = jar.get(COOKIE_NAME)
        if morsel is not None and morsel.value:
            return morsel.value, False

    raw_query = scope.get("query_string", b"")
    if raw_query:
        params = parse_qs(raw_query.decode("latin-1"), keep_blank_values=True)
        values = params.get(QUERY_PARAM)
        if values and values[0]:
            return values[0], True

    return "", False


def _send_with_bootstrap_cookie(send, secret: str):
    """Wrappa send per aggiungere Set-Cookie alla risposta (bootstrap da
    ?token=: il browser conserva il cookie HttpOnly e le richieste seguenti —
    fetch E EventSource — si autenticano da sole)."""
    jar = SimpleCookie()
    jar[COOKIE_NAME] = secret
    jar[COOKIE_NAME]["path"] = "/"
    jar[COOKIE_NAME]["httponly"] = True
    jar[COOKIE_NAME]["samesite"] = "Lax"
    # Niente flag Secure: run_server serve HTTP piano (nessun TLS in uvicorn).
    set_cookie = jar.output(header="").strip().encode("latin-1")

    async def wrapped(message):
        if message["type"] == "http.response.start":
            headers = list(message.get("headers", []))
            headers.append((b"set-cookie", set_cookie))
            message = {**message, "headers": headers}
        await send(message)

    return wrapped


class TokenGateMiddleware:
    """ASGI middleware puro (niente BaseHTTPMiddleware: zero buffering dei
    body e nessuna interferenza con le risposte SSE/streaming).

    Fail-closed sul client host sconosciuto: se scope["client"] manca o non
    e' loopback e il gate e' attivo, si richiede il token.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        secret = resolve_api_token()
        if not secret:
            # Gate disabilitato: comportamento identico a prima del gate.
            await self.app(scope, receive, send)
            return

        client = scope.get("client") or ("", 0)
        if client[0] in _LOOPBACK_HOSTS:
            await self.app(scope, receive, send)
            return

        presented, via_query = _extract_presented_token(scope)
        if presented and hmac.compare_digest(presented, secret):
            if via_query:
                send = _send_with_bootstrap_cookie(send, secret)
            await self.app(scope, receive, send)
            return

        response = JSONResponse({"error": "unauthorized"}, status_code=401)
        await response(scope, receive, send)
