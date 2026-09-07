"""A network walk that was cut has to say so, on both sides of the bridge.

`m_network` slices a component's children at `_MAX_CHILDREN` and stops at a
total node budget; without a marker the caller reads the slice as the whole
network and builds against operators that are not in it. The reply carries the
counts and `td_network` prints them.

Nothing here needs TouchDesigner: the walk only touches attributes that a
plain object can carry.
"""

from __future__ import annotations

import pytest

from td_atlas.component import handler
from td_atlas.mcp import server


class FakeOp:
    """As much of an operator as `_op_summary` and `m_network` read."""

    def __init__(self, path, children=None, op_type="nullCHOP", family="CHOP"):
        self.path = path
        self.name = path.rsplit("/", 1)[-1]
        self.OPType = op_type
        self.family = family
        self.valid = True
        self.inputs = []
        self.children = children or []

    def errors(self, recurse=False):
        return ""

    def warnings(self, recurse=False):
        return ""


def _flat(count, parent="/project1"):
    return FakeOp(parent, [FakeOp(f"{parent}/n{i}") for i in range(count)],
                  op_type="containerCOMP", family="COMP")


def _chain(levels):
    """A single child per level, `levels` deep below /project1."""
    node = FakeOp(f"/project1{'/c' * levels}", [], "containerCOMP", "COMP")
    for level in range(levels - 1, -1, -1):
        node = FakeOp(f"/project1{'/c' * level}" if level else "/project1",
                      [node], "containerCOMP", "COMP")
    return node


@pytest.fixture
def resolve(monkeypatch):
    def install(target):
        monkeypatch.setattr(handler, "_resolve", lambda path: target)
        return target

    return install


def test_a_component_wider_than_the_child_cap_says_how_many_are_missing(resolve):
    resolve(_flat(handler._MAX_CHILDREN + 7))
    reply = handler.m_network({"path": "/project1"})

    assert len(reply["children"]) == handler._MAX_CHILDREN
    assert reply["truncated"] is True
    assert reply["hidden"] == 7
    assert reply["childrenHidden"] == 7


def test_a_component_within_the_caps_is_not_marked_truncated(resolve):
    resolve(_flat(3))
    reply = handler.m_network({"path": "/project1"})

    assert len(reply["children"]) == 3
    assert "truncated" not in reply
    assert "hidden" not in reply


def test_the_total_node_budget_stops_the_walk_and_is_reported(resolve, monkeypatch):
    monkeypatch.setattr(handler, "_MAX_NETWORK_NODES", 5)
    parent = FakeOp("/project1", [_flat(4, "/project1/a"), _flat(4, "/project1/b")],
                    "containerCOMP", "COMP")
    resolve(parent)

    reply = handler.m_network({"path": "/project1", "depth": 2})

    described = sum(1 + len(c.get("children", [])) for c in reply["children"])
    assert described == 5
    assert reply["truncated"] is True
    assert reply["hidden"] >= 1


def test_depth_is_clamped_and_the_clamp_is_named(resolve):
    resolve(_chain(handler._MAX_DEPTH + 3))
    reply = handler.m_network({"path": "/project1", "depth": 999})

    assert reply["depth"] == handler._MAX_DEPTH
    assert reply["depthLimited"] == 999

    def deepest(nodes, level=1):
        return max(
            (deepest(n["children"], level + 1) for n in nodes if n.get("children")),
            default=level,
        )

    assert deepest(reply["children"]) == handler._MAX_DEPTH


def test_depth_below_one_still_lists_the_direct_children(resolve):
    resolve(_flat(3))
    reply = handler.m_network({"path": "/project1", "depth": 0})

    assert reply["depth"] == 1
    assert len(reply["children"]) == 3


class FakeClient:
    version_warning = None
    ambiguity_warning = None

    def __init__(self, reply):
        self._reply = reply

    def network(self, path="/", depth=1, pars=False):
        return self._reply


def _render(monkeypatch, reply):
    monkeypatch.setattr(server, "bridge", lambda: FakeClient(reply))
    return server.td_network("/project1", depth=1)


def test_td_network_prints_the_cut_rather_than_hiding_it(monkeypatch):
    text = _render(
        monkeypatch,
        {
            "path": "/project1",
            "type": "containerCOMP",
            "depth": 1,
            "children": [
                {
                    "path": "/project1/a",
                    "name": "a",
                    "type": "containerCOMP",
                    "inputs": [],
                    "childrenHidden": 4,
                }
            ],
            "childrenHidden": 12,
            "truncated": True,
            "hidden": 16,
            "limit": 5000,
            "maxChildren": 2000,
        },
    )

    assert "4 more child(ren) not listed" in text
    assert "12 more child(ren) not listed" in text
    # "at least": `hidden` counts only what the walk discovered and did not
    # describe, never what hangs below a component it cut.
    assert "TRUNCATED: at least 16 operator(s) were left out" in text


def test_td_network_says_when_the_depth_it_was_given_was_reduced(monkeypatch):
    text = _render(
        monkeypatch,
        {
            "path": "/project1",
            "type": "containerCOMP",
            "depth": 16,
            "depthLimited": 999,
            "children": [],
        },
    )

    assert "depth 999 was reduced to 16" in text


def test_an_untruncated_reply_says_nothing_about_limits(monkeypatch):
    text = _render(
        monkeypatch,
        {
            "path": "/project1",
            "type": "containerCOMP",
            "depth": 1,
            "children": [
                {"path": "/project1/a", "name": "a", "type": "nullCHOP", "inputs": []}
            ],
        },
    )

    assert "TRUNCATED" not in text
    assert "not listed" not in text
