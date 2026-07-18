"""
test_vector_store_e2e.py — Test E2E per VectorStore (Fase 1)
NO server, NO GPU, NO rete richiesti.
"""

import sys
import os
import tempfile
import shutil
from pathlib import Path

# Aggiungi devin al path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from devin.memory.vector_store import VectorStore, _run_e2e_test


def test_basic_indexing():
    """Test base: indicizzazione e ricerca."""
    tmpdir = tempfile.mkdtemp(prefix="devin_vs_test_")
    project_path = Path(tmpdir)

    try:
        # Setup file
        (project_path / "calc.py").write_text("""
def add(a, b):
    return a + b
""")
        (project_path / "main.py").write_text("""
from calc import add
print(add(2, 3))
""")

        files = [
            {"path": str(project_path / "calc.py"), "content": (project_path / "calc.py").read_text()},
            {"path": str(project_path / "main.py"), "content": (project_path / "main.py").read_text()},
        ]

        vs = VectorStore()
        vs.index_project(str(project_path), files)

        assert len(vs._index) == 2, f"Atteso 2 documenti, trovati {len(vs._index)}"

        # Ricerca
        results = vs.search_semantic("addition function", project_path=str(project_path), top_k=2)
        assert len(results) > 0
        assert any("calc.py" in r["metadata"]["path"] for r in results)

        print("✅ test_basic_indexing PASS")

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_cache_persistence():
    """Test persistenza cache con mtime."""
    tmpdir = tempfile.mkdtemp(prefix="devin_vs_cache_test_")
    project_path = Path(tmpdir)
    cache_path = project_path / ".devin_cache" / "test.pkl"

    try:
        (project_path / "file.py").write_text("x = 1")
        files = [{"path": str(project_path / "file.py"), "content": "x = 1"}]

        vs1 = VectorStore()
        vs1.index_project(str(project_path), files, cache_path=cache_path)

        # Seconda istanza: dovrebbe caricare da cache
        vs2 = VectorStore()
        vs2.index_project(str(project_path), files, cache_path=cache_path)

        assert len(vs2._index) == 1
        print("✅ test_cache_persistence PASS")

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_cache_invalidation():
    """Test invalidazione cache su file modificato."""
    tmpdir = tempfile.mkdtemp(prefix="devin_vs_inv_test_")
    project_path = Path(tmpdir)
    cache_path = project_path / ".devin_cache" / "test.pkl"

    try:
        (project_path / "file.py").write_text("x = 1")
        files = [{"path": str(project_path / "file.py"), "content": "x = 1"}]

        vs1 = VectorStore()
        vs1.index_project(str(project_path), files, cache_path=cache_path)

        # Modifica file
        import time
        time.sleep(0.05)
        (project_path / "file.py").write_text("x = 2")
        files_updated = [{"path": str(project_path / "file.py"), "content": "x = 2"}]

        vs2 = VectorStore()
        vs2.index_project(str(project_path), files_updated, cache_path=cache_path)

        # Verifica che l'indice sia stato aggiornato
        results = vs2.search_semantic("x equals two", project_path=str(project_path))
        assert len(results) > 0
        assert "x = 2" in results[0]["text"]

        print("✅ test_cache_invalidation PASS")

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_empty_project():
    """Test progetto vuoto."""
    tmpdir = tempfile.mkdtemp(prefix="devin_vs_empty_")
    project_path = Path(tmpdir)

    try:
        vs = VectorStore()
        vs.index_project(str(project_path), [])
        results = vs.search_semantic("anything", project_path=str(project_path))
        assert len(results) == 0
        print("✅ test_empty_project PASS")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ============================================================
# HARDENING 2026-07-18: cache JSON versionata (niente pickle)
# ============================================================

