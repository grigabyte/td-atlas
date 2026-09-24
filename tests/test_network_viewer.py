"""`td_network` marks a TOP whose viewer is off.

Second agent report, "Спотыкания помельче": the owner saw an empty node for
`look`, whose viewer flag had been turned off for frame rate, and concluded
the scene was broken. The flag is `OP.viewer` (read live on 2025.32460,
2026-09-24: `{'lev': False, 'look': True, 'trend': False}` in DEMO2). The
reply carries it for TOPs only, since a TOP's tile is the one that shows
the image, and the tool prints `[viewer off]`.
"""

from __future__ import annotations

from td_atlas.component import handler
from td_atlas.mcp import server


class FakeOp:
    def __init__(self, path, family="TOP", viewer=True, children=None):
        self.path = path
        self.name = path.rsplit("/", 1)[-1]
        self.OPType = {"TOP": "nullTOP", "CHOP": "nullCHOP"}.get(family, "baseCOMP")
        self.family = family
        self.valid = True
        self.inputs = []
        self.viewer = viewer
        self.children = children or []

    def errors(self, recurse=False):
        return ""

    def warnings(self, recurse=False):
        return ""


def test_the_reply_carries_the_viewer_flag_of_a_top():
    assert handler._op_summary(FakeOp("/p/look", viewer=False))["viewer"] is False
    assert handler._op_summary(FakeOp("/p/look", viewer=True))["viewer"] is True


def test_other_families_carry_no_viewer_field():
    assert "viewer" not in handler._op_summary(FakeOp("/p/c", family="CHOP"))


def test_the_tool_marks_a_top_whose_viewer_is_off(monkeypatch):
    root = FakeOp("/p", family="COMP", children=[
        FakeOp("/p/look", viewer=False),
        FakeOp("/p/shown", viewer=True),
        FakeOp("/p/lfo", family="CHOP", viewer=False),
    ])
    monkeypatch.setattr(handler, "_resolve", lambda path: root)

    class Client:
        version_warning = None
        selection_warning = None

        def network(self, path, depth):
            return handler.m_network({"path": path, "depth": depth})

    monkeypatch.setattr(server, "bridge", lambda: Client())
    monkeypatch.setattr(server, "_warn", lambda _c: "")
    text = server.td_network("/p")
    lines = {line.strip().split(" ")[0]: line for line in text.splitlines()}
    assert "[viewer off]" in lines["look"]
    assert "[viewer off]" not in lines["shown"]
    assert "[viewer off]" not in lines["lfo"]


def test_an_older_bridge_without_the_field_marks_nothing(monkeypatch):
    class Client:
        version_warning = None
        selection_warning = None

        def network(self, path, depth):
            return {"path": "/p", "type": "baseCOMP", "children": [
                {"name": "look", "type": "nullTOP", "inputs": []},
            ]}

    monkeypatch.setattr(server, "bridge", lambda: Client())
    monkeypatch.setattr(server, "_warn", lambda _c: "")
    assert "[viewer off]" not in server.td_network("/p")
