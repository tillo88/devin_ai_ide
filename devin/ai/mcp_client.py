"""Client MCP di DEVIN: i server MCP diventano strumenti dell'agente.

Due trasporti:
- ``http``: Streamable HTTP (POST JSON-RPC, risposta JSON o SSE, sessione con
  ``Mcp-Session-Id``), es. il server GitHub ospitato da GitHub;
- ``stdio``: un processo locale che parla JSON-RPC una riga per messaggio
  (Blender, Godot, filesystem...).

Il registro sta in ``config/mcp_servers.json`` (tracciato: niente segreti).
Un valore ``@file:PERCORSO`` in un header o in una variabile d'ambiente si
legge da quel file al momento della connessione: il token resta sul rig, in un
file che solo l'utente del backend legge, e non passa mai per il modello, i log
o la chat. Un server senza il suo file non si accende e lo dice.

Deny by default: un server espone all'agente solo gli strumenti elencati in
``tools`` (o tutti con ``"*"``), al massimo ``MAX_STRUMENTI`` in tutto.
I risultati degli strumenti sono dati non fidati e vengono tagliati.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import select
import subprocess
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

PROTOCOLLO = "2025-03-26"
MAX_RISULTATO = 8000
MAX_STRUMENTI = 48
TIMEOUT = 60
NOME_VALIDO = re.compile(r"[^A-Za-z0-9_-]")
REGISTRO_DEFAULT = Path(__file__).resolve().parents[2] / "config" / "mcp_servers.json"


class ErroreMCP(RuntimeError):
    pass


def risolvi_valore(valore: Any) -> str:
    """``@file:~/x`` -> contenuto del file (senza spazi ai bordi); altrimenti il valore.

    Dentro una stringa piu' lunga ("Bearer @file:~/t") si sostituisce solo il riferimento.
    """
    testo = str(valore)
    match = re.search(r"@file:(\S+)", testo)
    if not match:
        return testo
    percorso = Path(os.path.expanduser(match.group(1)))
    try:
        segreto = percorso.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ErroreMCP(f"manca il file {match.group(1)}") from exc
    if not segreto:
        raise ErroreMCP(f"il file {match.group(1)} e' vuoto")
    return testo[:match.start()] + segreto + testo[match.end():]


def _dalla_risposta_sse(testo: str) -> List[Dict[str, Any]]:
    messaggi = []
    for riga in testo.splitlines():
        if riga.startswith("data:"):
            dati = riga[5:].strip()
            if dati:
                try:
                    messaggi.append(json.loads(dati))
                except ValueError:
                    continue
    return messaggi


def testo_del_risultato(risultato: Dict[str, Any]) -> str:
    """I blocchi di contenuto di tools/call in testo, tagliato."""
    parti = []
    for blocco in risultato.get("content") or []:
        if not isinstance(blocco, dict):
            continue
        if blocco.get("type") == "text":
            parti.append(str(blocco.get("text") or ""))
        elif blocco.get("type") == "image":
            parti.append(f"[immagine {blocco.get('mimeType', '')}: non mostrata]")
        elif blocco.get("type") == "resource":
            risorsa = blocco.get("resource") or {}
            parti.append(str(risorsa.get("text") or f"[risorsa {risorsa.get('uri', '')}]"))
    if not parti and risultato.get("structuredContent") is not None:
        parti.append(json.dumps(risultato["structuredContent"], ensure_ascii=False))
    testo = "\n".join(parti) or "(nessun contenuto)"
    if risultato.get("isError"):
        testo = "ERRORE dallo strumento: " + testo
    if len(testo) > MAX_RISULTATO:
        testo = testo[:MAX_RISULTATO] + f"\n... (tagliato: {len(testo)} caratteri)"
    return testo


class ServerMCP:
    """Una connessione a un server MCP (http o stdio)."""

    def __init__(self, nome: str, config: Dict[str, Any]):
        self.nome = nome
        self.config = config
        self.trasporto = config.get("transport", "http")
        self._id = 0
        self._sessione: Optional[requests.Session] = None
        self._session_id: Optional[str] = None
        self._processo: Optional[subprocess.Popen] = None
        self._buffer = ""
        self._lock = threading.Lock()

    # --- trasporto ----------------------------------------------------------------

    def _prossimo_id(self) -> int:
        self._id += 1
        return self._id

    def _invia_http(self, messaggio: Dict[str, Any], attende: bool) -> Optional[Dict[str, Any]]:
        headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
        for chiave, valore in (self.config.get("headers") or {}).items():
            headers[chiave] = risolvi_valore(valore)
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        risposta = self._sessione.post(self.config["url"], json=messaggio, headers=headers,
                                       timeout=float(self.config.get("timeout", TIMEOUT)))
        if risposta.status_code in (401, 403):
            raise ErroreMCP(f"accesso negato (HTTP {risposta.status_code}): token mancante, scaduto o senza permessi")
        if risposta.status_code >= 400:
            raise ErroreMCP(f"HTTP {risposta.status_code}: {(risposta.text or '')[:300]}")
        if risposta.headers.get("Mcp-Session-Id"):
            self._session_id = risposta.headers["Mcp-Session-Id"]
        if not attende:
            return None
        tipo = risposta.headers.get("content-type", "")
        messaggi = (_dalla_risposta_sse(risposta.text) if "text/event-stream" in tipo
                    else [risposta.json()] if (risposta.text or "").strip() else [])
        for m in messaggi:
            if m.get("id") == messaggio.get("id"):
                return m
        raise ErroreMCP("risposta senza l'id della richiesta")

    def _invia_stdio(self, messaggio: Dict[str, Any], attende: bool) -> Optional[Dict[str, Any]]:
        if self._processo is None or self._processo.poll() is not None:
            raise ErroreMCP("il processo del server non e' in esecuzione")
        self._processo.stdin.write(json.dumps(messaggio) + "\n")
        self._processo.stdin.flush()
        if not attende:
            return None
        limite = time.time() + float(self.config.get("timeout", TIMEOUT))
        while time.time() < limite:
            while "\n" in self._buffer:
                riga, self._buffer = self._buffer.split("\n", 1)
                if not riga.strip():
                    continue
                try:
                    m = json.loads(riga)
                except ValueError:
                    continue          # rumore su stdout: non e' protocollo
                if m.get("id") == messaggio.get("id"):
                    return m
            pronti, _, _ = select.select([self._processo.stdout], [], [], 0.5)
            if pronti:
                pezzo = os.read(self._processo.stdout.fileno(), 65536).decode("utf-8", errors="replace")
                if not pezzo:
                    raise ErroreMCP("il server ha chiuso lo stdout")
                self._buffer += pezzo
        raise ErroreMCP(f"nessuna risposta in {self.config.get('timeout', TIMEOUT)} s")

    def _richiesta(self, metodo: str, parametri: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        messaggio = {"jsonrpc": "2.0", "id": self._prossimo_id(), "method": metodo}
        if parametri is not None:
            messaggio["params"] = parametri
        with self._lock:
            invia = self._invia_http if self.trasporto == "http" else self._invia_stdio
            risposta = invia(messaggio, True) or {}
        if risposta.get("error"):
            errore = risposta["error"]
            raise ErroreMCP(f"{metodo}: {errore.get('message', errore) if isinstance(errore, dict) else errore}")
        return risposta.get("result") or {}

    def _notifica(self, metodo: str) -> None:
        with self._lock:
            invia = self._invia_http if self.trasporto == "http" else self._invia_stdio
            invia({"jsonrpc": "2.0", "method": metodo}, False)

    # --- ciclo di vita ------------------------------------------------------------

    def avvia(self) -> None:
        if self.trasporto == "http":
            self._sessione = requests.Session()
        elif self.trasporto == "stdio":
            comando = self.config.get("command")
            if not isinstance(comando, list) or not comando:
                raise ErroreMCP("'command' deve essere una lista, es. [\"python\", \"server.py\"]")
            env = dict(os.environ)
            for chiave, valore in (self.config.get("env") or {}).items():
                env[chiave] = risolvi_valore(valore)
            try:
                self._processo = subprocess.Popen(
                    [os.path.expanduser(str(a)) for a in comando], stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=env,
                    cwd=os.path.expanduser(str(self.config.get("cwd") or "~")), start_new_session=True)
            except FileNotFoundError as exc:
                raise ErroreMCP(f"programma non trovato: {comando[0]}") from exc
        else:
            raise ErroreMCP(f"trasporto sconosciuto: {self.trasporto}")
        self._richiesta("initialize", {"protocolVersion": PROTOCOLLO, "capabilities": {},
                                       "clientInfo": {"name": "devin-ai-ide", "version": "1.0"}})
        self._notifica("notifications/initialized")

    def strumenti(self) -> List[Dict[str, Any]]:
        elenco, cursore = [], None
        for _ in range(20):
            risultato = self._richiesta("tools/list", {"cursor": cursore} if cursore else {})
            elenco.extend(t for t in risultato.get("tools") or [] if isinstance(t, dict) and t.get("name"))
            cursore = risultato.get("nextCursor")
            if not cursore:
                break
        return elenco

    def chiama(self, nome: str, argomenti: Dict[str, Any]) -> str:
        return testo_del_risultato(self._richiesta("tools/call", {"name": nome, "arguments": argomenti}))

    def chiudi(self) -> None:
        if self._sessione is not None:
            self._sessione.close()
            self._sessione = None
        if self._processo is not None and self._processo.poll() is None:
            self._processo.terminate()       # SIGTERM; mai SIGKILL
            try:
                self._processo.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
        self._processo = None


def carica_registro(percorso: Path | str | None = None) -> Dict[str, Dict[str, Any]]:
    path = Path(percorso or REGISTRO_DEFAULT)
    try:
        dati = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except ValueError as exc:
        raise ErroreMCP(f"{path.name} non e' JSON valido: {exc}") from exc
    servers = dati.get("servers") if isinstance(dati, dict) else None
    if not isinstance(servers, dict):
        raise ErroreMCP(f"{path.name}: manca l'oggetto 'servers'")
    return {str(k): v for k, v in servers.items() if isinstance(v, dict)}


def stato_registro(percorso: Path | str | None = None) -> List[Dict[str, Any]]:
    """Stato dichiarato dei server, senza connettersi (per la UI)."""
    righe = []
    for nome, config in carica_registro(percorso).items():
        riga = {"name": nome, "transport": config.get("transport", "http"),
                "enabled": bool(config.get("enabled", False)), "tools": config.get("tools", []),
                "description": str(config.get("description") or "")[:200]}
        if riga["enabled"]:
            try:
                for valore in [*(config.get("headers") or {}).values(), *(config.get("env") or {}).values()]:
                    risolvi_valore(valore)
                riga["status"] = "pronto"
            except ErroreMCP as exc:
                riga["status"] = f"non pronto: {exc}"
        else:
            riga["status"] = "spento"
        righe.append(riga)
    return righe


class RegistroMCP:
    """I server accesi per un run, con i loro strumenti nel formato tools dell'API."""

    def __init__(self, percorso: Path | str | None = None,
                 log: Callable[[str, str], None] = lambda m, l="info": None):
        self.percorso = percorso
        self.log = log
        self.server: Dict[str, ServerMCP] = {}
        self.mappa: Dict[str, Tuple[str, str]] = {}
        self.strumenti: List[Dict[str, Any]] = []

    def connetti(self) -> List[Dict[str, Any]]:
        try:
            registro = carica_registro(self.percorso)
        except ErroreMCP as exc:
            self.log(f"MCP: registro non leggibile ({exc})", "warning")
            return []
        strumenti: List[Dict[str, Any]] = []
        for nome, config in registro.items():
            if not config.get("enabled"):
                continue
            try:
                for valore in [*(config.get("headers") or {}).values(), *(config.get("env") or {}).values()]:
                    risolvi_valore(valore)
            except ErroreMCP as exc:
                # non ancora configurato (es. token non messo): si vede nello stato, non e' un guasto
                self.log(f"MCP {nome}: non configurato ({exc})", "info")
                continue
            server = ServerMCP(nome, config)
            try:
                server.avvia()
                disponibili = server.strumenti()
            except Exception as exc:
                server.chiudi()
                self.log(f"MCP {nome}: non disponibile ({exc})", "warning")
                continue
            ammessi = config.get("tools") or []
            scelti = [t for t in disponibili if ammessi == "*" or t["name"] in ammessi]
            for strumento in scelti:
                if len(strumenti) >= MAX_STRUMENTI:
                    self.log(f"MCP: raggiunti {MAX_STRUMENTI} strumenti, gli altri restano fuori", "warning")
                    break
                funzione = NOME_VALIDO.sub("_", f"{nome}__{strumento['name']}")[:64]
                schema = strumento.get("inputSchema") if isinstance(strumento.get("inputSchema"), dict) \
                    else {"type": "object", "properties": {}}
                strumenti.append({"type": "function", "function": {
                    "name": funzione,
                    "description": f"[MCP {nome}] {str(strumento.get('description') or '')[:900]}",
                    "parameters": schema}})
                self.mappa[funzione] = (nome, strumento["name"])
            self.server[nome] = server
            self.log(f"🔌 MCP {nome}: {len(scelti)} strumenti", "info")
        self.strumenti = strumenti
        return strumenti

    def gestisce(self, funzione: str) -> bool:
        return funzione in self.mappa

    def chiama(self, funzione: str, argomenti: Dict[str, Any]) -> str:
        nome, strumento = self.mappa[funzione]
        try:
            return self.server[nome].chiama(strumento, argomenti)
        except Exception as exc:
            return f"ERRORE MCP {nome}/{strumento}: {exc}"

    def __del__(self):
        # rete di sicurezza se chi lo usa sparisce prima di chiudere (es. chat interrotta)
        try:
            self.chiudi()
        except Exception:
            pass

    def chiudi(self) -> None:
        for server in self.server.values():
            server.chiudi()
        self.server.clear()
        self.mappa.clear()
        self.strumenti = []
