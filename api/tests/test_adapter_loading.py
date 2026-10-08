"""Adapter selection must survive missing credentials and unrelated adapter failures."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

from app.scraping.registry import BANKS


def isolated_python(code: str, tmp_path: Path) -> None:
    """Use a fresh interpreter so earlier test imports cannot hide eager imports."""
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith("DB_") and key not in {"GEMINI_API_KEY", "ENABLE_AI_ENRICHMENT"}
    }
    env["ENV"] = "adapter-import-test"
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


_IMPORT_BLOCKER = """
import importlib.abc
import sys
import types

class MissingDependency(importlib.abc.MetaPathFinder):
    def __init__(self, prefixes):
        self.prefixes = prefixes

    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == p or fullname.startswith(p + ".") for p in self.prefixes):
            raise ImportError("Unavailable dependency: " + fullname)
        return None
"""


def test_registry_and_package_imports_need_no_database_or_ai(tmp_path):
    isolated_python(_IMPORT_BLOCKER + """
sys.meta_path.insert(0, MissingDependency((
    "app.core", "app.database", "sqlalchemy", "requests",
    "app.scraping.experimental", "app.scraping.clients.gemini_client",
)))
from app.scraping import factories
from app.scraping import clients, parsers, sources, prompts
from app.scraping.registry import ADAPTER_VERSION, BANKS, BankSlug
from typing import get_args

assert BANKS == get_args(BankSlug)
assert ADAPTER_VERSION
assert "app.core.config" not in sys.modules
assert not any(name.endswith("_html_parser") or name.endswith("_html_source") for name in sys.modules)
for factory in (factories.get_source, factories.get_parser):
    try:
        factory("unsupported-bank")
    except ValueError:
        pass
    else:
        raise AssertionError("Unsupported bank was accepted")
""", tmp_path)


@pytest.mark.parametrize("bank", BANKS)
def test_selected_bank_does_not_load_broken_other_adapters(bank, tmp_path):
    other_adapters = tuple(
        f"app.scraping.{kind}.{other}_html_{suffix}"
        for other in BANKS if other != bank
        for kind, suffix in (("parsers", "parser"), ("sources", "source"))
    )
    isolated_python(_IMPORT_BLOCKER + f"""
blocked = {other_adapters!r} + (
    "app.database", "app.scraping.experimental", "app.scraping.clients.gemini_client",
)
sys.meta_path.insert(0, MissingDependency(blocked))
from app.scraping.factories import get_parser, get_source

parser = get_parser({bank!r})
assert type(parser).__module__ == "app.scraping.parsers.{bank}_html_parser"
assert "app.core.config" not in sys.modules

# Inject the only runtime setting source construction needs; no real secrets or network.
config = types.ModuleType("app.core.config")
config.settings = types.SimpleNamespace(SCRAPING_MAX_DETAILS_PER_BANK=17)
sys.modules["app.core.config"] = config
http_client = object()
source = get_source({bank!r}, http_client=http_client)
assert type(source).__module__ == "app.scraping.sources.{bank}_html_source"
assert source._http_client is http_client
assert "app.scraping.clients.gemini_client" not in sys.modules
assert not any(name.startswith("app.scraping.experimental") for name in sys.modules)
""", tmp_path)


def test_experimental_factory_requires_opt_in_before_loading_dependencies(tmp_path):
    isolated_python(_IMPORT_BLOCKER + """
sys.meta_path.insert(0, MissingDependency((
    "app.scraping.experimental", "app.scraping.clients.gemini_client",
)))
config = types.ModuleType("app.core.config")
config.settings = types.SimpleNamespace(ENABLE_AI_ENRICHMENT=False)
sys.modules["app.core.config"] = config
from app.scraping.factories import get_parser, get_source

for factory in (get_source, get_parser):
    try:
        factory("ueno-pdf")
    except ValueError as exc:
        assert "ENABLE_AI_ENRICHMENT" in str(exc)
    else:
        raise AssertionError("Experimental adapter ran without opt-in")
assert "app.scraping.clients.gemini_client" not in sys.modules
assert not any(name.startswith("app.scraping.experimental") for name in sys.modules)
""", tmp_path)


def test_explicit_experimental_adapter_preserves_compatibility_and_model(tmp_path):
    isolated_python(_IMPORT_BLOCKER + """
config = types.ModuleType("app.core.config")
config.settings = types.SimpleNamespace(
    ENABLE_AI_ENRICHMENT=True, GEMINI_API_KEY="test-key", AI_MODEL="test-model",
)
sys.modules["app.core.config"] = config
from app.scraping.factories import get_parser, get_source
from app.scraping.parsers.ueno_parser import UenoParser as PreviousParser
from app.scraping.sources.ueno_source import UenoSource as PreviousSource
from app.scraping.prompts.ueno_page_structuring_prompt import build_ueno_page_structuring_prompt
from app.scraping.experimental.prompts.ueno_page_structuring_prompt import build_ueno_page_structuring_prompt as moved_prompt

parser = get_parser("ueno-pdf")
source = get_source("ueno-pdf", http_client=object())
assert type(parser) is PreviousParser
assert type(source) is PreviousSource
assert type(parser).__module__ == "app.scraping.experimental.ueno_parser"
assert parser._gemini_client._model == "test-model"
assert build_ueno_page_structuring_prompt is moved_prompt
""", tmp_path)
