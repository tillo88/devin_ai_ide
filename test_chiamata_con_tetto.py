"""31/10: una chiamata al modello non resta aperta oltre lo stop, il tempo del caso o il suo tetto.

Il 30/10 una chiamata con strumenti e' rimasta aperta dalle 20:29 alle 23:06: il timeout di
requests vale fra un byte e l'altro, e tetto del caso e stop si guardavano solo fra un turno e
l'altro. Qui un vero server HTTP locale che tace (o gocciola) dopo le intestazioni, e un vero
socket: il guardiano deve sbloccare la lettura, non solo "accorgersene dopo".
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socket
import threading
import time

import pytest

from devin.ai import client as modulo
from devin.ai.client import AIClient, ChiamataInterrotta, _Guardiano

PEZZO = b'data: {"choices":[{"delta":{"reasoning_content":"penso"}}]}\n\n'
AVANZAMENTO = b'data: {"choices":[{"delta":{}}],"prompt_progress":{"processed":1}}\n\n'


def _chiusa_dall_altra_parte(conn):
    try:
        return conn.recv(1, socket.MSG_PEEK | socket.MSG_DONTWAIT) == b""
    except BlockingIOError:
        return False


class _Server:
    """modo: 'tace' (un pezzo e poi silenzio), 'gocciola' (un avanzamento ogni 0,1 s), 'finisce'."""

    def __init__(self, modo):
        self.modo, self.post, self.chiuse = modo, 0, threading.Event()
        server = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                server.post += 1
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                try:
                    self.wfile.write(PEZZO)
                    self.wfile.flush()
                    if server.modo == "finisce":
                        self.wfile.write(b'data: {"choices":[{"delta":{"content":"fatto"},"finish_reason":"stop"}]}\n\n')
                        self.wfile.write(b"data: [DONE]\n\n")
                        self.wfile.flush()
                        return
                    fine = time.monotonic() + 20
                    while time.monotonic() < fine:
                        time.sleep(0.1)
                        if server.modo == "gocciola":
                            self.wfile.write(AVANZAMENTO)
                            self.wfile.flush()
                        elif _chiusa_dall_altra_parte(self.connection):
                            break
                except OSError:
                    pass
                finally:
                    server.chiuse.set()

            def log_message(self, *a):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_port}/v1/chat/completions"

    def spegni(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server(request):
    s = _Server(request.param)
    yield s
    s.spegni()


def _client(monkeypatch, tmp_path, url):
    monkeypatch.setenv("DEVIN_RICHIESTE_DIR", str(tmp_path / "richieste"))
    monkeypatch.setattr(modulo.time, "sleep", lambda s: None)
    c = AIClient.__new__(AIClient)
    c.config = {"models": {"reasoning": {"timeouts": {"low": 30, "medium": 30, "xhigh": 30}}}}
    c.remote_coder_url = c.remote_reasoning_url = url
    c.remote_coder_ok = c.remote_reasoning_ok = True
    c.remote_base_url, c.remote_host = url.rsplit("/v1/", 1)[0], "127.0.0.1"
    c.remote_model_actual, c.allow_local_fallback, c.rig_required = "m", False, True
    c.rig_api_key, c.circuit_breaker_enabled, c.use_openai, c.openai = "", True, False, None
    c._rig_health = {"failures": 0, "last_fail": 0, "state": "closed"}
    c.refresh = lambda **kw: None
    c.ultimo_ragionamento = ""
    c._con_catalogo_se_serve = lambda messages, tools, url: messages
    return c


def _dopo(secondi):
    limite = time.monotonic() + secondi
    return lambda: time.monotonic() >= limite


@pytest.mark.parametrize("server", ["tace"], indirect=True)
def test_lo_stop_sblocca_una_chiamata_ferma(monkeypatch, tmp_path, server):
    c = _client(monkeypatch, tmp_path, server.url)
    inizio = time.monotonic()
    with pytest.raises(ChiamataInterrotta) as preso:
        c.chiama_con_strumenti([{"role": "user", "content": "x"}], [], interrompi=_dopo(0.5))
    assert preso.value.motivo == "stop"
    assert time.monotonic() - inizio < 5, "il server tace per 20 s: e' il guardiano ad aver chiuso"
    assert server.post == 1, "uno stop non si ritenta"
    assert server.chiuse.wait(5), "la connessione e' chiusa davvero: e' cosi' che llama-server smette"


@pytest.mark.parametrize("server", ["tace"], indirect=True)
def test_lo_stop_del_client_vale_senza_passarlo(monkeypatch, tmp_path, server):
    """L'orchestratore mette il suo stop (che comprende il tempo del caso) in ai_client.interrompi."""
    c = _client(monkeypatch, tmp_path, server.url)
    c.interrompi = _dopo(0.5)
    with pytest.raises(ChiamataInterrotta):
        c.chiama_con_strumenti([{"role": "user", "content": "x"}], [])


