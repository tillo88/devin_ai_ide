"""Client fail-soft per Understory con fallback semantico locale.

Understory resta un servizio esterno (MCP Streamable HTTP). Quando il bundle
condiviso e' montato localmente, il recall automatico usa VectorStore e non
consuma un secondo giro LLM; le mutazioni passano invece da MCP.
"""

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import requests


class UnderstoryClient:
    def __init__(self, config: dict):
        cfg = (config or {}).get("understory", {})
        self.enabled = bool(cfg.get("enabled", False))
        self.base_url = (cfg.get("url") or "http://127.0.0.1:3810").rstrip("/")
        self.timeout = float(cfg.get("timeout_seconds", 15))
        self.agentic_recall = bool(cfg.get("agentic_recall", True))
        self.agent_id = str(cfg.get("agent_id") or "devin").strip().lower()
        self.shared_statuses = {
            "verified_success", "verified_failure", "human_confirmed"
        }
        raw_bundle = str(cfg.get("bundle_path") or "").strip()
        self.bundle_path = Path(raw_bundle).expanduser() if raw_bundle else None
        self._vector_store = None
        self._bundle_signature = None
        self._request_id = 0

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    @staticmethod
    def _decode_response(response) -> Dict:
        if not response.content:
            return {}
        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type:
            data = response.json()
            return data if isinstance(data, dict) else {}
        # MCP puo' rispondere come SSE: usa l'ultimo evento JSON completo.
        decoded = {}
        for line in response.text.splitlines():
            if line.startswith("data:"):
                try:
                    item = json.loads(line[5:].strip())
                    if isinstance(item, dict):
                        decoded = item
                except json.JSONDecodeError:
                    continue
        return decoded

    def _post(self, session: requests.Session, payload: Dict,
              session_id: Optional[str] = None):
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        if session_id:
            headers["Mcp-Session-Id"] = session_id
        response = session.post(
            f"{self.base_url}/mcp", json=payload, headers=headers,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response, self._decode_response(response)

    def _call_tool(self, name: str, arguments: Dict) -> str:
        with requests.Session() as session:
            init_id = self._next_id()
            response, initialized = self._post(session, {
                "jsonrpc": "2.0", "id": init_id, "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "devin-ai-ide", "version": "1.0"},
                },
            })
            if initialized.get("error"):
                raise RuntimeError(str(initialized["error"]))
            session_id = response.headers.get("Mcp-Session-Id")
            self._post(session, {
                "jsonrpc": "2.0", "method": "notifications/initialized",
            }, session_id)
            call_id = self._next_id()
            _, result = self._post(session, {
                "jsonrpc": "2.0", "id": call_id, "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }, session_id)
            if result.get("error"):
                raise RuntimeError(str(result["error"]))
            blocks = result.get("result", {}).get("content", [])
            return "\n".join(
                str(block.get("text", "")) for block in blocks
                if isinstance(block, dict) and block.get("type") == "text"
            ).strip()

    def _semantic_recall(self, query: str, limit: int) -> List[str]:
        root = self.bundle_path
        if not root or not root.is_dir():
            return []
        paths = sorted(
            p for p in root.rglob("*.md")
            if ".git" not in p.parts and ".traces" not in p.parts
            and not {"raw", "quarantine", "private"}.intersection(p.parts)
            and p.name != "log.md"
        )
        signature = tuple((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in paths)
        if self._vector_store is None or signature != self._bundle_signature:
            from devin.memory.vector_store import VectorStore
            self._vector_store = VectorStore()
            files = [{"path": str(p), "content": p.read_text(encoding="utf-8", errors="replace")}
                     for p in paths]
            self._vector_store.index_project(str(root), files,
                                             cache_path=root / ".devin_cache" / "semantic_index.json")
            self._bundle_signature = signature
        hits = self._vector_store.search_semantic(query, project_path=str(root), top_k=limit)
        return [f"[{Path(hit['metadata']['path']).relative_to(root)}] {hit['text']}"
                for hit in hits if hit.get("score", 0) > 0.15]

    def recall(self, query: str, tags: Optional[List[str]] = None,
               limit: int = 3) -> List[str]:
        if not self.enabled or not query.strip():
            return []
        try:
            local = self._semantic_recall(query, limit)
            if local:
                return local
            if not self.agentic_recall:
                return []
            scope = f"\nTask/project hints: {', '.join(tags)}" if tags else ""
            policy = (
                "\nFederated recall policy: search verified shared knowledge from ALL agents "
                "(DEVIN, TEACHER and HERMES), not only the requesting agent. Exclude raw, "
                "quarantine, pending, inconclusive, revoked and superseded memories. "
                "Include source agent, status, polarity and provenance in the answer."
            )
            answer = self._call_tool("memory_query", {"question": query + scope + policy})
            return [answer] if answer else []
        except Exception as exc:
            print(f"[Understory] Recall non disponibile: {exc}")
            return []

    def store(self, content: str, tags: Optional[List[str]] = None,
              importance: float = 0.5, queue_if_offline: bool = True) -> str:
        del importance, queue_if_offline
        if not self.enabled or not content.strip():
            return "failed"
        try:
            labels = tags or []
            status = next((t.split(":", 1)[1] for t in labels
                           if t.startswith("status:") and ":" in t), "pending_review")
            domain = next((t.split(":", 1)[1] for t in labels
                           if t.startswith("domain:") and ":" in t), "general")
            source = next((t.split(":", 1)[1] for t in labels
                           if t.startswith("source:") and ":" in t), self.agent_id)
            polarity = next((t.split(":", 1)[1] for t in labels
                             if t.startswith("polarity:") and ":" in t),
                            "negative" if status == "verified_failure" else "positive")
            safe_domain = re.sub(r"[^a-z0-9_-]+", "-", domain.lower()).strip("-") or "general"
            safe_source = re.sub(r"[^a-z0-9_-]+", "-", source.lower()).strip("-") or self.agent_id
            memory_id = f"mem-{uuid.uuid4().hex}"
            created_at = datetime.now(timezone.utc).isoformat()
            confidence = next((t.split(":", 1)[1] for t in labels
                               if t.startswith("confidence:") and ":" in t),
                              "1.0" if status == "human_confirmed" else "0.5")
            evidence = next((t.split(":", 1)[1] for t in labels
                             if t.startswith("evidence:") and ":" in t),
                            "explicit_user_save" if status == "human_confirmed" else "unverified_client_record")
            prefix = (
                "Federated memory metadata:\n"
                f"- memory_id: {memory_id}\n- source_agent: {safe_source}\n"
                f"- domain: {safe_domain}\n"
                f"- status: {status}\n- polarity: {polarity}\n"
                f"- created_at: {created_at}\n- evidence: {evidence}\n"
                f"- confidence: {confidence}\n- provenance: explicit client publication\n"
                f"Source labels: {', '.join(labels)}\n"
                "Keep this knowledge inside its stated project/source scope. "
                "Do not create links to concepts that are merely present in the bundle; "
                "link only when the content itself establishes a real relationship.\n\n"
            )
            project = next((t.split(":", 1)[1] for t in labels
                            if t.startswith("project:") and ":" in t), "")
            slug = re.sub(r"[^a-z0-9_-]+", "-", project.lower()).strip("-")
            arguments = {"content": prefix + content.strip()[:8000]}
            item_slug = slug or "general"
            if status in self.shared_statuses:
                arguments["suggested_path"] = f"/shared/{safe_domain}/{safe_source}-{item_slug}.md"
            else:
                arguments["suggested_path"] = f"/agents/{safe_source}/quarantine/{item_slug}.md"
            self._call_tool("memory_add", arguments)
            self._bundle_signature = None
            return "stored"
        except Exception as exc:
            print(f"[Understory] Store non disponibile: {exc}")
            return "failed"

    def status(self) -> Dict:
        if not self.enabled:
            return {"enabled": False, "reachable": False, "backend": "understory"}
        try:
            response = requests.get(f"{self.base_url}/health", timeout=min(self.timeout, 3))
            return {"enabled": True, "reachable": response.status_code == 200,
                    "backend": "understory", "bundle_local": bool(
                        self.bundle_path and self.bundle_path.is_dir())}
        except Exception:
            return {"enabled": True, "reachable": False, "backend": "understory",
                    "bundle_local": bool(self.bundle_path and self.bundle_path.is_dir())}
