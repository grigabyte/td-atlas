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

A test that wants its own home still wins either way: `monkeypatch.setenv`
inside the test runs after this fixture.
"""

from __future__ import annotations

import os

import pytest

_LIVE_HELPER = "live_network"


def _reaches_a_real_bridge(module) -> bool:
    if module is None:
        return False
    name = getattr(module, "__name__", "")
    if name == _LIVE_HELPER or name.endswith("." + _LIVE_HELPER):
        return True
    return getattr(module, "ln", None) is not None or (
        getattr(module, _LIVE_HELPER, None) is not None
    )


@pytest.fixture(autouse=True)
def _isolated_td_atlas_home(request, tmp_path_factory, monkeypatch):
    if _reaches_a_real_bridge(request.module):
        return
    home = tmp_path_factory.mktemp("td-atlas-home")
    monkeypatch.setenv("TD_ATLAS_HOME", str(home))
    os.environ["TD_ATLAS_HOME"] = str(home)