@pytest.mark.parametrize("server", ["gocciola"], indirect=True)
def test_il_tetto_chiude_uno_stream_che_gocciola(monkeypatch, tmp_path, server):
    """Il server manda un avanzamento ogni 0,1 s: il timeout fra i byte non scade mai."""
    c = _client(monkeypatch, tmp_path, server.url)
    c._tetto_chiamata = lambda timeout, max_tokens=None: 1.0
    inizio = time.monotonic()
    with pytest.raises(ChiamataInterrotta) as preso:
        c.chiama_con_strumenti([{"role": "user", "content": "x"}], [])
    assert preso.value.motivo == "tetto" and time.monotonic() - inizio < 5 and server.post == 1


@pytest.mark.parametrize("server", ["tace"], indirect=True)
def test_local_si_ferma_e_lo_dice(monkeypatch, tmp_path, server):
    c = _client(monkeypatch, tmp_path, server.url)
    c.interrompi = _dopo(0.5)
    inizio = time.monotonic()
    assert c.local([{"role": "user", "content": "x"}]) is None
    assert c.ultima_fine == "interrotta" and time.monotonic() - inizio < 5 and server.post == 1


@pytest.mark.parametrize("server", ["finisce"], indirect=True)
def test_una_chiamata_normale_non_cambia(monkeypatch, tmp_path, server):
    c = _client(monkeypatch, tmp_path, server.url)
    c.interrompi = lambda: False
    esito = c.chiama_con_strumenti([{"role": "user", "content": "x"}], [])
    assert esito["content"] == "fatto" and esito["reasoning"] == "penso"
    assert c.local([{"role": "user", "content": "x"}]) == "fatto"


@pytest.mark.parametrize("server", ["finisce"], indirect=True)
def test_a_stop_gia_chiesto_non_si_manda_niente(monkeypatch, tmp_path, server):
    c = _client(monkeypatch, tmp_path, server.url)
    with pytest.raises(ChiamataInterrotta):
        c.chiama_con_strumenti([{"role": "user", "content": "x"}], [], interrompi=lambda: True)
    c.interrompi = lambda: True
    assert c.local([{"role": "user", "content": "x"}]) is None
    assert server.post == 0


def test_il_tetto_e_largo_per_il_lavoro_vero():
    """Turno dell'agente del 30/10: timeout 229 s, 8192 token. Misurati 8,8 token/s."""
    tetto = AIClient._tetto_chiamata(229, 8192)
    assert tetto == pytest.approx(229 + 8192 / 3)
    assert 229 + 8192 / 8.8 < tetto < 60 * 60, "piu' del doppio del lavoro vero, meno di un'ora"
    assert AIClient._tetto_chiamata(2400) > 1728, "il task xhigh misurato (1728 s) ci sta"


def test_un_errore_nello_stop_non_ferma_la_chiamata():
    def guasto():
        raise RuntimeError("x")
    assert _Guardiano.motivo_ora(guasto, time.monotonic() + 60) == ""
    assert _Guardiano.motivo_ora(None, time.monotonic() - 1) == "tetto"


def test_l_agente_fermato_a_meta_chiamata_e_uno_stop(tmp_path):
    from devin.agents.tool_agent import AgenteStrumenti

    class Ai:
        def chiama_con_strumenti(self, *a, **k):
            raise ChiamataInterrotta("stop", 12.0)

    log = []
    esito = AgenteStrumenti(Ai(), tmp_path, log=lambda m, l="info": log.append(m)).esegui("compito")
    assert esito.motivo == "stop" and "12.0 s" in esito.errore


def test_l_orchestratore_passa_il_suo_stop_al_client():
    from devin.core.orchestrator import Orchestrator
    import inspect
    sorgente = inspect.getsource(Orchestrator.__init__)
    assert "self.ai_client.interrompi = lambda: self._should_stop" in sorgente
    orch = Orchestrator.__new__(Orchestrator)
    orch._log = lambda *a, **k: None
    orch.ai_client = AIClient.__new__(AIClient)
    orch.ai_client.interrompi = lambda: orch._should_stop
    assert orch.ai_client.interrompi() is False
    orch.scadenza_caso = time.time() - 1
    assert orch.ai_client.interrompi() is True, "il tempo del caso arriva alla chiamata in corso"
