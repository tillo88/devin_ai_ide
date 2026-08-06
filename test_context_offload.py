"""Test di CS4 simbolico (devin/core/context_offload.py).

Offline e deterministico: nessun modello, nessuna rete.

Protegge l'invariante che rende questo approccio preferibile alla compattazione
LLM: **si perde ingombro, non informazione**. Ogni payload resta recuperabile
byte-per-byte, e i fallimenti non vengono mai elisi dal canvas.
"""

from __future__ import annotations

import pytest

from devin.core.context_offload import (
    KIND_ERROR,
    KIND_TOOL,
    STATUS_FAIL,
    STATUS_OK,
    ContextOffload,
    OffloadNode,
)

BIG = "x" * 5000          # sopra soglia: va su disco
SMALL = "ok"              # sotto soglia: resta inline


@pytest.fixture
def off(tmp_path):
    return ContextOffload(tmp_path, run_id="r1", min_bytes=400, max_canvas_nodes=5)


class TestOffload:
    def test_payload_grande_va_su_disco(self, off, tmp_path):
        node = off.offload(KIND_TOOL, "pytest output", BIG)
        assert node is not None
        assert node.node_id == "n001"
        assert (tmp_path / node.ref).is_file()
        assert off.offloaded_bytes == 5000

    def test_payload_piccolo_resta_inline(self, off):
        assert off.offload(KIND_TOOL, "piccolo", SMALL) is None
        assert off.nodes == []
        assert off.inline_bytes == len(SMALL)

    def test_nessuna_perdita_resolve_e_byte_per_byte(self, off):
        payload = "riga1\nriga2\n" + "y" * 1000 + "\nfine"
        node = off.offload(KIND_TOOL, "log", payload)
        assert off.resolve(node.node_id) == payload

    def test_dedup_per_impronta(self, off):
        # Nei retry lo stesso errore ricompare identico: non riscriverlo.
        a = off.offload(KIND_ERROR, "boom", BIG, status=STATUS_FAIL)
        b = off.offload(KIND_ERROR, "boom", BIG, status=STATUS_FAIL)
        assert a.node_id == b.node_id
        assert len(off.nodes) == 1

    def test_payload_diversi_nodi_diversi(self, off):
        a = off.offload(KIND_TOOL, "log", BIG)
        b = off.offload(KIND_TOOL, "log", BIG + "z")
        assert a.node_id != b.node_id

    def test_resolve_id_inesistente(self, off):
        assert off.resolve("n999") is None


class TestCanvas:
    def test_canvas_vuoto(self, off):
        assert "nessun contesto scaricato" in off.render_canvas()

    def test_canvas_molto_piu_piccolo_del_payload(self, off):
        for i in range(4):
            off.offload(KIND_TOOL, f"step {i}", BIG)
        canvas = off.render_canvas()
        # 4 payload da 5000 byte = 20 KB; il canvas deve stare in poche centinaia
        assert len(canvas) < 600
        assert off.offloaded_bytes == 20000

    def test_i_nodi_sono_greppabili(self, off):
        off.offload(KIND_TOOL, "primo", BIG)
        assert "n001" in off.render_canvas()

    def test_i_fallimenti_non_vengono_mai_elisi(self, off):
        # max_canvas_nodes=5: un errore vecchio deve sopravvivere a 10 successi.
        off.offload(KIND_ERROR, "errore antico", BIG + "err", status=STATUS_FAIL)
        for i in range(10):
            off.offload(KIND_TOOL, f"ok {i}", BIG + str(i))
        canvas = off.render_canvas()
        assert "n001" in canvas                # l'errore c'e' ancora
        assert "errore antico" in canvas

    def test_elisione_dichiarata_e_ancora_risolvibile(self, off):
        for i in range(12):
            off.offload(KIND_TOOL, f"ok {i}", BIG + str(i))
        canvas = off.render_canvas()
        assert "elisi dal canvas" in canvas
        assert "risolvibili per node_id" in canvas
        # elisi dal CANVAS, non persi: il payload torna comunque
        assert off.resolve("n001") is not None

    def test_conteggio_elisi_corretto(self, off):
        for i in range(12):
            off.offload(KIND_TOOL, f"ok {i}", BIG + str(i))
        stats = off.stats()
        assert stats["nodes"] == 12
        assert stats["visible"] == 5
        assert stats["elided"] == 7

    def test_deterministico(self, tmp_path):
        def build(path):
            o = ContextOffload(path, run_id="r", min_bytes=400, max_canvas_nodes=5)
            for i in range(3):
                o.offload(KIND_TOOL, f"step {i}", BIG + str(i))
            return o.render_canvas()

        assert build(tmp_path / "a") == build(tmp_path / "b")

    def test_etichette_sanificate_per_mermaid(self, off):
        # virgolette e newline romperebbero il grafo
        node = off.offload(KIND_TOOL, 'ha detto "ciao"\ne poi\tbasta', BIG)
        assert '"' not in node.label
        assert "\n" not in node.label
        canvas = off.render_canvas()
        assert canvas.count('"') % 2 == 0     # virgolette bilanciate

    def test_etichetta_lunga_troncata(self, off):
        node = off.offload(KIND_TOOL, "parola " * 50, BIG)
        assert len(node.label) <= 48

    def test_fallimento_evidenziato(self, off):
        off.offload(KIND_ERROR, "crash", BIG, status=STATUS_FAIL)
        canvas = off.render_canvas()
        assert "✗" in canvas
        assert "stroke:#c00" in canvas


