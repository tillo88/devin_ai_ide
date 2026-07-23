"""Test della catena provider web (SearXNG primario + TinyFish fallback)."""

from __future__ import annotations

import pytest

from devin.ai.web_search import (
    FallbackProvider,
    SearXNGProvider,
    TinyFishProvider,
    WebSearchProvider,
    get_web_search_provider,
)


class _Stub(WebSearchProvider):
    def __init__(self, results=None, exc=None):
        self.results = results
        self.exc = exc
        self.called = 0

    def search(self, query, max_results=5):
        self.called += 1
        if self.exc:
            raise self.exc
        return list(self.results or [])


# --- FallbackProvider ------------------------------------------------------

def test_fallback_su_errore_del_primario():
    primary = _Stub(exc=RuntimeError("searxng down"))
    backup = _Stub(results=[{"url": "u", "title": "t"}])
    fp = FallbackProvider([primary, backup])
    res = fp.search("q")
    assert res == [{"url": "u", "title": "t"}]
    assert primary.called == 1 and backup.called == 1  # ha davvero fatto fallback


def test_primario_ok_non_usa_il_fallback():
    primary = _Stub(results=[{"url": "u"}])
    backup = _Stub(results=[{"url": "cloud"}])
    fp = FallbackProvider([primary, backup])
    assert fp.search("q") == [{"url": "u"}]
    assert backup.called == 0  # il fallback NON e' stato toccato


def test_primario_vuoto_non_fa_fallback_privacy():
    # SearXNG risponde ma con 0 risultati -> NON si va sul cloud (privacy).
    primary = _Stub(results=[])
    backup = _Stub(results=[{"url": "cloud"}])
    fp = FallbackProvider([primary, backup])
    assert fp.search("q") == []
    assert backup.called == 0


def test_tutti_falliti_propaga_errore():
    fp = FallbackProvider([_Stub(exc=RuntimeError("a")), _Stub(exc=RuntimeError("b"))])
    with pytest.raises(RuntimeError):
        fp.search("q")


# --- factory ---------------------------------------------------------------

def test_factory_provider_singolo():
    cfg = {"web_search": {"provider": "searxng", "searxng_url": "http://x:8081"}}
    p = get_web_search_provider(cfg)
    assert isinstance(p, SearXNGProvider)  # singolo, non una catena


def test_factory_searxng_primario_tinyfish_fallback(monkeypatch):
    monkeypatch.setenv("TINYFISH_API_KEY", "sk-test")
    cfg = {"web_search": {"provider": "searxng", "searxng_url": "http://x:8081", "fallback": "tinyfish"}}
    p = get_web_search_provider(cfg)
    assert isinstance(p, FallbackProvider)
    assert [type(x).__name__ for x in p.providers] == ["SearXNGProvider", "TinyFishProvider"]


def test_factory_salta_provider_non_costruibile(monkeypatch):
    # tinyfish senza chiave -> saltato; resta solo searxng (singolo, non catena).
    monkeypatch.delenv("TINYFISH_API_KEY", raising=False)
    cfg = {"web_search": {"providers": ["searxng", "tinyfish"], "searxng_url": "http://x:8081"}}
    p = get_web_search_provider(cfg)
    assert isinstance(p, SearXNGProvider)