def test_json_cache_actually_persists_and_loads():
    """La cache deve essere scritta in JSON e RIUSATA al secondo giro."""
    import json
    tmpdir = tempfile.mkdtemp(prefix="devin_vs_json_")
    project_path = Path(tmpdir)
    cache_path = project_path / ".devin_cache" / "semantic_index.json"

    try:
        (project_path / "file.py").write_text("def compute_total(items): return sum(items)")
        files = [{"path": str(project_path / "file.py"),
                  "content": (project_path / "file.py").read_text()}]

        vs1 = VectorStore()
        vs1.index_project(str(project_path), files, cache_path=cache_path)

        assert cache_path.exists(), "Cache JSON non scritta"
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        assert cached.get("format") == VectorStore._CACHE_FORMAT

        # Seconda istanza: se la cache e' valida NON deve risalvarla
        save_calls = []
        orig_save = VectorStore._save_to_cache
        VectorStore._save_to_cache = lambda self, cp, mh: save_calls.append(cp)
        try:
            vs2 = VectorStore()
            vs2.index_project(str(project_path), files, cache_path=cache_path)
        finally:
            VectorStore._save_to_cache = orig_save

        assert len(vs2._index) == 1
        assert not save_calls, "Cache valida ignorata: re-indicizzazione inattesa"
        print("✅ test_json_cache_actually_persists_and_loads PASS")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_legacy_pkl_migrated_without_being_read():
    """Un path .pkl legacy migra a .json; il pickle non viene MAI letto."""
    tmpdir = tempfile.mkdtemp(prefix="devin_vs_pkl_")
    project_path = Path(tmpdir)
    legacy = project_path / ".devin_cache" / "semantic_index.pkl"

    try:
        (project_path / "file.py").write_text("x = 1")
        legacy.parent.mkdir(parents=True, exist_ok=True)
        # Contenuto ostile: non e' ne' JSON ne' un pickle sensato; se il codice
        # provasse a leggerlo come cache valida farebbe confusione.
        legacy.write_bytes(b"\x80\x04 hostile pickle bytes {{{")

        files = [{"path": str(project_path / "file.py"), "content": "x = 1"}]
        vs = VectorStore()
        vs.index_project(str(project_path), files, cache_path=legacy)

        assert not legacy.exists(), "Il file .pkl legacy non e' stato rimosso"
        json_cache = legacy.with_suffix(".json")
        assert json_cache.exists(), "Cache .json migrata non trovata"
        assert len(vs._index) == 1
        print("✅ test_legacy_pkl_migrated_without_being_read PASS")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_cached_search_consistent_after_tfidf_refit():
    """Dopo load da cache, la search con engine corpus-dipendente deve
    ritrovare il documento giusto (vocabolario rifittato sui testi cached)."""
    tmpdir = tempfile.mkdtemp(prefix="devin_vs_refit_")
    project_path = Path(tmpdir)
    cache_path = project_path / ".devin_cache" / "semantic_index.json"

    try:
        (project_path / "calc.py").write_text(
            "def add(a, b):\n    \"\"\"Add numbers and return the sum total.\"\"\"\n    return a + b\n")
        (project_path / "net.py").write_text(
            "import socket\n\ndef open_connection(host):\n    return socket.create_connection((host, 80))\n")
        files = [
            {"path": str(project_path / "calc.py"), "content": (project_path / "calc.py").read_text()},
            {"path": str(project_path / "net.py"), "content": (project_path / "net.py").read_text()},
        ]

        vs1 = VectorStore()
        vs1.index_project(str(project_path), files, cache_path=cache_path)

        # Nuova istanza: carica da cache e cerca
        vs2 = VectorStore()
        vs2.index_project(str(project_path), files, cache_path=cache_path)
        results = vs2.search_semantic("add numbers sum total", project_path=str(project_path), top_k=1)

        assert results, "Nessun risultato dalla cache caricata"
        assert "calc.py" in results[0]["metadata"]["path"], \
            f"Risultato sbagliato dopo cache load: {results[0]['metadata']['path']}"
        assert results[0]["score"] > 0, f"Score nullo: spazio embedding inconsistente"
        print("✅ test_cached_search_consistent_after_tfidf_refit PASS")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ============================================================
# W5 (2026-07-18): staleness key = path+mtime+size+content hash
# ============================================================

