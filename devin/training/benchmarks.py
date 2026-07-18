"""Small built-in benchmark seeds for DEVIN training mode.

These are deliberately tiny and local. Public benchmark integrations such as
MBPP, HumanEval, BigCodeBench, LiveCodeBench and SWE-bench should be imported
through explicit adapters (see devin/training/adapters.py), not silently
downloaded.

GOLD TESTS (2026-07-15): ogni caso porta in `gold_tests` uno o piu' file di
test CANONICI scritti da noi. Il training runner li inietta nel sandbox e il
quality gate li esegue insieme ai test del modello. Senza gold tests il
modello "si corregge i compiti da solo": puo' scrivere test deboli e passare.
I gold test usano discovery dinamica della funzione (nome file libero), cosi'
valgono anche se il modello nomina i moduli a modo suo. I prompt dichiarano
il CONTRATTO (nomi funzione/firme): un benchmark senza contratto non e'
verificabile a macchina.
"""

# Helper autonomo incluso in testa a ogni gold test: trova una funzione per
# nome in qualunque modulo top-level del sandbox (esclusi i file di test).
_GOLD_FINDER = '''"""Gold test DEVIN — iniettato dal training runner, NON scritto dal modello."""
import importlib.util
import pathlib


def _find_callable(name):
    here = pathlib.Path(__file__).parent
    me = pathlib.Path(__file__).name
    for path in sorted(here.glob("*.py")):
        if path.name == me or path.name.startswith("test") or path.name.endswith("_test.py"):
            continue
        spec = importlib.util.spec_from_file_location("gold_" + path.stem, path)
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception:
            continue
        fn = getattr(mod, name, None)
        if callable(fn):
            return fn
    return None
'''

_GOLD_ADD = _GOLD_FINDER + '''

def test_gold_add_exists_and_is_correct():
    add = _find_callable("add")
    assert add is not None, "nessuna funzione add(a, b) trovata nei moduli del progetto"
    assert add(2, 3) == 5
    assert add(-1, 1) == 0
    assert add(0, 0) == 0
    assert add(0.5, 0.25) == 0.75
'''

_GOLD_RANGE = _GOLD_FINDER + '''

def test_gold_count_up_to_excludes_endpoint():
    fn = _find_callable("count_up_to")
    assert fn is not None, "nessuna funzione count_up_to(n) trovata nei moduli del progetto"
    assert list(fn(3)) == [0, 1, 2], "endpoint incluso: off-by-one non corretto"
    assert list(fn(1)) == [0]
    assert list(fn(0)) == []
'''

_GOLD_STEAM = _GOLD_FINDER + '''

def test_gold_player_summaries_url_is_official():
    fn = _find_callable("player_summaries_url")
    assert fn is not None, "nessuna funzione player_summaries_url(api_key, steamid) trovata"
    url = fn("TESTKEY", "76561197960435530")
    assert isinstance(url, str) and url.startswith(
        "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/"
    ), f"endpoint non ufficiale: {url!r}"
    assert "TESTKEY" in url and "76561197960435530" in url
'''

BUILTIN_BENCHMARKS = {
    "devin-mini": {
        "name": "DEVIN Mini Bench",
        "description": "Small local coding-agent exercises for scaffolding, debugging, tests and memory safety.",
        "source": "local",
        "cases": [
            {
                "title": "Create tested add function",
                "kind": "code_generation",
                "prompt": (
                    "Create a tiny Python module exposing a function add(a, b) that returns "
                    "the sum, plus pytest tests (test_*.py) that prove it works."
                ),
                "expected_signals": ["file_created", "tests_pass"],
                "tags": ["python", "scaffold", "tests"],
                "gold_tests": {"test_gold_add.py": _GOLD_ADD},
            },
            {
                "title": "Fix off-by-one loop",
                "kind": "debugging",
                "prompt": (
                    "Create a Python module with a function count_up_to(n) that returns the "
                    "list of integers from 0 up to n EXCLUDED (a naive implementation often "
                    "includes the endpoint by mistake: avoid that off-by-one). Add pytest "
                    "tests covering n=0, n=1 and n=3, and explain the off-by-one in a comment."
                ),
                "expected_signals": ["tests_pass"],
                "tags": ["python", "debug", "off_by_one"],
                "gold_tests": {"test_gold_count_up_to.py": _GOLD_RANGE},
            },
            {
                "title": "Official API only",
                "kind": "source_discipline",
                "prompt": (
                    "Build a Steam Profile Checker MVP using only documented official Steam "
                    "Web API endpoints. Expose a function player_summaries_url(api_key, "
                    "steamid) returning the full GetPlayerSummaries v2 request URL, and add "
                    "tests or mocks (no real network calls in tests)."
                ),
                "expected_signals": ["no_invented_endpoint", "tests_or_mocks"],
                "tags": ["api", "official_sources", "steam"],
                # Finisce in case.metadata (seed_cases) e viene letta dal
                # validatore no_invented_endpoint: URL fuori da questi prefissi
                # = endpoint inventato = auto_failure. Fallback per i casi gia'
                # seedati senza metadata: allowlist di default per tag "steam"
                # in devin/training/validators.py.
                "allowed_url_prefixes": [
                    "https://api.steampowered.com/",
                    "http://api.steampowered.com/",
                    "https://steamcommunity.com/",
                    "https://partner.steam-api.com/",
                    "https://store.steampowered.com/api/",
                ],
                "gold_tests": {"test_gold_steam_url.py": _GOLD_STEAM},
            },
        ],
    },
    "benchmark-roadmap": {
        "name": "Public Benchmark Roadmap",
        "description": "References for future adapters; not downloaded automatically.",
        "source": "reference",
        "cases": [],
        "adapters": [
            {"id": "mbpp", "stage": "starter", "notes": "Entry-level Python tasks with tests. Import esplicito via devin/training/adapters.py."},
            {"id": "human-eval", "stage": "starter-eval", "notes": "Small Python function eval; requires sandboxing generated code."},
            {"id": "bigcodebench", "stage": "intermediate", "notes": "Practical code generation with richer library usage."},
            {"id": "livecodebench", "stage": "intermediate", "notes": "Contamination-aware code generation/self-repair benchmark."},
            {"id": "swe-bench-lite", "stage": "advanced", "notes": "Repo-level issue fixing; Docker-heavy."},
        ],
    },
}


def list_builtin_benchmarks():
    return [dict({"id": key}, **{k: v for k, v in value.items() if k != "cases"}) for key, value in BUILTIN_BENCHMARKS.items()]


def get_builtin_cases(benchmark_id: str):
    bench = BUILTIN_BENCHMARKS.get(benchmark_id)
    if not bench:
        return []
    return [dict(case) for case in bench.get("cases", [])]
