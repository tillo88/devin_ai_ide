"""
Regression tests for devin/core/context_engine.py and devin/core/repo_map.py.

Contracti pinned:
- collect_project_files: solo .py, directory escluse potate (workspace/,
  .devin/, venv/...) — regression del bug "residui sandbox confondono il Coder".
- build(): budget max_total_chars rispettato anche con repo map in testa;
  repo map presente solo con >1 file; progetto vuoto -> "".
- repo_map: firme top-level via ast, file con sintassi rotta non fatali,
  non-.py elencati per nome, troncatura marcata.
"""
import textwrap

from devin.core.context_engine import ContextEngine
from devin.core.repo_map import build_repo_map_from_files


def _write(path, name, body):
    f = path / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(textwrap.dedent(body))


# ------------------------------------------------------------- collection

class TestCollectProjectFiles:
    def test_only_python_collected(self, tmp_path):
        _write(tmp_path, "a.py", "x = 1\n")
        _write(tmp_path, "notes.txt", "non codice\n")
        engine = ContextEngine()
        engine.project_path = str(tmp_path)
        files = engine.collect_project_files()
        assert [f["rel_path"] for f in files] == ["a.py"]

    def test_excluded_dirs_pruned(self, tmp_path):
        """workspace/, .devin/, venv/ etc. non devono MAI entrare nel contesto:
        e' il fix per i residui sandbox che facevano patchare il file sbagliato."""
        _write(tmp_path, "real.py", "x = 1\n")
        for d in ("workspace/sandbox", ".devin/knowledge", "venv/lib",
                  "__pycache__", ".devin_state", "logs"):
            _write(tmp_path, f"{d}/residue.py", "FAKE = True\n")
        engine = ContextEngine()
        engine.project_path = str(tmp_path)
        files = engine.collect_project_files()
        assert [f["rel_path"] for f in files] == ["real.py"]


# ------------------------------------------------------------------ build

class TestBuild:
    def test_empty_project_returns_empty_string(self, tmp_path):
        assert ContextEngine().build(str(tmp_path)) == ""

    def test_budget_respected_with_many_files(self, tmp_path):
        for i in range(10):
            _write(tmp_path, f"mod_{i}.py", f"# {'padding ' * 400}\nx_{i} = 1\n")
        engine = ContextEngine(max_chars=3000)
        result = engine.build(str(tmp_path))
        assert 0 < len(result) <= 3000

    def test_repo_map_included_with_multiple_files(self, tmp_path):
        _write(tmp_path, "a.py", "def alpha(x):\n    return x\n")
        _write(tmp_path, "b.py", "class Beta:\n    def run(self):\n        pass\n")
        result = ContextEngine(max_chars=20000).build(str(tmp_path))
        assert "# REPO MAP" in result
        assert "def alpha(x)" in result
        assert "class Beta(run)" in result

    def test_no_repo_map_for_single_file(self, tmp_path):
        _write(tmp_path, "only.py", "def solo():\n    pass\n")
        result = ContextEngine(max_chars=20000).build(str(tmp_path))
        assert "# REPO MAP" not in result
        assert "def solo" in result

    def test_query_relevance_ranks_matching_file_first(self, tmp_path):
        _write(tmp_path, "unrelated.py", "z = 1\n")
        _write(tmp_path, "calculator.py", "def add(a, b):\n    return a + b\n")
        result = ContextEngine(max_chars=20000).build(str(tmp_path), query="fix calculator add")
        assert result.index("# FILE: calculator.py") < result.index("# FILE: unrelated.py")

    def test_prioritize_prepends_semantic(self):
        engine = ContextEngine()
        assert engine.prioritize("BASE", "SEM", "q") == "SEM\n\nBASE"
        assert engine.prioritize("BASE", "", "q") == "BASE"
        assert engine.prioritize("BASE", None, "q") == "BASE"


# --------------------------------------------------------------- repo map

class TestRepoMap:
    def test_signatures_and_classes(self):
        files = [{"rel_path": "m.py", "content": textwrap.dedent("""\
            async def fetch(url, timeout=10):
                pass
            class Client:
                def get(self): pass
                def post(self): pass
            def helper():
                pass
            """)}]
        out = build_repo_map_from_files(files)
        assert "async def fetch(url, timeout=10)" in out
        assert "class Client(get, post)" in out
        assert "def helper()" in out

    def test_broken_syntax_not_fatal(self):
        files = [{"rel_path": "broken.py", "content": "def oops(:\n"}]
        out = build_repo_map_from_files(files)
        assert "broken.py" in out
        assert "sintassi non parsabile" in out

    def test_non_python_listed_by_name(self):
        files = [{"rel_path": "data.csv", "content": "a,b\n"}]
        out = build_repo_map_from_files(files)
        assert "altri file: data.csv" in out

    def test_truncation_marked(self):
        files = [{"rel_path": f"m{i}.py", "content": f"def f{i}():\n    pass\n"}
                 for i in range(50)]
        out = build_repo_map_from_files(files, max_chars=200)
        assert "repo map troncata" in out

    def test_empty_files_returns_empty(self):
        assert build_repo_map_from_files([]) == ""