def test_reindex_on_content_rewrite_with_preserved_mtime():
    """Riscrittura contenuto + mtime identico (os.utime) => reindex, NO stale.

    Prima del fix la chiave era path+mtime: questo scenario serviva gli
    embedding del contenuto VECCHIO in silenzio."""
    tmpdir = tempfile.mkdtemp(prefix="devin_vs_stale_")
    project_path = Path(tmpdir)
    cache_path = project_path / ".devin_cache" / "semantic_index.json"

    try:
        target = project_path / "file.py"
        target.write_text("def alpha_unique_token(): return 1\n")
        files_v1 = [{"path": str(target), "content": target.read_text()}]

        vs1 = VectorStore()
        vs1.index_project(str(project_path), files_v1, cache_path=cache_path)
        assert len(vs1._index) == 1
        assert "alpha_unique_token" in vs1._index[0]["text"]

        # Riscrivi contenuto e ripristina l'mtime ESATTO di prima
        st = target.stat()
        target.write_text("def omega_different_token(): return 2\n")
        os.utime(target, ns=(st.st_atime_ns, st.st_mtime_ns))
        assert target.stat().st_mtime_ns == st.st_mtime_ns

        files_v2 = [{"path": str(target), "content": target.read_text()}]
        vs2 = VectorStore()
        vs2.index_project(str(project_path), files_v2, cache_path=cache_path)

        assert len(vs2._index) == 1
        assert "omega_different_token" in vs2._index[0]["text"], \
            f"Contenuto stale servito dalla cache: {vs2._index[0]['text']!r}"

        results = vs2.search_semantic("omega_different_token", project_path=str(project_path))
        assert results and "omega_different_token" in results[0]["text"], \
            "Nuovo contenuto non trovato in search: cache stale"
        print("✅ test_reindex_on_content_rewrite_with_preserved_mtime PASS")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_old_mtime_only_cache_entries_invalidated_not_crashed():
    """Una cache scritta col VECCHIO formato chiavi (path+mtime) non deve
    crashare: mismatch di chiave => reindex pulito."""
    import json
    import hashlib
    tmpdir = tempfile.mkdtemp(prefix="devin_vs_oldcache_")
    project_path = Path(tmpdir)
    cache_path = project_path / ".devin_cache" / "semantic_index.json"

    try:
        target = project_path / "file.py"
        target.write_text("x = 1\n")
        files = [{"path": str(target), "content": "x = 1\n"}]

        vs1 = VectorStore()
        vs1.index_project(str(project_path), files, cache_path=cache_path)

        # Simula una cache legacy: files_meta con chiavi path+mtime (vecchio formato)
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        legacy_meta = {}
        for p in cached["files_meta"]:
            mtime = Path(p).stat().st_mtime
            legacy_meta[p] = hashlib.md5(f"{p}:{mtime}".encode()).hexdigest()[:16]
        cached["files_meta"] = legacy_meta
        cache_path.write_text(json.dumps(cached), encoding="utf-8")

        # Deve re-indicizzare senza errori e servire il contenuto corrente
        vs2 = VectorStore()
        vs2.index_project(str(project_path), files, cache_path=cache_path)
        assert len(vs2._index) == 1
        results = vs2.search_semantic("x = 1", project_path=str(project_path))
        assert results, "Search fallita dopo migrazione chiavi legacy"
        print("✅ test_old_mtime_only_cache_entries_invalidated_not_crashed PASS")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ============================================================
# W6 (2026-07-18): search_semantic normalizza i project path
# ============================================================

def _make_project(tmpdir, name, token):
    root = Path(tmpdir) / name
    root.mkdir(parents=True)
    f = root / "code.py"
    f.write_text(f"def {token}(): return 1\n")
    return root, [{"path": str(f), "content": f.read_text()}]


def test_search_semantic_project_path_normalized():
    """Indicizza con path assoluto; cerca con trailing slash e variante '..':
    i risultati devono arrivare (prima: [] silenzioso)."""
    tmpdir = tempfile.mkdtemp(prefix="devin_vs_norm_")
    cache_path = Path(tmpdir) / ".devin_cache" / "semantic_index.json"

    try:
        proj_a, files_a = _make_project(tmpdir, "projA", "alpha_token_xyz")
        vs = VectorStore()
        vs.index_project(str(proj_a), files_a, cache_path=cache_path)

        abs_results = vs.search_semantic("alpha_token_xyz", project_path=str(proj_a))
        assert abs_results, "Baseline: nessun risultato con path assoluto identico"

        slash = vs.search_semantic("alpha_token_xyz", project_path=str(proj_a) + "/")
        assert slash, "Trailing slash: recall azzerato (filtro non normalizzato)"

        dotdot = vs.search_semantic(
            "alpha_token_xyz", project_path=str(Path(tmpdir) / "other" / ".." / "projA"))
        assert dotdot, "Variante '..': recall azzerato (filtro non normalizzato)"

        print("✅ test_search_semantic_project_path_normalized PASS")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_search_semantic_project_isolation_after_normalization():
    """La normalizzazione non deve bucare l'isolamento: un altro progetto
    (anche con nome simile / variante di path) non deve ricevere hit."""
    tmpdir = tempfile.mkdtemp(prefix="devin_vs_iso_")
    cache_path = Path(tmpdir) / ".devin_cache" / "semantic_index.json"

    try:
        proj_a, files_a = _make_project(tmpdir, "projA", "alpha_token_xyz")
        proj_b, files_b = _make_project(tmpdir, "projB", "beta_token_xyz")

        vs = VectorStore()
        vs.index_project(str(proj_a), files_a, cache_path=cache_path)
        # Aggiungo il doc di B direttamente (stessa struttura usata da
        # index_project): isola il test del FILTRO dal comportamento di
        # index_project/cache — vedi i test multi-progetto W9 per il
        # percorso via index_project.
        emb_b = vs._encode([files_b[0]["content"]])[0]
        vs._index.append({
            "text": files_b[0]["content"],
            "embedding": emb_b,
            "metadata": {"path": files_b[0]["path"], "project": str(proj_b),
                         "filename": "code.py"},
            "id": 1,
        })
        assert len(vs._index) == 2

        # Cerca il token di A filtrando su B (con trailing slash): zero hit
        hits = vs.search_semantic("alpha_token_xyz", project_path=str(proj_b) + "/")
        assert not any("alpha_token_xyz" in h["text"] for h in hits), \
            "Cross-project leakage: documento di A restituito filtrando su B"

        # E filtrando su A con variante '..' il token di B non deve apparire
        hits_a = vs.search_semantic(
            "beta_token_xyz",
            project_path=str(Path(tmpdir) / "x" / ".." / "projA"))
        assert not any("beta_token_xyz" in h["text"] for h in hits_a), \
            "Cross-project leakage: documento di B restituito filtrando su A"

        print("✅ test_search_semantic_project_isolation_after_normalization PASS")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ============================================================
