"""Snapshot a branch, change it, see the difference — by composition.

First agent report, "Что стоило бы добавить" 5: "save the parameters of the
whole branch, I change them, show me the difference". The agent ended up
dumping parameters to a text file by hand (`params_v5_before_tiles.txt`, 20 KB)
and used it as a point to roll back to.

No new tool answers that, because three existing ones already do:
`td_snapshot(label, path)` writes the branch's COMP to a .tox,
a second `td_snapshot` under another label writes it again after the edits,
and `td_project_diff` of the two files lists every parameter that moved. What
was missing was the agent finding the route, so the snapshot's own reply now
names the next step with the file path in it.

Two halves, failing differently:

- The reply is checked against a fake bridge, with no TouchDesigner at all.
- The diff leg runs on a shipped palette component edited offline, the way a
  second snapshot would differ from the first, and skips without an
  installation (it needs `toeexpand`). It passed before the reply changed; it
  is here so the recipe the reply points at stays true.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from td_atlas.mcp import server


class FakeClient:
    """A bridge that writes nothing and reports the file it was asked for."""

    def __init__(self):
        self.calls = []

    def call(self, method, **params):
        self.calls.append((method, params))
        return {"saved": params["file"]}


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / "home"
    root.mkdir()
    monkeypatch.setenv("TD_ATLAS_HOME", str(root))
    return root


@pytest.fixture
def client(monkeypatch, home):
    fake = FakeClient()
    monkeypatch.setattr(server, "bridge", lambda: fake)
    monkeypatch.setattr(server, "_warn", lambda _c: "")
    return fake


# -- the reply names the route ----------------------------------------------


def test_the_snapshot_reply_names_the_diff_that_comes_next(client, home):
    reply = server.td_snapshot("before", path="/project1/cement")
    saved = str(home / "snapshots" / "before.tox")

    assert client.calls == [("save_tox", {"path": "/project1/cement", "file": saved})]
    # The path must be there verbatim: it is what goes into `before=`.
    assert f"td_project_diff(before='{saved}'" in reply
    # And the second snapshot must be of the same branch under another label,
    # or it replaces this one and the diff says "No differences."
    assert "td_snapshot(label='<another label>', path='/project1/cement')" in reply


def test_a_reused_label_says_it_replaced_the_earlier_snapshot(client, home):
    first = server.td_snapshot("try", path="/project1/cement")
    assert "replaced" not in first

    # save_tox is faked, so put the file where a real save would have.
    (home / "snapshots" / "try.tox").write_bytes(b"earlier")
    second = server.td_snapshot("try", path="/project1/cement")
    assert "replaced an earlier snapshot 'try'" in second


# -- the diff leg, on a real component --------------------------------------


def _installed():
    from td_atlas.install import InstallNotFound, discover

    try:
        return discover()
    except InstallNotFound:
        return None


needs_td = pytest.mark.skipif(
    _installed() is None, reason="no TouchDesigner installation"
)


def _palette(name: str) -> Path | None:
    install = _installed()
    if install is None:
        return None
    root = install.root / "Contents" / "Resources" / "tfs" / "Samples" / "Palette"
    if not root.is_dir():
        root = install.root / "Samples" / "Palette"
    found = sorted(root.rglob(name))
    return found[0] if found else None


def _find(ops, name):
    for op in ops:
        if op["name"] == name:
            return op
        found = _find(op.get("children") or [], name)
        if found:
            return found
    return None


@needs_td
def test_two_snapshots_of_a_branch_diff_to_its_parameter_changes(home, tmp_path):
    """The second snapshot, made offline: one of each kind of parameter edit.

    A constant, an expression, a custom parameter on the branch's own COMP,
    and a parameter put back to its default — the four things a parameter
    dump by hand would have been compared for.
    """
    source = _palette("checker.tox")
    if source is None:
        pytest.skip("palette not present")
    before = tmp_path / "before.tox"
    before.write_bytes(source.read_bytes())
    after = tmp_path / "after.tox"

    data = json.loads(server.td_project_text(str(before)))
    rect = _find(data["operators"], "rectangle1")
    rect["parms"]["sizex"] = "0.7"
    par1 = _find(data["operators"], "par1")
    assert par1["parms"]["ops"]["expr"] == "parent.Checker", "fixture moved"
    par1["parms"]["ops"] = {"expr": "parent.Other", "value": "parent()"}
    inner = _find(data["operators"], "checker")["children"][0]
    assert inner["name"] == "checker" and inner["parms"]["Version"] == "1.0.1"
    inner["parms"]["Version"] = "2.0.0"
    del _find(data["operators"], "lookup1")["parms"]["darkuv1"]

    wrote = server.td_project_write(str(before), json.dumps(data), str(after))
    assert "did not land" not in wrote, wrote

    reply = server.td_project_diff(str(before), str(after))
    assert reply.startswith("0 added, 0 removed, 4 changed"), reply
    assert "sizex: '1' -> '0.7'" in reply
    assert "ops: 'parent.Checker' -> 'parent.Other'" in reply
    assert "Version: '1.0.1' -> '2.0.0'" in reply
    assert "-darkuv1 (was '0')" in reply


@needs_td
def test_the_before_snapshot_holds_each_value_in_the_shape_set_params_takes(
    tmp_path,
):
    """The roll-back leg: the diff shows what drives a parameter, not its mode.

    `ops: 'parent.Checker' -> ...` does not say that 'parent.Checker' is an
    expression, so setting it back as a plain value would be wrong. The text
    of the before snapshot says so, as {"expr": ...}, which is the form
    td_set_params reads — and writing those values back over the after state
    leaves nothing to diff.
    """
    source = _palette("checker.tox")
    if source is None:
        pytest.skip("palette not present")
    before = tmp_path / "before.tox"
    before.write_bytes(source.read_bytes())
    after = tmp_path / "after.tox"
    restored = tmp_path / "restored.tox"

    data = json.loads(server.td_project_text(str(before)))
    _find(data["operators"], "par1")["parms"]["ops"] = {
        "expr": "parent.Other", "value": "parent()"
    }
    _find(data["operators"], "rectangle1")["parms"]["sizex"] = "0.7"
    server.td_project_write(str(before), json.dumps(data), str(after))

    was = json.loads(
        server.td_project_text(str(before), path="/checker/checker/par1")
    )["operators"][0]["parms"]["ops"]
    assert was == {"expr": "parent.Checker", "value": "parent()"}

    now = json.loads(server.td_project_text(str(after)))
    was_rect = json.loads(
        server.td_project_text(str(before), path="/checker/checker/rectangle1")
    )["operators"][0]["parms"]
    _find(now["operators"], "par1")["parms"]["ops"] = was
    _find(now["operators"], "rectangle1")["parms"]["sizex"] = was_rect["sizex"]
    server.td_project_write(str(after), json.dumps(now), str(restored))

    assert server.td_project_diff(str(before), str(restored)).endswith(
        "No differences."
    )
