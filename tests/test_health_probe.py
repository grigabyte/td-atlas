"""`health_probe`, the bridge half: which quiet operators had work to do.

Fake operators stand in for TouchDesigner's: `cook()` cooks one only when it
is dirty, and cooks its input first, which is the order trap the method has
to survive — measured live, cooking a Level TOP cooked the noise above it.
"""

from __future__ import annotations

import pytest

from td_atlas.component import handler


class FakeOp:
    def __init__(self, path, dirty=False, cooks=5, family="TOP",
                 optype="noiseTOP", upstream=None):
        self.path = path
        self.dirty = dirty
        self.totalCooks = cooks
        self.family = family
        self.OPType = optype
        self.upstream = upstream

    def cook(self, force=False):
        if self.upstream is not None:
            self.upstream.cook()
        if self.dirty or force:
            self.totalCooks += 1
            self.dirty = False


@pytest.fixture
def ops(monkeypatch):
    table = {}
    monkeypatch.setattr(handler, "op", table.get, raising=False)
    return table


def test_an_upstream_cooked_by_its_consumer_still_counts_as_cooked(ops):
    noise = FakeOp("/p/noise", dirty=True)
    level = FakeOp("/p/level", dirty=True, upstream=noise, optype="levelTOP")
    ops.update({o.path: o for o in (noise, level)})
    # The consumer first: walking it cooks the noise before the noise's turn.
    answer = handler.m_health_probe({"paths": ["/p/level", "/p/noise"]})
    assert sorted(answer["cooked"]) == ["/p/level", "/p/noise"]
    assert answer["quiet"] == []


def test_static_never_cooked_outputs_and_containers_are_kept_apart(ops):
    static = FakeOp("/p/ramp")
    fresh = FakeOp("/p/new", dirty=True, cooks=0)
    rec = FakeOp("/p/rec", dirty=True, optype="moviefileoutTOP")
    box = FakeOp("/p/box", family="COMP", optype="baseCOMP")
    ops.update({o.path: o for o in (static, fresh, rec, box)})
    answer = handler.m_health_probe(
        {"paths": ["/p/ramp", "/p/new", "/p/rec", "/p/box", "/p/gone"]})
    assert answer["quiet"] == ["/p/ramp"]
    assert answer["never"] == ["/p/new"]
    assert answer["skipped"] == ["/p/rec", "/p/box"]
    assert answer["missing"] == ["/p/gone"]
    # Neither the recorder nor the never-cooked operator was asked to cook.
    assert rec.totalCooks == 5 and fresh.totalCooks == 0


def test_the_budget_leaves_the_rest_unprobed(ops, monkeypatch):
    monkeypatch.setattr(handler, "_PROBE_BUDGET_S", -1.0)
    ops.update({"/p/a": FakeOp("/p/a", dirty=True)})
    answer = handler.m_health_probe({"paths": ["/p/a"]})
    assert answer["unprobed"] == ["/p/a"] and answer["cooked"] == []