# W9 (2026-07-18): index_project multi-progetto — eviction per-progetto
# ============================================================

def test_index_project_keeps_other_projects_docs():
    """Regressione: indicizzare il progetto B NON deve azzerare i doc di A.

    Pre-fix: index_project faceva `self._index = []` a ogni chiamata (nonostante
    il commento "rimuovi vecchi doc dello stesso progetto") -> la search su A
    tornava [] silenzioso: recall failure indistinguibile da "nessun file
    rilevante"."""
    tmpdir = tempfile.mkdtemp(prefix="devin_vs_multi_")

    try:
        proj_a, files_a = _make_project(tmpdir, "projA", "alpha_token_xyz")
        proj_b, files_b = _make_project(tmpdir, "projB", "beta_token_xyz")
        cache_a = proj_a / ".devin_cache" / "semantic_index.json"
        cache_b = proj_b / ".devin_cache" / "semantic_index.json"

        vs = VectorStore()
        vs.index_project(str(proj_a), files_a, cache_path=cache_a)
        vs.index_project(str(proj_b), files_b, cache_path=cache_b)

        hits_a = vs.search_semantic("alpha_token_xyz", project_path=str(proj_a))
        assert hits_a and "alpha_token_xyz" in hits_a[0]["text"], \
            "Recall di A perso dopo indicizzazione di B (indice azzerato)"

        hits_b = vs.search_semantic("beta_token_xyz", project_path=str(proj_b))
        assert hits_b and "beta_token_xyz" in hits_b[0]["text"], \
            "Baseline: B non trovato dopo la propria indicizzazione"

        print("✅ test_index_project_keeps_other_projects_docs PASS")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_reindex_refreshes_only_same_project():
    """Re-indicizzare A aggiorna i doc di A senza toccare B; la cache di A
    contiene SOLO doc di A (le cache restano per-progetto)."""
    import json
    tmpdir = tempfile.mkdtemp(prefix="devin_vs_refresh_")

    try:
        proj_a, files_a = _make_project(tmpdir, "projA", "alpha_token_xyz")
        proj_b, files_b = _make_project(tmpdir, "projB", "beta_token_xyz")
        cache_a = proj_a / ".devin_cache" / "semantic_index.json"
        cache_b = proj_b / ".devin_cache" / "semantic_index.json"

        vs = VectorStore()
        vs.index_project(str(proj_a), files_a, cache_path=cache_a)
        vs.index_project(str(proj_b), files_b, cache_path=cache_b)

        # Modifica il file di A e re-indicizza A (content hash nella staleness
        # key: basta il contenuto diverso, niente sleep su mtime)
        f_a = proj_a / "code.py"
        f_a.write_text("def gamma_new_token():\n    return 2\n")
        files_a2 = [{"path": str(f_a), "content": f_a.read_text()}]
        vs.index_project(str(proj_a), files_a2, cache_path=cache_a)

        # A: nuovo contenuto trovato, vecchio rimosso
        hits = vs.search_semantic("gamma_new_token", project_path=str(proj_a))
        assert hits and "gamma_new_token" in hits[0]["text"], \
            "Nuovo contenuto di A non trovato dopo reindex"
        a_texts = [d["text"] for d in vs._index
                   if d["metadata"]["project"] == str(proj_a)]
        assert a_texts and not any("alpha_token_xyz" in t for t in a_texts), \
            "Vecchi doc di A non rimossi dal reindex"

        # B intatto
        hits_b = vs.search_semantic("beta_token_xyz", project_path=str(proj_b))
        assert hits_b and "beta_token_xyz" in hits_b[0]["text"], \
            "Reindex di A ha cancellato i doc di B"

        # La cache di A contiene SOLO doc di A (formato per-progetto preservato)
        cached_a = json.loads(cache_a.read_text(encoding="utf-8"))
        assert cached_a["index"], "Cache di A vuota dopo reindex"
        assert all(d["metadata"]["project"] == str(proj_a)
                   for d in cached_a["index"]), \
            "Cache di A contaminata con doc di altri progetti"
        assert any("gamma_new_token" in d["text"] for d in cached_a["index"])

        print("✅ test_reindex_refreshes_only_same_project PASS")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_project_cache_file_survives_other_project_reindex():
    """Pin del comportamento cache-layer: le cache sono file PER-PROGETTO
    (project/.devin_cache/semantic_index.json); re-indicizzare B non tocca il
    file cache di A, che resta caricabile da una nuova istanza. (Passa anche
    pre-fix: la perdita dati del bug era solo in-memoria, per la sessione.)"""
    tmpdir = tempfile.mkdtemp(prefix="devin_vs_cachesurv_")

    try:
        proj_a, files_a = _make_project(tmpdir, "projA", "alpha_token_xyz")
        proj_b, files_b = _make_project(tmpdir, "projB", "beta_token_xyz")
        cache_a = proj_a / ".devin_cache" / "semantic_index.json"
        cache_b = proj_b / ".devin_cache" / "semantic_index.json"

        vs = VectorStore()
        vs.index_project(str(proj_a), files_a, cache_path=cache_a)
        vs.index_project(str(proj_b), files_b, cache_path=cache_b)

        # Reindex di B con contenuto cambiato
        f_b = proj_b / "code.py"
        f_b.write_text("def beta_v2_token():\n    return 3\n")
        files_b2 = [{"path": str(f_b), "content": f_b.read_text()}]
        vs.index_project(str(proj_b), files_b2, cache_path=cache_b)

        assert cache_a.exists(), "Cache di A rimossa dal reindex di B"

        # Nuova istanza: A si ricarica dalla propria cache e risponde
        vs2 = VectorStore()
        vs2.index_project(str(proj_a), files_a, cache_path=cache_a)
        hits = vs2.search_semantic("alpha_token_xyz", project_path=str(proj_a))
        assert hits and "alpha_token_xyz" in hits[0]["text"], \
            "Cache di A non piu' valida dopo reindex di B"

        print("✅ test_project_cache_file_survives_other_project_reindex PASS")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def run_all_tests():
    """Esegue tutti i test e restituisce report."""
    print("=" * 60)
    print("VectorStore E2E Test Suite — Fase 1")
    print("=" * 60)

    tests = [
        ("Basic Indexing", test_basic_indexing),
        ("Cache Persistence", test_cache_persistence),
        ("Cache Invalidation", test_cache_invalidation),
        ("Empty Project", test_empty_project),
        ("JSON Cache Round-Trip", test_json_cache_actually_persists_and_loads),
        ("Legacy PKL Migration", test_legacy_pkl_migrated_without_being_read),
        ("Cached Search After Refit", test_cached_search_consistent_after_tfidf_refit),
        ("Staleness Key Preserved Mtime", test_reindex_on_content_rewrite_with_preserved_mtime),
        ("Legacy Mtime-Only Keys Migration", test_old_mtime_only_cache_entries_invalidated_not_crashed),
        ("Project Path Normalized", test_search_semantic_project_path_normalized),
        ("Project Isolation After Normalization", test_search_semantic_project_isolation_after_normalization),
        ("Multi-Project Coexistence", test_index_project_keeps_other_projects_docs),
        ("Reindex Refreshes Only Same Project", test_reindex_refreshes_only_same_project),
        ("Cache Survives Other Project Reindex", test_project_cache_file_survives_other_project_reindex),
        ("Integrated E2E", _run_e2e_test),
    ]

    passed = 0
    failed = 0

    for name, test_fn in tests:
        print(f"\n--- Running: {name} ---")
        try:
            result = test_fn()
            if result is not False:
                passed += 1
            else:
                failed += 1
                print(f"❌ {name} FAIL")
        except Exception as e:
            failed += 1
            print(f"❌ {name} FAIL: {e}")
            import traceback
            traceback.print_exc()

    print("\n" + "=" * 60)
    print(f"RISULTATI: {passed} passati, {failed} falliti")
    print("=" * 60)

    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
