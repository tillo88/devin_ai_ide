"""PWA slice (2026-07-18): manifest, service worker root-scope, icone,
meta tag della shell /app.

Verifica HTTP-level via TestClient + source-level per la policy del service
worker (nessun browser disponibile in questo ambiente: il comportamento
runtime del SW non e' testabile qui). Il client e' loopback esplicito cosi'
il test resta verde anche con token gate configurato (loopback sempre
esente, vedi devin/ui/token_gate.py).
"""

import json
from pathlib import Path

from fastapi.testclient import TestClient

SW_PATH = Path("devin/ui/static/sw.js")


def _client():
    from devin.ui.fast_app import app
    return TestClient(app, client=("127.0.0.1", 5000))


def test_manifest_served_with_manifest_content_type_and_required_keys():
    response = _client().get("/manifest.webmanifest")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/manifest+json")
    data = json.loads(response.text)
    assert data["name"] == "DEVIN AI IDE"
    assert data["short_name"] == "DEVIN"
    assert data["display"] == "standalone"
    assert data["start_url"] == "/app"
    assert data["theme_color"]
    assert data["background_color"]
    sizes = {icon["sizes"] for icon in data["icons"]}
    assert {"192x192", "512x512"} <= sizes
    for icon in data["icons"]:
        assert icon["type"] == "image/png"
        assert icon["src"].startswith("/static/icons/")


def test_service_worker_served_at_root_scope_with_revalidate_headers():
    response = _client().get("/sw.js")
    assert response.status_code == 200
    assert response.headers["Service-Worker-Allowed"] == "/"
    assert "javascript" in response.headers["content-type"]
    # Il file SW deve sempre rivalidarsi: e' il meccanismo di update del SW.
    assert "no-cache" in response.headers["Cache-Control"]


def test_service_worker_source_shell_only_cache_and_network_only_api():
    source = SW_PATH.read_text(encoding="utf-8")
    # Cache versionata + cleanup delle vecchie cache su activate.
    assert 'CACHE_VERSION = "devin-shell-v' in source
    assert "caches.delete" in source
    # Precache SOLO shell: route HTML + asset SPA + manifest + icone.
    for shell_url in (
        '"/app"',
        '"/manifest.webmanifest"',
        '"/static/css/codex_app.css"',
        '"/static/js/codex_app.js"',
        '"/static/icons/icon-192.png"',
        '"/static/icons/icon-512.png"',
    ):
        assert shell_url in source
    # Privacy: TUTTE le /api/* network-only (memoria/chat mai in cache).
    api_check = source.index('startsWith("/api/")')
    api_branch = source[api_check:source.index("return;", api_check)]
    assert "event.respondWith(fetch(request))" in api_branch
    assert "caches." not in api_branch
    # Il branch /api/ precede qualunque scrittura in cache: nessuna API puo'
    # finire in cache per costruzione.
    assert api_check < source.index("cache.put(request")


def test_pwa_icons_served_as_png():
    client = _client()
    for name in ("icon-192.png", "icon-512.png"):
        response = client.get(f"/static/icons/{name}")
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert response.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_app_shell_includes_pwa_meta_and_sw_registration():
    response = _client().get("/app")
    assert response.status_code == 200
    html = response.text
    assert '<link rel="manifest" href="/manifest.webmanifest">' in html
    assert 'name="viewport"' in html
    assert 'name="theme-color"' in html
    assert "apple-mobile-web-app-capable" in html
    assert "mobile-web-app-capable" in html
    assert "serviceWorker" in html
    assert "navigator.serviceWorker.register('/sw.js')" in html