class TestContextBlock:
    def test_blocco_spiega_come_recuperare(self, off):
        off.offload(KIND_TOOL, "log", BIG)
        block = off.render_context_block()
        assert "<task_canvas>" in block and "</task_canvas>" in block
        assert "node_id" in block
        # deve essere chiaro che NON e' un riassunto
        assert "non e' un riassunto" in block or "non un riassunto" in block

    def test_resolve_mentions(self, off):
        a = off.offload(KIND_TOOL, "primo", BIG)
        off.offload(KIND_TOOL, "secondo", BIG + "2")
        risposta_modello = f"Guardando {a.node_id} vedo il problema, e anche n002."
        found = off.resolve_mentions(risposta_modello)
        assert set(found) == {"n001", "n002"}
        assert found["n001"] == BIG

    def test_resolve_mentions_ignora_id_inventati(self, off):
        off.offload(KIND_TOOL, "primo", BIG)
        found = off.resolve_mentions("secondo n999 e n001")
        assert set(found) == {"n001"}


class TestPersistence:
    def test_roundtrip(self, off, tmp_path):
        off.offload(KIND_TOOL, "primo", BIG)
        off.offload(KIND_ERROR, "boom", BIG + "e", status=STATUS_FAIL)
        restored = ContextOffload.from_dict(off.to_dict())
        assert len(restored.nodes) == 2
        assert restored.render_canvas() == off.render_canvas()
        assert restored.resolve("n001") == BIG

    def test_dedup_sopravvive_al_resume(self, off):
        off.offload(KIND_TOOL, "primo", BIG)
        restored = ContextOffload.from_dict(off.to_dict())
        again = restored.offload(KIND_TOOL, "primo", BIG)
        assert again.node_id == "n001"
        assert len(restored.nodes) == 1

    def test_numerazione_continua_dopo_il_resume(self, off):
        off.offload(KIND_TOOL, "primo", BIG)
        restored = ContextOffload.from_dict(off.to_dict())
        nuovo = restored.offload(KIND_TOOL, "secondo", BIG + "2")
        assert nuovo.node_id == "n002"

    def test_node_from_dict_tollera_campi_mancanti(self):
        node = OffloadNode.from_dict({"node_id": "n001", "kind": "tool", "label": "x",
                                      "status": "ok", "ref": "r", "size_bytes": 1,
                                      "fingerprint": "f"})
        assert node.parent is None


class TestStats:
    def test_stats(self, off):
        off.offload(KIND_TOOL, "grande", BIG)
        off.offload(KIND_TOOL, "piccolo", SMALL)
        stats = off.stats()
        assert stats["nodes"] == 1
        assert stats["offloaded_bytes"] == 5000
        assert stats["inline_bytes"] == len(SMALL)
        assert stats["canvas_chars"] < stats["offloaded_bytes"]   # il punto di CS4
