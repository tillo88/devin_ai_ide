"""Robust model routing (2026-07-22): on a self-hosted rig, DEVIN always
targets the remote model (Ornith) and never silently falls back to a spurious
local model; the optional rig api key is sent only to remote endpoints.
"""
import json

from devin.ai.client import AIClient


def _client(tmp_path, **models):
    cfg = {"models": {"rig_host": "192.0.2.10", "rig_port": 8080,
                      "rig_models": {"unified": "Ornith-test"}, **models}}
    p = tmp_path / "settings.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    return AIClient(config_path=str(p))


def test_self_hosted_rig_always_uses_remote(tmp_path):
    c = _client(tmp_path, rig_self_hosted=True)
    c.remote_coder_ok = False
    c.remote_reasoning_ok = False
    # Even with remote marked not-ok, self-hosted must target the rig model,
    # never the local qwen fallback.
    url_c, model_c = c._get_endpoints("coder")
    url_r, model_r = c._get_endpoints("reasoning")
    assert "192.0.2.10:8080" in url_c and model_c == "Ornith-test"
    assert "192.0.2.10:8080" in url_r and model_r == "Ornith-test"
    assert "localhost:8000" not in url_c


def test_non_self_hosted_still_falls_back_local(tmp_path):
    c = _client(tmp_path, rig_self_hosted=False)
    c.remote_coder_ok = False
    url, _ = c._get_endpoints("coder")
    assert "localhost:8000" in url  # PC backup path preserved


def test_rig_api_key_only_on_remote(tmp_path):
    c = _client(tmp_path, rig_self_hosted=True, rig_api_key="secret123")
    assert c._auth_headers(c.remote_coder_url) == {"Authorization": "Bearer secret123"}
    assert c._auth_headers("http://localhost:8000/v1/chat/completions") == {}


def test_no_key_no_header(tmp_path):
    c = _client(tmp_path, rig_self_hosted=True)
    assert c._auth_headers(c.remote_coder_url) == {}
