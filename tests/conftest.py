"""Keep the suite out of the real ~/.td-atlas.

Most tests already point TD_ATLAS_HOME at a tmp directory, but not all of
them did, and the call journal writes from inside `BridgeClient.call` — a
chokepoint the mocked-transport tests exercise dozens of times. Without this
the suite would append to the developer's own journal.

Modules that reach a *real* running bridge are exempt: they skip when there
is none, so they need the real token and the real instance registry, both of
which live in the home this fixture would otherwise move. Redirecting them
turned seven passing live checks into skips — a silent loss of coverage, so
the exemption is explicit. They are recognised by the helper they all import
(`live_network`), which is also the thing that does the skipping, so a new
live module joins the list by importing it rather than by being remembered
here. Those modules do append to the developer's own journal, in the same way
they already read their own registry.

The exemption is from the home, not from the expansion cache. The cache is
what grew: the owner's held 1246 expansions on 2026-09-24, 216 `checker` and
86 `difftest` among them, and `test_live_text_diff` expands a freshly saved
`difftest.tox` on every live run. So whenever the real home is in force — an
exempt module, or a module-scoped fixture, which runs before any
function-scoped one — expansions go to a cache of this session's instead.
`tests/test_home_isolation.py` holds both halves.

The home is set through a private `pytest.MonkeyPatch`, not the test's own
`monkeypatch`: a test calling `monkeypatch.undo()` used to undo the isolation
with it and write the rest of itself into the real home (test_journal did,
one line per run). A test that wants its own home still wins either way:
`monkeypatch.setenv` inside the test runs after this fixture.
"""

from __future__ import annotations

import importlib
import os

import pytest

_LIVE_HELPER = "live_network"

# Every module that binds `cache_dir` by name. By module path, because
# `td_atlas.project` re-exports a function called `expand` that shadows the
# module of that name.
_CACHE_USERS = (
    "td_atlas.project.expand",
    "td_atlas.project.rebuild",
    "td_atlas.project.release",
)


def _reaches_a_real_bridge(module) -> bool:
    if module is None:
        return False
    name = getattr(module, "__name__", "")
    if name == _LIVE_HELPER or name.endswith("." + _LIVE_HELPER):
        return True
    return getattr(module, "ln", None) is not None or (
        getattr(module, _LIVE_HELPER, None) is not None
    )


@pytest.fixture(autouse=True, scope="session")
def _expansions_out_of_the_real_cache(tmp_path_factory):
    expand_mod = importlib.import_module("td_atlas.project.expand")
    real_cache_dir = expand_mod.cache_dir
    session_cache = tmp_path_factory.mktemp("td-atlas-cache")

    def cache_dir():
        # A home the test chose owns its cache; test_expand_cache reads
        # <home>/cache directly.
        if os.environ.get("TD_ATLAS_HOME"):
            return real_cache_dir()
        return session_cache

    patch = pytest.MonkeyPatch()
    for name in _CACHE_USERS:
        patch.setattr(importlib.import_module(name), "cache_dir", cache_dir)
    yield
    patch.undo()


@pytest.fixture(autouse=True)
def _isolated_td_atlas_home(request, tmp_path_factory):
    if _reaches_a_real_bridge(request.module):
        yield
        return
    home = tmp_path_factory.mktemp("td-atlas-home")
    patch = pytest.MonkeyPatch()
    patch.setenv("TD_ATLAS_HOME", str(home))
    yield
    patch.undo()
