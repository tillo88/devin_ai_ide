"""Fail-closed routing contract for the broker-owned DEVIN model slot."""

import json
from unittest.mock import patch

import pytest
import requests

from devin.ai.client import AIClient, RigUnavailableError


def _client(tmp_path, **models):
    cfg = {
        "models": {
            "rig_base_url": "http://127.0.0.1:9",
            "rig_required": True,
            "allow_local_fallback": False,
            **models,
        }
    }
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    with patch(
        "devin.ai.client.requests.get",
        side_effect=requests.exceptions.ConnectionError("slot down"),
    ):
        return AIClient(config_path=str(path))


def test_required_slot_never_falls_back_local(tmp_path):
    client = _client(tmp_path)
    client.remote_coder_ok = False
    client.remote_reasoning_ok = False
    with pytest.raises(RigUnavailableError, match="slot DEVIN non disponibile"):
        client._get_endpoints("coder")
    with pytest.raises(RigUnavailableError, match="slot DEVIN non disponibile"):
        client._get_endpoints("reasoning")


def test_local_fallback_requires_explicit_double_opt_in(tmp_path):
    client = _client(tmp_path, rig_required=False, allow_local_fallback=True)
    client.remote_coder_ok = False
    client.remote_reasoning_ok = False
    assert "localhost:8000" in client._get_endpoints("coder")[0]
    assert "localhost:8001" in client._get_endpoints("reasoning")[0]

    required = _client(tmp_path, rig_required=True, allow_local_fallback=True)
    required.remote_coder_ok = False
    with pytest.raises(RigUnavailableError):
        required._get_endpoints("coder")


def test_rig_endpoint_must_be_loopback(tmp_path):
    cfg = {"models": {"rig_base_url": "http://192.0.2.10:18081"}}
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    with pytest.raises(ValueError, match="loopback"):
        AIClient(config_path=str(path))


def test_rig_api_key_only_on_configured_origin(tmp_path):
    client = _client(tmp_path, rig_api_key="secret123")
    assert client._auth_headers(client.remote_coder_url) == {
        "Authorization": "Bearer secret123"
    }
    assert client._auth_headers("http://localhost:8000/v1/chat/completions") == {}


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def test_model_discovery_requires_exactly_one_openai_id(tmp_path):
    client = _client(tmp_path)
    assert client._parse_served_model(_Resp({"data": [{"id": "served-model"}]})) == "served-model"
    assert client._parse_served_model(_Resp({"data": []})) is None
    assert client._parse_served_model(_Resp({"data": [{"id": "one"}, {"id": "two"}]})) is None
    assert client._parse_served_model(_Resp({"models": [{"name": "ollama-hint"}]})) is None


def test_discovered_model_is_required_and_used_for_both_modes(tmp_path):
    client = _client(tmp_path)
    client.remote_coder_ok = True
    client.remote_reasoning_ok = True
    with pytest.raises(RigUnavailableError, match="model ID"):
        client._get_endpoints("coder")

    client.remote_model_actual = "served-model-from-slot"
    assert client._get_endpoints("coder")[1] == "served-model-from-slot"
    assert client._get_endpoints("reasoning")[1] == "served-model-from-slot"
    assert client.health()["routing_policy"] == "rig_required"
