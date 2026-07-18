"""vector_store.py - Semantic search con persistenza e re-indicizzazione condizionale

FASE 1 AGGIORNAMENTI:
- Persistenza in workspace/.devin_cache/semantic_index.json
- Re-indicizzazione condizionale (check mtime)
- Fallback robusto: sentence-transformers -> sklearn -> keyword matching
- Test E2E integrato
- BUGFIX: TF-IDF con testi corti, cache dir creation, multilingual search

HARDENING (2026-07-18): cache in JSON versionato, niente piu' pickle (il pickle
puo' eseguire codice in load). I vecchi path .pkl vengono migrati al formato
.json e il file .pkl rimosso senza mai essere letto. Dopo il load da cache,
gli engine corpus-dipendenti (sklearn-tfidf, keyword) rifitano gli embedding
sui testi cached: il vocabolario dipende dal corpus, quindi embeddings cached
sarebbero incompatibili con la query encoding.

STALENESS KEY (2026-07-18, W5): la chiave in files_meta non e' piu' solo
path+mtime ma path+mtime+size+md5(contenuto): una riscrittura che preserva
mtime (granularita' FS, os.utime, alcuni checkout git) serviva embedding
stale. Nessun bump di _CACHE_FORMAT necessario: le vecchie chiavi mtime-only
semplicemente non matchano le nuove -> reindex una tantum, mai crash (il
confronto e' pura string equality su JSON).

MULTI-PROGETTO (2026-07-18, W9): index_project non azzera piu' l'intero
indice a ogni chiamata — rimuove SOLO i doc del progetto (normalizzato) che
sta re-indicizzando, come il commento prometteva. Prima, indicizzare il
progetto B cancellava silenziosamente gli embeddings di A: search_semantic
su A tornava [] — recall failure indistinguibile da "nessun file rilevante".
Il load da cache fonde (eviction per-progetto + extend) e il save scrive
solo i doc del progetto corrente: le cache restano file per-progetto.
"""

import json
import hashlib
import os
from pathlib import Path
from typing import List, Dict, Any, Optional


