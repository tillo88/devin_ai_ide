"""Token gate a segreto condiviso (devin/ui/token_gate.py) — 2026-07-18.

Test HTTP-level via fastapi.testclient.TestClient: il gate vive nello stack
ASGI, quindi va verificato attraverso richieste vere sull'app assemblata
(non e' il pattern endpoint-level degli altri test, e' voluto). Il client
non-loopback e' simulato con `TestClient(app, client=("10.0.0.5", 5000))`
(supportato da starlette 1.3.1); il default di TestClient e' ("testclient",
porta) che NON e' loopback — i test loopback passano client=("127.0.0.1", ...).

Isolamento: il segreto e' risolto dal middleware ad OGNI richiesta via
token_gate.resolve_api_token(); i test monkeypatchano l'env
DEVIN_API_TOKEN e puntano token_gate.CONFIG_PATH a un settings.json vuoto
su tmp_path, cosi' il file reale config/settings.json non influenza la
suite (e un eventuale ui.api_token futuro nel file reale non rompe il caso
"gate disabilitato").
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from devin.ui import fast_app, token_gate

LAN_CLIENT = ("10.0.0.5", 5000)
LOOPBACK_V4 = ("127.0.0.1", 5000)
LOOPBACK_V6 = ("::1", 5000)
SECRET = "gate-test-secret-7f3a"
WRONG = "not-the-secret"

# Endpoint rappresentativo protetto dal gate: lettura della history chat,
# senza parametri obbligatori e senza side effect (load-only).
PROBE = "/api/chat/history"


@pytest.fixture
def gate(tmp_path, monkeypatch):
    """Isola la risoluzione del segreto e ritorna enable()/disable()."""
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"models": {}}), encoding="utf-8")
    monkeypatch.setattr(token_gate, "CONFIG_PATH", str(settings))
    monkeypatch.delenv(token_gate.ENV_TOKEN_VAR, raising=False)

    def enable(token=SECRET):
        monkeypatch.setenv(token_gate.ENV_TOKEN_VAR, token)

    def disable():
        monkeypatch.delenv(token_gate.ENV_TOKEN_VAR, raising=False)

    enable.settings_path = settings
    return enable, disable


def _client(client_addr=LAN_CLIENT):
    return TestClient(fast_app.app, client=client_addr)


# --- 1. Gate disabilitato: comportamento corrente preservato ----------------

def test_gate_disabled_allows_non_loopback(gate):
    _, disable = gate
    disable()
    resp = _client().get(PROBE)
    assert resp.status_code == 200


# --- 2. Gate abilitato: 401 senza token, loopback esente --------------------

def test_enabled_non_loopback_without_token_gets_401_json(gate):
    enable, _ = gate
    enable()
    resp = _client().get(PROBE)
    assert resp.status_code == 401
    assert resp.json() == {"error": "unauthorized"}
    assert "application/json" in resp.headers["content-type"]


def test_enabled_loopback_v4_without_token_passes(gate):
    enable, _ = gate
    enable()
    resp = _client(LOOPBACK_V4).get(PROBE)
    assert resp.status_code == 200


def test_enabled_loopback_v6_without_token_passes(gate):
    enable, _ = gate
    enable()
    resp = _client(LOOPBACK_V6).get(PROBE)
    assert resp.status_code == 200


# --- 3. Tre canali di autenticazione: pass e 401 su token sbagliato ---------

def test_enabled_bearer_header_passes(gate):
    enable, _ = gate
    enable()
    resp = _client().get(PROBE, headers={"Authorization": f"Bearer {SECRET}"})
    assert resp.status_code == 200


def test_enabled_wrong_bearer_header_gets_401(gate):
    enable, _ = gate
    enable()
    resp = _client().get(PROBE, headers={"Authorization": f"Bearer {WRONG}"})
    assert resp.status_code == 401
    assert resp.json() == {"error": "unauthorized"}


def test_enabled_cookie_passes(gate):
    enable, _ = gate
    enable()
    client = _client()
    client.cookies.set(token_gate.COOKIE_NAME, SECRET)
    resp = client.get(PROBE)
    assert resp.status_code == 200


def test_enabled_wrong_cookie_gets_401(gate):
    enable, _ = gate
    enable()
    client = _client()
    client.cookies.set(token_gate.COOKIE_NAME, WRONG)
    resp = client.get(PROBE)
    assert resp.status_code == 401


def test_enabled_query_token_passes(gate):
    enable, _ = gate
    enable()
    resp = _client().get(PROBE, params={"token": SECRET})
    assert resp.status_code == 200


def test_enabled_wrong_query_token_gets_401(gate):
    enable, _ = gate
    enable()
    resp = _client().get(PROBE, params={"token": WRONG})
    assert resp.status_code == 401


# --- 4. Bootstrap cookie da ?token= -----------------------------------------

def test_query_token_success_sets_httponly_cookie_and_bootstraps(gate):
    enable, _ = gate
    enable()
    client = _client()
    resp = client.get(PROBE, params={"token": SECRET})
    assert resp.status_code == 200

    set_cookie = resp.headers["set-cookie"]
    assert f"{token_gate.COOKIE_NAME}=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=Lax" in set_cookie.replace("samesite=lax", "SameSite=Lax")
    # Il cookie e' nel jar del client: la richiesta successiva, SENZA query
    # token e senza header, passa via cookie (e' il bootstrap della SPA da un
    # solo URL con ?token=, fetch/EventSource compresi).
    assert client.cookies.get(token_gate.COOKIE_NAME) == SECRET
    followup = client.get(PROBE)
    assert followup.status_code == 200


def test_wrong_query_token_does_not_set_cookie(gate):
    enable, _ = gate
    enable()
    client = _client()
    resp = client.get(PROBE, params={"token": WRONG})
    assert resp.status_code == 401
    assert "set-cookie" not in resp.headers
    assert client.cookies.get(token_gate.COOKIE_NAME) is None


# --- 5. Endpoint rappresentativo dietro il gate (pass + 401) -----------------

def test_history_endpoint_behind_gate_401_then_pass(gate):
    enable, _ = gate
    enable()
    client = _client()
    denied = client.get(PROBE)
    assert denied.status_code == 401
    assert denied.json() == {"error": "unauthorized"}
    allowed = client.get(PROBE, headers={"Authorization": f"Bearer {SECRET}"})
    assert allowed.status_code == 200
    body = allowed.json()
    assert "history" in body and "updated_at" in body


# --- 6. Risoluzione del segreto: precedenza env > settings -------------------

def test_settings_token_used_when_env_absent(gate, monkeypatch):
    enable, _ = gate
    enable.settings_path.write_text(
        json.dumps({"ui": {"api_token": SECRET}}), encoding="utf-8")
    monkeypatch.delenv(token_gate.ENV_TOKEN_VAR, raising=False)
    denied = _client().get(PROBE)
    assert denied.status_code == 401
    allowed = _client().get(PROBE, headers={"Authorization": f"Bearer {SECRET}"})
    assert allowed.status_code == 200


def test_env_token_takes_precedence_over_settings(gate):
    enable, _ = gate
    enable.settings_path.write_text(
        json.dumps({"ui": {"api_token": WRONG}}), encoding="utf-8")
    enable(SECRET)
    # Vince l'env: il token del file NON apre, quello dell'env si'.
    from_file = _client().get(PROBE, headers={"Authorization": f"Bearer {WRONG}"})
    assert from_file.status_code == 401
    from_env = _client().get(PROBE, headers={"Authorization": f"Bearer {SECRET}"})
    assert from_env.status_code == 200


# --- 7. Confronto a tempo costante (pin sul sorgente; niente test di timing,
#        sarebbero flaky) -----------------------------------------------------

def test_constant_time_comparison_used():
    source = Path(token_gate.__file__).read_text(encoding="utf-8")
    assert "hmac.compare_digest" in source
