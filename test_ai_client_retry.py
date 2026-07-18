"""
Regression tests for devin/ai/client.py retry/backoff/circuit-breaker behavior.

Covered behaviors (documented contract, not new features):
- _is_retryable_error: Timeout/ConnectionError/ChunkedEncodingError retryable,
  HTTPError only for 502/503/504, everything else aborts.
- local(): exponential backoff 2s, 4s between MAX_RETRIES=3 attempts;
  returns None (never raises) after exhaustion; 4xx is NOT retried
  (client.py:533-545 documents this as deliberate); success records
  circuit-breaker success for remote URLs.
- stream(): 4xx yields a rejection notice without retrying; retryable
  errors retry with backoff and yield an error notice after exhaustion.
- Circuit breaker: opens after CIRCUIT_BREAKER_THRESHOLD consecutive rig
  failures, short-circuits refresh() while open, goes half-open after
  CIRCUIT_BREAKER_COOLDOWN, success resets it.

All network (requests.post/get), WOL (_send_wol), and sleeping (time.sleep)
are mocked so the suite is instant and has no side effects.
"""

from unittest.mock import MagicMock, patch, call

import pytest
import requests

from devin.ai.client import AIClient


# ---------------------------------------------------------------- helpers

def _http_error(status):
    resp = MagicMock()
    resp.status_code = status
    err = requests.exceptions.HTTPError(f"HTTP {status}")
    err.response = resp
    return err


def _bad_response(status):
    r = MagicMock()
    r.status_code = status
    r.raise_for_status.side_effect = _http_error(status)
    return r


def _ok_response(content="ok"):
    r = MagicMock()
    r.status_code = 200
    r.raise_for_status.return_value = None
    r.json.return_value = {"choices": [{"message": {"content": content}}]}
    return r


def _make_client(remote_ok=False):
    """Build an AIClient without real network/WOL side effects."""
    with patch("devin.ai.client.requests.get") as mock_get, \
         patch.object(AIClient, "_send_wol", return_value=False):
        if remote_ok:
            mock_get.return_value = MagicMock(status_code=200)
        else:
            mock_get.side_effect = requests.exceptions.ConnectionError("rig down")
        client = AIClient()
    # No-op the mid-retry refresh(); its behavior is tested separately.
    client.refresh = MagicMock()
    return client


MESSAGES = [{"role": "user", "content": "hi"}]


# ------------------------------------------------------- _is_retryable_error

class TestIsRetryableError:
    @pytest.mark.parametrize("exc", [
        requests.exceptions.Timeout("t"),
        requests.exceptions.ConnectionError("c"),
        requests.exceptions.ChunkedEncodingError("chunk"),
    ])
    def test_transient_errors_are_retryable(self, exc):
        client = _make_client()
        assert client._is_retryable_error(exc) is True

    @pytest.mark.parametrize("status", [502, 503, 504])
    def test_server_errors_are_retryable(self, status):
        client = _make_client()
        assert client._is_retryable_error(_http_error(status)) is True

    @pytest.mark.parametrize("status", [400, 401, 403, 404, 422, 500])
    def test_other_http_errors_not_retryable(self, status):
        client = _make_client()
        assert client._is_retryable_error(_http_error(status)) is False

    def test_http_error_without_response_not_retryable(self):
        client = _make_client()
        err = requests.exceptions.HTTPError("no response attached")
        err.response = None
        assert client._is_retryable_error(err) is False

    def test_generic_exception_not_retryable(self):
        client = _make_client()
        assert client._is_retryable_error(ValueError("bug")) is False


# ------------------------------------------------------------------- local()

class TestLocalRetry:
    def test_success_first_attempt_no_sleep(self):
        client = _make_client()
        with patch("devin.ai.client.requests.post", return_value=_ok_response("ciao")) as mock_post, \
             patch("devin.ai.client.time.sleep") as mock_sleep:
            assert client.local(MESSAGES) == "ciao"
        assert mock_post.call_count == 1
        mock_sleep.assert_not_called()
        client.refresh.assert_not_called()

    def test_two_failures_then_success_backoff_2_then_4(self):
        client = _make_client()
        effects = [
            requests.exceptions.ConnectionError("down"),
            requests.exceptions.Timeout("slow"),
            _ok_response("recovered"),
        ]
        with patch("devin.ai.client.requests.post", side_effect=effects) as mock_post, \
             patch("devin.ai.client.time.sleep") as mock_sleep:
            assert client.local(MESSAGES) == "recovered"
        assert mock_post.call_count == 3
        assert mock_sleep.call_args_list == [call(2), call(4)]
        # refresh() between retries, WOL only on the last retry slot
        assert client.refresh.call_count == 2

    def test_exhausted_retries_return_none(self):
        client = _make_client()
        with patch("devin.ai.client.requests.post",
                   side_effect=requests.exceptions.ConnectionError("down")) as mock_post, \
             patch("devin.ai.client.time.sleep") as mock_sleep:
            assert client.local(MESSAGES) is None
        assert mock_post.call_count == client.MAX_RETRIES
        assert mock_sleep.call_args_list == [call(2), call(4)]

    def test_http_4xx_aborts_without_retry(self):
        """4xx = server reachable but rejected the request: retrying the same
        payload is pointless (see client.py:533-545). Abort immediately."""
        client = _make_client()
        with patch("devin.ai.client.requests.post", return_value=_bad_response(400)) as mock_post, \
             patch("devin.ai.client.time.sleep") as mock_sleep:
            assert client.local(MESSAGES) is None
        assert mock_post.call_count == 1
        mock_sleep.assert_not_called()

    def test_http_503_is_retried(self):
        client = _make_client()
        effects = [_bad_response(503), _ok_response("after-503")]
        with patch("devin.ai.client.requests.post", side_effect=effects) as mock_post, \
             patch("devin.ai.client.time.sleep"):
            assert client.local(MESSAGES) == "after-503"
        assert mock_post.call_count == 2

    def test_generic_exception_aborts_without_retry(self):
        client = _make_client()
        with patch("devin.ai.client.requests.post", side_effect=ValueError("bug")) as mock_post, \
             patch("devin.ai.client.time.sleep") as mock_sleep:
            assert client.local(MESSAGES) is None
        assert mock_post.call_count == 1
        mock_sleep.assert_not_called()


