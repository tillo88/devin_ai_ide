"""Registro multi-server MCP: configurazione, discovery e budget globale.

Contesto: `docs/devin_mcp-integration-notes_v1.md`.

Il registro e' l'unico punto da cui DEVIN parla MCP. Tiene:

- l'elenco dei server dichiarati in `settings.json` (per-macchina, per-ruolo);
- l'interruttore generale (`mcp.enabled`, **default false**);
- un budget di chiamate valido per l'intero run, oltre a quelli per-server;
- l'audit di tutto cio' che e' stato invocato.

Default deliberatamente chiuso: senza configurazione esplicita **non esiste
nessun server e non si chiama niente**. Aggiungere un server MCP allarga la
superficie di prompt-injection del loop, quindi e' una decisione dell'operatore,
non un comportamento implicito.

Configurazione attesa in `settings.json`:

```json
"mcp": {
  "enabled": false,
  "max_calls_per_run": 10,
  "servers": [
    {"name": "sandbox", "url": "http://127.0.0.1:8090", "enabled": true,
     "allowed_tools": ["run_python"], "timeout_seconds": 30, "max_calls": 5}
  ]
}
```

Nota sui server **stdio**: la maggior parte dei server MCP pubblici parla stdio,
non HTTP. La scelta registrata nelle note e' di NON implementare stdio dentro
DEVIN ma di metterci davanti un bridge (`mcpo`/`supergateway`) che li espone in
HTTP: il registro li vede come normali server HTTP e non deve gestire
sottoprocessi, pipe e cicli di vita.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from devin.ai.mcp_client import (
    CALL_BUDGET,
    CALL_DENIED,
    MCPClient,
    MCPError,
    MCPServerConfig,
    MCPToolResult,
)


class MCPRegistry:
    """Punto unico d'accesso ai server MCP configurati."""

    def __init__(self, config: Optional[Dict[str, Any]] = None, *, client_factory=None):
        cfg = ((config or {}).get("mcp") or {})
        self.enabled = bool(cfg.get("enabled", False))
        self.max_calls_per_run = cfg.get("max_calls_per_run")
        self.max_calls_per_run = (
            int(self.max_calls_per_run) if self.max_calls_per_run is not None else None
        )
        self.calls_made = 0
        self._client_factory = client_factory or (lambda c: MCPClient(c))
        self._clients: Dict[str, MCPClient] = {}
        self.servers: List[MCPServerConfig] = []
        self.config_errors: List[Dict[str, str]] = []

        for entry in cfg.get("servers", []) or []:
            try:
                server = MCPServerConfig.from_dict(entry)
            except MCPError as exc:
                # una riga di config sbagliata non deve impedire le altre
                self.config_errors.append({"entry": str(entry)[:200], "error": str(exc)})
                continue
            self.servers.append(server)

    # --- introspezione ----------------------------------------------------
    def server_names(self) -> List[str]:
        return [s.name for s in self.servers]

    def get_server(self, name: str) -> Optional[MCPServerConfig]:
        for server in self.servers:
            if server.name == name:
                return server
        return None

    def client(self, name: str) -> MCPClient:
        server = self.get_server(name)
        if server is None:
            raise MCPError(f"server MCP sconosciuto: {name!r}")
        if name not in self._clients:
            self._clients[name] = self._client_factory(server)
        return self._clients[name]

    def discover(self) -> Dict[str, Any]:
        """Elenca i tool di ogni server attivo, annotando quali sono autorizzati.

        Fail-soft: un server irraggiungibile compare con il suo errore, non fa
        fallire la discovery degli altri.
        """
        report: Dict[str, Any] = {"enabled": self.enabled, "servers": {}}
        if not self.enabled:
            report["note"] = "MCP disabilitato in configurazione: nessuna scoperta eseguita"
            return report
        for server in self.servers:
            if not server.enabled:
                report["servers"][server.name] = {"status": "disabled", "tools": []}
                continue
            try:
                tools = self.client(server.name).available_tools()
            except Exception as exc:
                report["servers"][server.name] = {"status": "error", "error": str(exc)[:300],
                                                  "tools": []}
                continue
            report["servers"][server.name] = {
                "status": "ok",
                "tools": [{"name": t.get("name"), "description": (t.get("description") or "")[:200],
                           "allowed": t.get("allowed", False)} for t in tools],
                "allowed_count": sum(1 for t in tools if t.get("allowed")),
            }
        return report

    # --- invocazione ------------------------------------------------------
    def call(self, server: str, tool: str,
             arguments: Optional[Dict[str, Any]] = None) -> MCPToolResult:
        """Invoca un tool su un server. Non solleva: ritorna sempre un esito."""
        if not self.enabled:
            return MCPToolResult(server, tool, CALL_DENIED,
                                 detail="MCP disabilitato in configurazione")
        if self.max_calls_per_run is not None and self.calls_made >= self.max_calls_per_run:
            return MCPToolResult(server, tool, CALL_BUDGET,
                                 detail=f"budget globale di {self.max_calls_per_run} chiamate esaurito")
        try:
            client = self.client(server)
        except MCPError as exc:
            return MCPToolResult(server, tool, CALL_DENIED, detail=str(exc))
        result = client.call_tool(tool, arguments)
        if result.status != CALL_DENIED:
            # le chiamate negate non consumano budget: non hanno toccato la rete
            self.calls_made += 1
        return result

    def audit(self) -> List[Dict[str, Any]]:
        """Tutto cio' che e' stato invocato, in ordine, con esito."""
        rows: List[Dict[str, Any]] = []
        for name, client in self._clients.items():
            rows.extend(client.audit)
        return rows

    def close(self) -> None:
        for client in self._clients.values():
            try:
                client.close()
            except Exception:
                pass
        self._clients.clear()


def registry_from_settings(config_path: Optional[str] = None,
                           config: Optional[Dict[str, Any]] = None) -> MCPRegistry:
    """Costruisce il registro dal `settings.json` (o da un dict gia' caricato)."""
    if config is None:
        import json
        if config_path is None:
            from devin.ui.fast_app import CONFIG_PATH  # lazy
            config_path = CONFIG_PATH
        try:
            with open(config_path, "r", encoding="utf-8") as handle:
                config = json.load(handle) or {}
        except Exception:
            config = {}
    return MCPRegistry(config)
