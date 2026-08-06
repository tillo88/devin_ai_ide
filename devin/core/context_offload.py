"""Context Steward CS4 — compattazione SIMBOLICA e deterministica (niente LLM).

Vedi `docs/CONTEXT_STEWARD_PLAN.md`. CS0-CS3 decidono **quando** compattare;
questo modulo decide **come**, senza modello.

Idea presa da TencentDB Agent Memory (canvas Mermaid + `node_id`) e adattata
agli invarianti del Context Steward, in particolare:

    "never replaces evidence with summary"

Un riassunto prodotto da un LLM **viola** quell'invariante: l'evidenza sparisce e
resta un'interpretazione. L'offload simbolico no — il payload grezzo va su disco
e resta recuperabile per `node_id`, mentre in contesto rimane solo un grafo
compatto. Si perde *ingombro*, non *informazione*.

```
    log verboso (decine di migliaia di token)
        -> payload integrale su file        (refs/<node_id>.txt)
        -> nodo nel canvas Mermaid          (poche decine di token)
        -> l'agente ragiona sul canvas
        -> serve un dettaglio? resolve(node_id) e riprende il testo esatto
```

Proprieta' volute, tutte verificate dai test:

- **deterministico**: stesso input, stesso canvas e stessi `node_id`. Nessun
  modello, nessuna GPU, nessuna rete (coerente con CS0-CS3);
- **nessuna perdita**: `resolve()` restituisce il payload byte-per-byte;
- **i fallimenti non si collassano mai**: quando il canvas e' pieno si elidono i
  nodi riusciti piu' vecchi, mai gli errori — sono l'informazione che serve;
- **elisione dichiarata**: i nodi tolti dal canvas restano contati e
  risolvibili, e il canvas lo dice. Niente sparisce in silenzio;
- **stato serializzabile** per il resume dopo crash, come `ContextSteward`.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

# tipi di nodo (il `kind` finisce nell'etichetta del canvas)
KIND_TOOL = "tool"
KIND_COMMAND = "command"
KIND_RESULT = "result"
KIND_ERROR = "error"
KIND_NOTE = "note"

STATUS_OK = "ok"
STATUS_FAIL = "fail"
STATUS_PENDING = "pending"

_LABEL_SAFE = re.compile(r'[^\w\s.:/\-+()#]', re.UNICODE)
NODE_ID_RE = re.compile(r"\bn\d{3,}\b")


def _fingerprint(*parts: Any) -> str:
    digest = hashlib.sha256("\x1f".join(str(p) for p in parts).encode("utf-8", "replace"))
    return digest.hexdigest()[:16]


def _safe_label(text: str, limit: int = 48) -> str:
    """Etichetta breve e sicura per Mermaid (le virgolette romperebbero il grafo)."""
    clean = _LABEL_SAFE.sub(" ", str(text or "").replace("\n", " ")).strip()
    clean = re.sub(r"\s+", " ", clean)
    if len(clean) > limit:
        clean = clean[: limit - 1].rstrip() + "…"
    return clean or "(senza etichetta)"


@dataclass
class OffloadNode:
    """Un pezzo di contesto scaricato su disco e rappresentato da un simbolo."""

    node_id: str
    kind: str
    label: str
    status: str
    ref: str                  # path relativo alla radice di offload
    size_bytes: int
    fingerprint: str
    parent: Optional[str] = None

    @property
    def failed(self) -> bool:
        return self.status == STATUS_FAIL

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "OffloadNode":
        return cls(**{k: data.get(k) for k in cls.__dataclass_fields__})


class ContextOffload:
    """Scarica payload verbosi su disco e mantiene un canvas Mermaid navigabile.

    `min_bytes`: sotto questa soglia non conviene scaricare (il riferimento
    costerebbe piu' del contenuto) e il payload resta inline.
    `max_canvas_nodes`: quanti nodi restano visibili nel canvas; gli altri sono
    elisi (ma contati e risolvibili).
    """

    def __init__(
        self,
        root: str | Path,
        *,
        run_id: str = "run",
        min_bytes: int = 400,
        max_canvas_nodes: int = 40,
    ):
        self.root = Path(root)
        self.run_id = str(run_id)
        self.min_bytes = int(min_bytes)
        self.max_canvas_nodes = max(1, int(max_canvas_nodes))
        self.nodes: List[OffloadNode] = []
        self._by_id: Dict[str, OffloadNode] = {}
        self._by_fingerprint: Dict[str, str] = {}
        self.inline_bytes = 0          # payload rimasti in contesto (sotto soglia)
        self.offloaded_bytes = 0       # payload spostati su disco

    # --- percorsi ---------------------------------------------------------
    @property
    def refs_dir(self) -> Path:
        return self.root / self.run_id / "refs"

    def _next_id(self) -> str:
        return f"n{len(self.nodes) + 1:03d}"

    # --- scrittura --------------------------------------------------------
    def should_offload(self, payload: str) -> bool:
        return len(str(payload).encode("utf-8")) >= self.min_bytes

    def offload(
        self,
        kind: str,
        label: str,
        payload: str,
        *,
        status: str = STATUS_OK,
        parent: Optional[str] = None,
    ) -> Optional[OffloadNode]:
        """Scarica un payload e ritorna il nodo, oppure None se e' troppo piccolo.

        Deduplica per impronta: due payload identici (stesso kind/label) non
        vengono riscritti, si riusa il nodo esistente. Utile nei loop di retry,
        dove lo stesso errore ricompare identico.
        """
        payload = "" if payload is None else str(payload)
        size = len(payload.encode("utf-8"))
        if not self.should_offload(payload):
            self.inline_bytes += size
            return None

        fingerprint = _fingerprint(kind, label, payload)
        existing_id = self._by_fingerprint.get(fingerprint)
        if existing_id:
            return self._by_id[existing_id]

        node_id = self._next_id()
        rel = f"{self.run_id}/refs/{node_id}.txt"
        target = self.root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(payload, encoding="utf-8")

        node = OffloadNode(
            node_id=node_id,
            kind=str(kind or KIND_NOTE),
            label=_safe_label(label),
            status=str(status or STATUS_OK),
            ref=rel,
            size_bytes=size,
            fingerprint=fingerprint,
            parent=parent,
        )
        self.nodes.append(node)
        self._by_id[node_id] = node
        self._by_fingerprint[fingerprint] = node_id
        self.offloaded_bytes += size
        return node

    # --- lettura (drill-down) --------------------------------------------
    def resolve(self, node_id: str) -> Optional[str]:
        """Riprende il payload INTEGRALE di un nodo. Nessuna perdita."""
        node = self._by_id.get(str(node_id).strip())
        if node is None:
            return None
        path = self.root / node.ref
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return None

    def resolve_mentions(self, text: str) -> Dict[str, str]:
        """Risolve tutti i `node_id` citati in un testo (es. la replica del modello)."""
        out: Dict[str, str] = {}
        for node_id in dict.fromkeys(NODE_ID_RE.findall(text or "")):
            payload = self.resolve(node_id)
            if payload is not None:
                out[node_id] = payload
        return out

    # --- canvas -----------------------------------------------------------
    def _visible_nodes(self) -> tuple[List[OffloadNode], List[OffloadNode]]:
        """Nodi da mostrare e nodi elisi.

        Regola: **i fallimenti restano sempre**. Si elidono i nodi riusciti piu'
        vecchi, perche' un errore e' esattamente cio' che serve quando si
        ricostruisce cos'e' andato storto.
        """
        if len(self.nodes) <= self.max_canvas_nodes:
            return list(self.nodes), []
        failures = [n for n in self.nodes if n.failed]
        others = [n for n in self.nodes if not n.failed]
        room = max(0, self.max_canvas_nodes - len(failures))
        kept_others = others[-room:] if room else []
        kept_ids = {n.node_id for n in failures} | {n.node_id for n in kept_others}
        visible = [n for n in self.nodes if n.node_id in kept_ids]
        elided = [n for n in self.nodes if n.node_id not in kept_ids]
        return visible, elided

    def render_canvas(self) -> str:
        """Canvas Mermaid: poche decine di token al posto dei log integrali."""
        if not self.nodes:
            return "graph TD\n    empty[\"nessun contesto scaricato\"]"

        visible, elided = self._visible_nodes()
        lines = ["graph TD"]
        previous: Optional[str] = None
        for node in visible:
            mark = "✗" if node.failed else ("…" if node.status == STATUS_PENDING else "✓")
            lines.append(f'    {node.node_id}["{node.node_id} · {node.kind} · {node.label} {mark}"]')
            link_from = node.parent if node.parent in self._by_id else previous
            if link_from:
                lines.append(f"    {link_from} --> {node.node_id}")
            previous = node.node_id

        if elided:
            ids = f"{elided[0].node_id}–{elided[-1].node_id}"
            total_kb = sum(n.size_bytes for n in elided) / 1024
            # L'elisione e' DICHIARATA: i nodi restano risolvibili per node_id.
            lines.append(
                f'    elided["… {len(elided)} nodi riusciti elisi dal canvas ({ids}, '
                f'{total_kb:.0f} KB) — ancora risolvibili per node_id"]'
            )
        for node in visible:
            if node.failed:
                lines.append(f"    style {node.node_id} stroke:#c00,stroke-width:2px")
        return "\n".join(lines)

    def render_context_block(self) -> str:
        """Blocco pronto da iniettare nel prompt, con le istruzioni d'uso."""
        stats = self.stats()
        return (
            "<task_canvas>\n"
            "# Mappa simbolica del lavoro svolto. I log integrali NON sono in contesto:\n"
            "# sono su disco e si recuperano citando il node_id (es. n007) quando ti\n"
            "# serve un dettaglio. Il canvas e' una scorciatoia, non un riassunto:\n"
            "# nulla e' stato perso.\n"
            f"{self.render_canvas()}\n"
            f"# {stats['nodes']} nodi · {stats['offloaded_kb']:.0f} KB fuori dal contesto\n"
            "</task_canvas>"
        )

    # --- diagnostica ------------------------------------------------------
    def stats(self) -> Dict[str, Any]:
        visible, elided = self._visible_nodes()
        return {
            "run_id": self.run_id,
            "nodes": len(self.nodes),
            "visible": len(visible),
            "elided": len(elided),
            "failures": sum(1 for n in self.nodes if n.failed),
            "offloaded_bytes": self.offloaded_bytes,
            "offloaded_kb": self.offloaded_bytes / 1024,
            "inline_bytes": self.inline_bytes,
            "canvas_chars": len(self.render_canvas()),
        }

    # --- persistenza (resume dopo crash, come ContextSteward) -------------
    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "root": str(self.root),
            "min_bytes": self.min_bytes,
            "max_canvas_nodes": self.max_canvas_nodes,
            "inline_bytes": self.inline_bytes,
            "offloaded_bytes": self.offloaded_bytes,
            "nodes": [n.to_dict() for n in self.nodes],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ContextOffload":
        data = data or {}
        instance = cls(
            data.get("root") or ".",
            run_id=data.get("run_id") or "run",
            min_bytes=int(data.get("min_bytes", 400)),
            max_canvas_nodes=int(data.get("max_canvas_nodes", 40)),
        )
        instance.inline_bytes = int(data.get("inline_bytes", 0))
        instance.offloaded_bytes = int(data.get("offloaded_bytes", 0))
        for raw in data.get("nodes") or []:
            node = OffloadNode.from_dict(raw)
            instance.nodes.append(node)
            instance._by_id[node.node_id] = node
            instance._by_fingerprint[node.fingerprint] = node.node_id
        return instance
