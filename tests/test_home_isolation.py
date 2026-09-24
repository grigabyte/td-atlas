"""The suite keeps out of the developer's own ~/.td-atlas.

The owner's cache held 1246 expansions, 216 of them named `checker` and 86
`difftest` — test fixtures. Two routes past `conftest.py` were found on
2026-09-24 by running the whole default suite with `Path.home()` pointed at an
empty scratch directory and looking at what appeared there:

- A test's own `monkeypatch.undo()` (test_journal's "a write that works again")
  undid the isolation too, because the fixture had set TD_ATLAS_HOME through
  the same `monkeypatch` object. The rest of that test wrote one journal line
  into the real home, on every run of the suite.
- Modules that reach a live bridge are exempt from the redirect, since they
  need the real token and registry, and module-scoped fixtures run before any
  function-scoped one. Both read and write with the real home in force, and
  `test_live_text_diff` expands a freshly saved `difftest.tox` on every live
  run — a new cache key each time, because the key covers path and mtime.

The checks below are the two routes, closed.
"""

from __future__ import annotations

import importlib
import os
from pathlib import Path

from td_atlas import config

# By module path: `td_atlas.project` re-exports a function named `expand`,
# which shadows the module of that name as an attribute of the package.
expand = importlib.import_module("td_atlas.project.expand")
rebuild = importlib.import_module("td_atlas.project.rebuild")
release = importlib.import_module("td_atlas.project.release")

REAL_HOME = Path.home() / ".td-atlas"


def _outside_real_home(path: Path) -> bool:
    try:
        path.resolve().relative_to(REAL_HOME.resolve())
    except ValueError:
        return True
    return False


def test_a_tests_own_undo_does_not_undo_the_isolation(monkeypatch):
    monkeypatch.setenv("SOMETHING_ELSE", "1")
    monkeypatch.undo()
    assert os.environ.get("TD_ATLAS_HOME")
    assert _outside_real_home(config.home())


def test_expansions_stay_out_of_the_real_cache_even_with_the_real_home(monkeypatch):
    # What an exempt live module, or a module-scoped fixture, runs under.
    monkeypatch.delenv("TD_ATLAS_HOME", raising=False)
    assert config.home() == REAL_HOME
    for module in (expand, rebuild, release):
        assert _outside_real_home(module.cache_dir()), module.__name__


def test_a_chosen_home_still_owns_its_cache(monkeypatch, tmp_path):
    # test_expand_cache and friends inspect <home>/cache directly.
    monkeypatch.setenv("TD_ATLAS_HOME", str(tmp_path))
    assert expand.cache_dir() == tmp_path / "cache"
