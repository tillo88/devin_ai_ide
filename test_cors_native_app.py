"""CORS for the native desktop app: the bundled UI (tauri origin) must be able
to call the backend cross-origin; non-allowed origins must not be echoed.
"""
from fastapi.testclient import TestClient

from devin.ui import fast_app


def test_cors_allows_tauri_and_localhost_origins():
    client = TestClient(fast_app.app)
    for origin in ("tauri://localhost", "http://tauri.localhost",
                   "http://localhost:5000", "http://127.0.0.1:5000"):
        r = client.get("/api/health", headers={"Origin": origin})
        assert r.status_code == 200
        assert r.headers.get("access-control-allow-origin") == origin


def test_cors_does_not_echo_foreign_origin():
    client = TestClient(fast_app.app)
    r = client.get("/api/health", headers={"Origin": "https://evil.example.com"})
    # Request still succeeds (loopback), but the foreign origin is not allowed.
    assert r.headers.get("access-control-allow-origin") != "https://evil.example.com"