# --------------------------------------------------------- circuit breaker

class TestCircuitBreaker:
    def test_remote_failures_open_breaker_after_threshold(self):
        client = _make_client(remote_ok=True)
        with patch("devin.ai.client.requests.post",
                   side_effect=requests.exceptions.ConnectionError("rig dead")), \
             patch("devin.ai.client.time.sleep"):
            assert client.local(MESSAGES) is None
        assert client._rig_health["failures"] == client.CIRCUIT_BREAKER_THRESHOLD
        assert client._rig_health["state"] == "open"
        assert client._circuit_breaker_is_open() is True

    def test_open_breaker_short_circuits_refresh(self):
        client = _make_client(remote_ok=True)
        for _ in range(client.CIRCUIT_BREAKER_THRESHOLD):
            client._circuit_breaker_record_failure()
        assert client._rig_health["state"] == "open"

        real_refresh = AIClient.refresh  # unbound, bypass the instance MagicMock
        with patch("devin.ai.client.requests.get") as mock_get:
            real_refresh(client)
        mock_get.assert_not_called()
        assert client.remote_coder_ok is False
        assert client.remote_reasoning_ok is False

    def test_breaker_half_open_after_cooldown(self):
        client = _make_client(remote_ok=True)
        for _ in range(client.CIRCUIT_BREAKER_THRESHOLD):
            client._circuit_breaker_record_failure()
        assert client._circuit_breaker_is_open() is True

        future = client._rig_health["last_fail"] + client.CIRCUIT_BREAKER_COOLDOWN + 1
        with patch("devin.ai.client.time.time", return_value=future):
            assert client._circuit_breaker_is_open() is False
        assert client._rig_health["state"] == "half-open"
        assert client._rig_health["failures"] == 0

    def test_failure_in_half_open_reopens_immediately(self):
        client = _make_client(remote_ok=True)
        client._rig_health["state"] = "half-open"
        client._circuit_breaker_record_failure()
        assert client._rig_health["state"] == "open"

    def test_local_failures_do_not_touch_breaker(self):
        """Breaker tracks the RIG only; local-endpoint failures must not count."""
        client = _make_client(remote_ok=False)
        with patch("devin.ai.client.requests.post",
                   side_effect=requests.exceptions.ConnectionError("local dead")), \
             patch("devin.ai.client.time.sleep"):
            assert client.local(MESSAGES) is None
        assert client._rig_health["failures"] == 0
        assert client._rig_health["state"] == "closed"

    def test_success_resets_breaker(self):
        client = _make_client(remote_ok=True)
        client._rig_health["failures"] = 2
        with patch("devin.ai.client.requests.post", return_value=_ok_response("fine")), \
             patch("devin.ai.client.time.sleep"):
            assert client.local(MESSAGES) == "fine"
        assert client._rig_health["failures"] == 0
        assert client._rig_health["state"] == "closed"


# ------------------------------------------------------------------ stream()

def _stream_response(status=200, lines=(), text=""):
    r = MagicMock()
    r.status_code = status
    r.text = text
    r.raise_for_status.return_value = None
    r.iter_lines.return_value = iter(lines)
    ctx = MagicMock()
    ctx.__enter__.return_value = r
    ctx.__exit__.return_value = False
    return ctx


class TestStreamRetry:
    def test_4xx_yields_notice_without_retry(self):
        client = _make_client()
        ctx = _stream_response(status=400, text="exceed context window")
        with patch("devin.ai.client.requests.post", return_value=ctx) as mock_post, \
             patch("devin.ai.client.time.sleep") as mock_sleep:
            out = list(client.stream(MESSAGES))
        assert mock_post.call_count == 1
        mock_sleep.assert_not_called()
        assert len(out) == 1
        assert "HTTP 400" in out[0]
        assert "Contesto troppo lungo" in out[0]

    def test_stream_yields_tokens(self):
        client = _make_client()
        lines = [
            b'data: {"choices": [{"delta": {"content": "Hello"}}]}',
            b'data: {"choices": [{"delta": {"content": " world"}}]}',
            b'data: [DONE]',
        ]
        with patch("devin.ai.client.requests.post", return_value=_stream_response(lines=lines)), \
             patch("devin.ai.client.time.sleep"):
            out = list(client.stream(MESSAGES))
        assert out == ["Hello", " world"]

    def test_stream_exhausted_retries_yield_error_notice(self):
        client = _make_client()
        with patch("devin.ai.client.requests.post",
                   side_effect=requests.exceptions.ConnectionError("down")) as mock_post, \
             patch("devin.ai.client.time.sleep") as mock_sleep:
            out = list(client.stream(MESSAGES))
        assert mock_post.call_count == client.MAX_RETRIES
        assert mock_sleep.call_args_list == [call(2), call(4)]
        assert len(out) == 1
        assert "Stream error after 3 attempts" in out[0]
