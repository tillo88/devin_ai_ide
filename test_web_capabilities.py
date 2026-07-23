"""Test del selettore/estrattore delle capacita' web (offline, deterministico)."""

from __future__ import annotations

from devin.ai.web_capabilities import (
    CAP_DOCS_FOR_IMPORTS,
    CAP_ERROR_REFERENCE,
    CAP_TASK_DOCS,
    detect_language,
    error_reference_query,
    extract_imports,
    select_web_capabilities,
)


# --- detect_language ------------------------------------------------------

def test_detect_language_per_estensione():
    assert detect_language(["a.py", "b.py", "c.js"]) == "python"
    assert detect_language(["a.ts", "b.tsx"]) == "typescript"
    assert detect_language(["main.rs"]) == "rust"
    assert detect_language([]) == "python"  # default


# --- extract_imports ------------------------------------------------------

def test_extract_imports_python_esclude_stdlib_e_relativi():
    code = (
        "import os\n"
        "import requests\n"
        "from fastapi import FastAPI\n"
        "from . import local_helper\n"
        "import json, re\n"
        "from mypkg.sub import thing\n"
    )
    mods = extract_imports(code, "python")
    assert "requests" in mods
    assert "fastapi" in mods
    assert "mypkg" in mods
    assert "os" not in mods and "json" not in mods and "re" not in mods  # stdlib
    assert "local_helper" not in mods  # relativo


def test_extract_imports_js_esclude_builtin_e_relativi():
    code = (
        "import express from 'express';\n"
        "import { x } from './local';\n"
        "const fs = require('fs');\n"
        "import foo from '@scope/pkg';\n"
        "const lodash = require('lodash/merge');\n"
    )
    mods = extract_imports(code, "javascript")
    assert "express" in mods
    assert "@scope/pkg" in mods
    assert "lodash" in mods       # root del path lodash/merge
    assert "fs" not in mods       # builtin
    assert "./local" not in mods  # relativo


# --- error_reference_query (fix hardcoded 'python') -----------------------

def test_error_reference_query_consapevole_del_linguaggio():
    q = error_reference_query("TypeError: undefined is not a function\n stack...", "javascript")
    assert q.startswith("javascript ")
    assert "TypeError" in q
    assert "python" not in q  # non piu' hardcoded


def test_error_reference_query_default_python():
    assert error_reference_query("ModuleNotFoundError: no module x").startswith("python ")


# --- select_web_capabilities (il cervello del dispatch) -------------------

def test_select_disabilitato_o_budget_zero():
    assert select_web_capabilities({"web_enabled": False, "imports": {"requests"}}) == []
    assert select_web_capabilities({"web_enabled": True, "budget_left": 0, "imports": {"requests"}}) == []


def test_select_import_attiva_docs():
    caps = select_web_capabilities({"web_enabled": True, "budget_left": 2, "imports": {"requests"}})
    assert caps == [CAP_DOCS_FOR_IMPORTS]


def test_select_errore_cercabile_attiva_reference():
    caps = select_web_capabilities({
        "web_enabled": True, "budget_left": 2,
        "error": "ModuleNotFoundError", "error_searchable": True,
    })
    assert caps == [CAP_ERROR_REFERENCE]


def test_select_docs_e_reference_insieme_ma_bounded():
    state = {"web_enabled": True, "budget_left": 2, "imports": {"a"},
             "error": "boom", "error_searchable": True}
    assert select_web_capabilities(state) == [CAP_DOCS_FOR_IMPORTS, CAP_ERROR_REFERENCE]
    # budget 1 -> solo la prima
    state["budget_left"] = 1
    assert select_web_capabilities(state) == [CAP_DOCS_FOR_IMPORTS]


def test_select_fallback_task_keywords():
    caps = select_web_capabilities({
        "web_enabled": True, "budget_left": 1,
        "task_has_api_keywords": True,
    })
    assert caps == [CAP_TASK_DOCS]
