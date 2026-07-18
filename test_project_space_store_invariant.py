"""Pin delle invarianti ProjectSpace <-> VectorStore (sweep finale 2026-07-18).

Motivazione: bug live 27c4697 — l'orchestratore indicizzava su uno store e
recuperava da un ALTRO, quindi la ricerca tornava sempre vuota. Qui si
pinna che, per OGNI concern (knowledge curata e file di progetto), index e
search passano dalla STESSA istanza lazy di VectorStore tenuta dal
ProjectSpace (project_space.py L358-370 e L486-497), e che
`fast_app._project_space_for` restituisce UN ProjectSpace cached per
project path (fast_app.py L495-499) — cosi' upload/delete e chat condividono
lo stato indice sulla stessa istanza.

Nessun mismatch trovato nella lettura del sorgente: entrambi i concern
usano `self._vector_store` / `self._files_vector_store` per index_project E
search_semantic nella stessa chiamata. Questi test lo pinnano a livello
comportamentale. Nessuna sorgente toccata.
"""

from pathlib import Path

import pytest
from fastapi import HTTPException

from devin.core.project_space import ProjectSpace
from devin.ui import fast_app


@pytest.fixture(autouse=True)
def _isolate_project_space_cache():
    """Isola la cache globale `_project_spaces` e le allowed roots tra i test
    (stato di modulo di fast_app, come _training_jobs per il router)."""
    saved_spaces = dict(fast_app._project_spaces)
    saved_roots = set(fast_app._ALLOWED_ROOTS)
    fast_app._project_spaces.clear()
    yield
    fast_app._project_spaces.clear()
    fast_app._project_spaces.update(saved_spaces)
    fast_app._ALLOWED_ROOTS.clear()
    fast_app._ALLOWED_ROOTS.update(saved_roots)


@pytest.fixture
def allowed_project(tmp_path):
    """Progetto reale registrato come allowed root (come fa il picker UI)."""
    assert fast_app._register_allowed_root(str(tmp_path)) is True
    return tmp_path


# ---------------------------------------------------------------------------
# 1. Coerenza index/search: knowledge curata
# ---------------------------------------------------------------------------

def test_knowledge_index_and_search_share_one_store(tmp_path):
    space = ProjectSpace(str(tmp_path))
    space.add_knowledge(
        "notes.md",
        b"Il motore zanzibar coordina la indicizzazione semantica del progetto.")

    found = space.retrieve_context("motore zanzibar")
    assert "zanzibar" in found  # indicizzato E ritrovato: stesso store

    # pin identita': l'istanza lazy e' UNA per concern e viene riusata
    store = space._vector_store
    assert store is not None
    again = space.retrieve_context("zanzibar")
    assert "zanzibar" in again
    assert space._vector_store is store
    # il concern "files" resta un'istanza SEPARATA (mai creata qui)
    assert space._files_vector_store is None

    # mutazione: add_knowledge invalida indice e istanza sulla STESSA space,
    # quindi la retrieve successiva re-indicizza e vede il contenuto nuovo
    # (la classe di bug di 27c4697: index e search su store diversi)
    space.add_knowledge(
        "protocol.md",
        b"Il protocollo gorgonzola definisce il handshake tra i moduli.")
    out = space.retrieve_context("protocollo gorgonzola")
    assert "gorgonzola" in out
    assert space._vector_store is not store  # istanza ricreata dopo invalidate


def test_files_index_and_search_share_one_store(tmp_path):
    (tmp_path / "engine.py").write_text(
        "def zanzibar_engine():\n    return 'semantic coordination'\n")
    space = ProjectSpace(str(tmp_path))

    found = space.retrieve_from_files("zanzibar_engine")
    assert "zanzibar_engine" in found

    files_store = space._files_vector_store
    assert files_store is not None
    again = space.retrieve_from_files("zanzibar_engine")
    assert "zanzibar_engine" in again
    assert space._files_vector_store is files_store
    # il concern "knowledge" resta un'istanza SEPARATA (mai creata qui)
    assert space._vector_store is None

    # nessuna cross-contaminazione: i due concern sullo stesso ProjectSpace
    # usano due store distinti, ognuno stabile nel tempo
    space.add_knowledge("notes.md", b"il motore zanzibar nei documenti curati")
    knowledge_hit = space.retrieve_context("zanzibar")
    assert "zanzibar" in knowledge_hit
    assert space._vector_store is not None
    assert space._vector_store is not space._files_vector_store
    assert space._files_vector_store is files_store


# ---------------------------------------------------------------------------
# 2. `_project_space_for`: identita' cached per project path
# ---------------------------------------------------------------------------

def test_project_space_for_identity_and_path_normalization(allowed_project):
    path = str(allowed_project)

    first = fast_app._project_space_for(path)
    second = fast_app._project_space_for(path)
    assert first is second  # stessa istanza cached per la stessa chiave
    assert first.project_path == allowed_project.resolve()

    # trailing slash: `_validated_project_path` -> `_safe_under_allowed` ->
    # Path(...).expanduser().resolve() normalizza, quindi la chiave e' la
    # STESSA e non nasce un secondo ProjectSpace (che avrebbe un suo indice).
    trailing = fast_app._project_space_for(path + "/")
    assert trailing is first
    # NOTA (case): su Windows il FS stesso normalizza il case; i test girano
    # su Linux/WSL dove resolve() NON unisce "/Path/X" e "/path/x" — ma un
    # path con case diverso non esisterebbe su disco, quindi non e' pinnabile
    # qui in modo portabile. La normalizzazione rilevante (resolve completo)
    # e' pinnata sopra via trailing slash.

    # la cache contiene UNA sola entry per il progetto
    keys = [k for k in fast_app._project_spaces
            if Path(k) == allowed_project.resolve()]
    assert len(keys) == 1


def test_project_space_for_empty_path_uses_general_chat_key():
    space = fast_app._project_space_for("")
    expected = str(Path(fast_app.GENERAL_CHAT_PROJECT_KEY).expanduser().resolve())
    assert str(space.project_path) == expected
    assert fast_app._project_space_for("") is space  # cached anche lui


def test_project_space_for_rejects_disallowed_path():
    # path fuori dalle allowed roots: 403, nessuna entry in cache
    # (l'identita' cached non deve mai agganciare path arbitrari)
    with pytest.raises(HTTPException) as excinfo:
        fast_app._project_space_for("/etc")
    assert excinfo.value.status_code == 403
    assert fast_app._project_spaces == {}