class VectorStore:
    """
    Vector store con embedding progressivi:
    1. sentence-transformers all-MiniLM-L6-v2 (~80MB)
    2. sklearn TfidfVectorizer(max_features=5000) — con min_df=1 per testi corti
    3. keyword matching (one-hot parole, pad a stessa dim)
    """

    # Formato cache versionato (JSON, mai pickle: il pickle puo' eseguire codice).
    _CACHE_FORMAT = "devin-vector-cache/1"

    def __init__(self, cache_dir: str = None):
        self._embedding_engine = None
        self._engine_name = None
        self._vectorizer = None
        self._index = []  # lista di dict: {text, embedding, metadata, mtime_hash}
        self._dim = None
        self._cache_dir = Path(cache_dir) if cache_dir else None
        self._cache_file = None
        self._project_path = None

    def _make_tfidf(self):
        """Costruisce il TfidfVectorizer con i parametri canonici del progetto."""
        from sklearn.feature_extraction.text import TfidfVectorizer
        return TfidfVectorizer(
            max_features=5000,
            min_df=1,           # BUGFIX: accetta anche parole che appaiono 1 volta
            stop_words=None,     # BUGFIX: non filtrare stop words (multilingual)
            token_pattern=r"(?u)\b\w+\b"  # BUGFIX: include anche numeri e underscore
        )

    def _get_embedding_engine(self):
        """Inizializza l'engine di embedding migliore disponibile."""
        if self._embedding_engine is not None:
            return self._embedding_engine, self._engine_name

        # 1. Prova sentence-transformers
        try:
            from sentence_transformers import SentenceTransformer
            model = SentenceTransformer('all-MiniLM-L6-v2')
            self._embedding_engine = model
            self._engine_name = "sentence-transformers"
            self._dim = 384  # all-MiniLM-L6-v2
            print(f"[VectorStore] Using sentence-transformers (dim={self._dim})")
            return self._embedding_engine, self._engine_name
        except Exception as e:
            print(f"[VectorStore] sentence-transformers unavailable: {e}")

        # 2. Fallback a sklearn TF-IDF — min_df=1 per testi corti, no stop words
        try:
            self._vectorizer = self._make_tfidf()
            self._embedding_engine = self._vectorizer
            self._engine_name = "sklearn-tfidf"
            self._dim = 5000
            print(f"[VectorStore] Using sklearn TF-IDF (dim={self._dim})")
            return self._embedding_engine, self._engine_name
        except Exception as e:
            print(f"[VectorStore] sklearn unavailable: {e}")

        # 3. Fallback a keyword matching
        self._embedding_engine = None
        self._engine_name = "keyword"
        self._dim = 1000
        print(f"[VectorStore] Using keyword matching (dim={self._dim})")
        return self._embedding_engine, self._engine_name

    def _encode(self, texts: List[str]) -> List[List[float]]:
        """Codifica una lista di testi in embeddings."""
        engine, name = self._get_embedding_engine()

        if name == "sentence-transformers":
            embeddings = engine.encode(texts, show_progress_bar=False)
            return embeddings.tolist()

        elif name == "sklearn-tfidf":
            if not hasattr(self._vectorizer, 'vocabulary_'):
                # Prima chiamata: fit_transform
                matrix = self._vectorizer.fit_transform(texts)
            else:
                matrix = self._vectorizer.transform(texts)
            return matrix.toarray().tolist()

        else:  # keyword fallback
            return self._encode_keyword(texts)

    def _encode_keyword(self, texts: List[str]) -> List[List[float]]:
        """Fallback: one-hot encoding delle parole più comuni."""
        from collections import Counter
        import re

        # Estrai parole da tutti i testi
        all_words = []
        for text in texts:
            words = re.findall(r'\b[a-zA-Z_]+\b', text.lower())
            all_words.extend(words)

        # Top 1000 parole
        vocab = {word: i for i, (word, _) in enumerate(Counter(all_words).most_common(1000))}

        embeddings = []
        for text in texts:
            words = re.findall(r'\b[a-zA-Z_]+\b', text.lower())
            vec = [0.0] * 1000
            for word in words:
                if word in vocab:
                    vec[vocab[word]] = 1.0
            embeddings.append(vec)

        return embeddings

    def _staleness_key(self, file_path: Path, content: Optional[str] = None) -> str:
        """Chiave di staleness: path + mtime + size + hash del contenuto.

        mtime-only serviva embedding stale quando una riscrittura preservava
        mtime (granularita' FS grossolana, os.utime, alcuni checkout git).
        Il contenuto e' GIA' in memoria per l'embedding (files[].content),
        quindi un md5 del contenuto e' quasi gratis — niente re-read da disco.
        size resta nel mix come segnale cheap; per path non presenti su disco
        (chunk virtuali tipo "file#chunk-h") lo stat fallisce e la chiave si
        appoggia a path+contenuto.
        """
        content_hash = hashlib.md5(
            (content or "").encode("utf-8", errors="replace")).hexdigest()[:16]
        try:
            st = file_path.stat()
            base = f"{file_path}:{st.st_mtime}:{st.st_size}"
        except Exception:
            base = str(file_path)
        return hashlib.md5(f"{base}:{content_hash}".encode()).hexdigest()[:16]

    def _should_reindex(self, files: List[Dict[str, Any]], cache_path: Path) -> bool:
        """Verifica se l'indice in cache è ancora valido."""
        if not cache_path.exists():
            return True

        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cached = json.load(f)

            if cached.get("format") != self._CACHE_FORMAT:
                print(f"[VectorStore] Cache format mismatch, reindexing")
                return True

            cached_files = cached.get("files_meta", {})
            for file_info in files:
                path = Path(file_info["path"])
                current_hash = self._staleness_key(path, file_info.get("content"))
                cached_hash = cached_files.get(str(path))
                if cached_hash != current_hash:
                    print(f"[VectorStore] File changed: {path}")
                    return True

            # Verifica anche che non ci siano file rimossi
            current_paths = {str(f["path"]) for f in files}
            cached_paths = set(cached_files.keys())
            if current_paths != cached_paths:
                print(f"[VectorStore] File set changed")
                return True

            return False

        except Exception as e:
            print(f"[VectorStore] Cache read error: {e}, reindexing")
            return True

    def index_project(
        self, 
        project_path: str, 
        files: List[Dict[str, Any]],
        cache_path: Path = None
    ):
        """
        Indicizza i file del progetto con persistenza.

        Args:
            project_path: path del progetto
            files: lista di dict con 'path', 'content'
            cache_path: path per la cache persistente (default: project/.devin_cache/semantic_index.json)
        """
        self._project_path = Path(project_path)

        if cache_path is None:
            cache_dir = self._project_path / ".devin_cache"
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_path = cache_dir / "semantic_index.json"
        else:
            # BUGFIX: assicurati che la directory esista
            cache_path.parent.mkdir(parents=True, exist_ok=True)

        # Migrazione legacy: un path .pkl viene convertito in .json e il file
        # pickle rimosso SENZA mai essere letto (pickle.load puo' eseguire codice).
        if cache_path.suffix == ".pkl":
            legacy_pkl = cache_path
            cache_path = cache_path.with_suffix(".json")
            try:
                legacy_pkl.unlink(missing_ok=True)
            except Exception:
                pass

        self._cache_file = cache_path

        # Verifica se serve re-indicizzazione
        if not self._should_reindex(files, cache_path):
            print(f"[VectorStore] Cache valida, caricamento da {cache_path}")
            self._load_from_cache(cache_path)
            return

        print(f"[VectorStore] Re-indicizzazione di {len(files)} file...")

        # Rimuovi vecchi doc dello stesso progetto — SOLO quelli: doc di
        # altri progetti nello stesso store restano (vedi _evict_project_docs).
        self._evict_project_docs(project_path)

        texts = []
        metadatas = []
        mtime_hashes = {}

        for file_info in files:
            path = Path(file_info["path"])
            raw_content = file_info.get("content", "")

            # Staleness key sul contenuto RAW (pre-troncamento): una modifica
            # oltre i 4000 char deve comunque invalidare la cache.
            staleness = self._staleness_key(path, raw_content)

            # Tronca content a 4000 chars
            content = raw_content
            if len(content) > 4000:
                content = content[:4000]

            texts.append(content)
            metadatas.append({
                "path": str(path),
                "project": str(project_path),
                "filename": path.name,
            })
            mtime_hashes[str(path)] = staleness

        if not texts:
            print("[VectorStore] Nessun file da indicizzare")
            return

        # Genera embeddings
        embeddings = self._encode(texts)

        # Costruisci indice
        for i, (text, emb, meta) in enumerate(zip(texts, embeddings, metadatas)):
            self._index.append({
                "text": text,
                "embedding": emb,
                "metadata": meta,
                "id": i,
            })

        # Salva cache
        self._save_to_cache(cache_path, mtime_hashes)
        print(f"[VectorStore] Indicizzati {len(texts)} documenti")

    def _evict_project_docs(self, project_path) -> None:
        """Rimuove dall'indice in memoria SOLO i doc del progetto indicato.

        Multi-progetto (2026-07-18, W9): index_project viene chiamato spesso
        (retrieve_context lo invoca a ogni query); l'eviction deve essere
        per-progetto. Azzerare tutto l'indice cancellava SILENZIOSAMENTE gli
        embeddings degli altri progetti -> recall failure indistinguibile da
        "nessun file rilevante". Il confronto usa i path normalizzati (stessa
        regola di search_semantic): chunk virtuali tipo "file#chunk-h" hanno
        comunque metadata.project valorizzato da index_project.
        """
        if project_path is None:
            return
        key = self._normalize_project_path(project_path)
        self._index = [
            d for d in self._index
            if self._normalize_project_path(
                d.get("metadata", {}).get("project", "")) != key
        ]

    def _save_to_cache(self, cache_path: Path, mtime_hashes: Dict[str, str]):
        """Salva l'indice su disco in JSON versionato, con atomic write.

        La cache e' PER-PROGETTO: salva solo i doc del progetto corrente,
        non l'intero indice in memoria (che puo' contenere altri progetti).
        """
        try:
            # BUGFIX: assicurati che la directory esista
            cache_path.parent.mkdir(parents=True, exist_ok=True)

            project_key = self._normalize_project_path(self._project_path)
            project_docs = [
                d for d in self._index
                if self._normalize_project_path(
                    d.get("metadata", {}).get("project", "")) == project_key
            ]

            cache_data = {
                "format": self._CACHE_FORMAT,
                "index": project_docs,
                "engine": self._engine_name,
                "dim": self._dim,
                "files_meta": mtime_hashes,
                "project": str(self._project_path),
            }

            # Atomic write
            tmp_path = cache_path.with_suffix(".tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(cache_data, f)
            tmp_path.rename(cache_path)

            print(f"[VectorStore] Cache salvata: {cache_path}")
        except Exception as e:
            print(f"[VectorStore] Cache save error: {e}")

    def _load_from_cache(self, cache_path: Path):
        """Carica l'indice dalla cache JSON e lo fonde con quello in memoria.

        Multi-progetto (2026-07-18, W9): la cache e' per-progetto; i doc cached
        SOSTITUISCONO quelli dello stesso progetto, i doc degli altri progetti
        in memoria restano. In caso di cache illeggibile/formato sbagliato si
        invalidano solo i doc del progetto corrente, non l'intero indice.
        """
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cache_data = json.load(f)

            if cache_data.get("format") != self._CACHE_FORMAT:
                print(f"[VectorStore] Cache format mismatch, indice ignorato")
                self._evict_project_docs(self._project_path)
                return

            cached_index = cache_data.get("index", [])
            self._engine_name = cache_data.get("engine", "unknown")
            self._dim = cache_data.get("dim", 0)
            self._project_path = Path(cache_data.get("project", "."))

            # Merge: eviction per-progetto + extend (come il ramo reindex di
            # index_project). I doc degli altri progetti non vengono toccati.
            self._evict_project_docs(self._project_path)
            self._index.extend(cached_index)

            # Gli engine corpus-dipendenti (tfidf/keyword) non possono riusare
            # gli embeddings cached: il vocabolario deriva dal corpus e la query
            # verrebbe codificata in uno spazio diverso. Re-fit deterministico
            # sui testi cached (stesso corpus -> stessi embeddings), costo
            # trascurabile rispetto al modello sentence-transformers. Il refit
            # gira su TUTTO l'indice fuso: embeddings di progetti diversi
            # restano nello stesso spazio vettoriale.
            if self._engine_name in ("sklearn-tfidf", "keyword") and self._index:
                texts = [doc.get("text", "") for doc in self._index]
                embeddings = self._encode(texts)
                for doc, emb in zip(self._index, embeddings):
                    doc["embedding"] = emb

            print(f"[VectorStore] Caricati {len(cached_index)} documenti dalla cache")
        except Exception as e:
            print(f"[VectorStore] Cache load error: {e}")
            self._evict_project_docs(self._project_path)

    @staticmethod
    def _normalize_project_path(p) -> str:
        """Normalizza un project path per il confronto del filtro progetto.

        Il filtro era string equality secca: caller che passano path relativi,
        con trailing slash o ".." (vs. il path stored all'indicizzazione)
        ottenevano [] SILENZIOSO — recall failure indistinguibile da "nessun
        file rilevante". Qui: abspath (risolve relativi e ".."), normpath
        (trailing slash, separatori), normcase (Windows). NON resolve symlink:
        costo per-call e rischio di mismatch coi path stored sono peggiori
        del caso residuo (progetto indicizzato e cercato via symlink diversi).
        """
        try:
            return os.path.normcase(os.path.normpath(os.path.abspath(str(p))))
        except Exception:
            return str(p)

    def search_semantic(
        self, 
        query: str, 
        project_path: str = None, 
        top_k: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Ricerca semantica nel progetto.

        Args:
            query: query di ricerca
            project_path: filtra per progetto specifico
            top_k: numero di risultati
        """
        if not self._index:
            print("[VectorStore] Indice vuoto, nessun risultato")
            return []

        # Codifica query
        query_embedding = self._encode([query])[0]

        # Calcola similarità coseno
        results = []
        wanted_project = self._normalize_project_path(project_path) if project_path else None
        for doc in self._index:
            # Filtra per progetto se specificato (path normalizzati su entrambi
            # i lati: trailing slash / ".." / relativi non devono azzerare il recall)
            if wanted_project is not None:
                doc_project = doc["metadata"].get("project", "")
                if self._normalize_project_path(doc_project) != wanted_project:
                    continue

            score = self._cosine_similarity(query_embedding, doc["embedding"])
            results.append({
                "text": doc["text"],
                "score": score,
                "metadata": doc["metadata"],
            })

        # Ordina per score decrescente
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]

    def _cosine_similarity(self, a: List[float], b: List[float]) -> float:
        """Calcola similarità coseno tra due vettori."""
        import math

        # Vettori di spazi diversi (es. engine cambiato tra cache e query)
        # non sono confrontabili: zip troncherebbe SILENZIOSAMENTE al piu'
        # corto producendo score spazzatura.
        if len(a) != len(b):
            return 0.0

        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))

        if norm_a == 0 or norm_b == 0:
            return 0.0

        return dot / (norm_a * norm_b)

    def clear(self):
        """Pulisce l'indice in memoria."""
        self._index = []
        self._embedding_engine = None
        self._engine_name = None


# ============================================================
# TEST E2E INTEGRATO
# ============================================================

def _run_e2e_test():
    """Test E2E del vector store (no server, no GPU, no rete)."""
    import tempfile
    import shutil
    import time

    print("=" * 60)
    print("VectorStore E2E Test")
    print("=" * 60)

    # Crea progetto fittizio
    tmpdir = tempfile.mkdtemp(prefix="devin_test_")
    project_path = Path(tmpdir)

    try:
        # Crea file di test — contenuto più ricco per TF-IDF
        (project_path / "calc.py").write_text("""
def add(a, b):
    \"\"\"Add two numbers together and return the sum.\"\"\"
    return a + b

def sum_list(numbers):
    \"\"\"Calculate the total sum of a list of numbers.\"\"\"
    result = 0
    for n in numbers:
        result = result + n
    return result
""")
        (project_path / "readme.md").write_text("""
# Calculator Project
This is a simple calculator for basic arithmetic operations.
You can add numbers, subtract them, and perform calculations.
""")
        (project_path / "utils.py").write_text("""
import os

def get_env(key):
    \"\"\"Get environment variable value.\"\"\"
    return os.getenv(key, "")
""")

        files = []
        for f in project_path.rglob("*"):
            if f.is_file() and f.suffix in (".py", ".md"):
                files.append({
                    "path": str(f),
                    "content": f.read_text(),
                })

        # Test 1: Indicizzazione
        print("\n[Test 1] Indicizzazione...")
        vs = VectorStore()
        cache_path = project_path / ".devin_cache" / "semantic_index.json"
        vs.index_project(str(project_path), files, cache_path=cache_path)
        assert len(vs._index) == 3, f"Atteso 3 documenti, trovati {len(vs._index)}"
        print("  PASS: 3 documenti indicizzati")

        # Test 2: Ricerca semantica — query in inglese per TF-IDF
        print("\n[Test 2] Ricerca semantica...")
        results = vs.search_semantic("add numbers sum total", project_path=str(project_path), top_k=2)
        assert len(results) > 0, "Nessun risultato trovato"

        # Verifica che calc.py sia nei top risultati
        calc_found = any("calc.py" in r["metadata"]["path"] for r in results)
        assert calc_found, f"calc.py non trovato nei top-{len(results)} risultati: {[r['metadata']['path'] for r in results]}"
        print(f"  PASS: calc.py trovato nei top-{len(results)} risultati (scores: {[round(r['score'], 3) for r in results]})")

        # Test 3: Cache persistente
        print("\n[Test 3] Cache persistente...")
        vs2 = VectorStore()
        vs2.index_project(str(project_path), files, cache_path=cache_path)
        assert len(vs2._index) == 3, "Cache non caricata correttamente"
        print("  PASS: Cache caricata correttamente")

        # Test 4: Re-indicizzazione condizionale
        print("\n[Test 4] Re-indicizzazione condizionale...")
        time.sleep(0.1)
        (project_path / "calc.py").write_text("""
def add(a, b):
    return a + b

def multiply(a, b):
    return a * b
""")
        files_updated = []
        for f in project_path.rglob("*"):
            if f.is_file() and f.suffix in (".py", ".md"):
                files_updated.append({
                    "path": str(f),
                    "content": f.read_text(),
                })

        vs3 = VectorStore()
        vs3.index_project(str(project_path), files_updated, cache_path=cache_path)
        # Dovrebbe aver re-indicizzato perché calc.py è cambiato
        assert len(vs3._index) == 3, "Re-indicizzazione fallita"
        print("  PASS: Re-indicizzazione avvenuta su file modificato")

        print("\n" + "=" * 60)
        print("TUTTI I TEST PASSATI!")
        print("=" * 60)
        return True

    except Exception as e:
        print(f"\n[FAIL] Test fallito: {e}")
        import traceback
        traceback.print_exc()
        return False

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    _run_e2e_test()
