"""A DAT's contents, set in the same step that creates it.

The defect these cover was measured in a live session (2026-08-30, journal
entry 22): `op_create` could not carry a DAT's text, so every shader and every
script body cost a second call — a `td_exec` doing `op(...).text = ...`. That
call lands outside the batch, so the text is outside its rollback and outside
the artist's single Ctrl+Z. On a GLSL network, where most of what is built *is*
DAT text, that was the common path rather than an edge case.

Nothing here needs TouchDesigner: `m_op_create` runs against a fake network,
the same trick `test_layout.py` uses.
"""

from __future__ import annotations

import pytest

from td_atlas.component import handler


# type -> (family, TouchDesigner's short `type` string used by
# `_dat_owns_its_text`). The short names are what a live operator reports:
# `textDAT.type` is 'text', not 'textDAT'.
KINDS = {
    "textDAT": ("DAT", "text"),
    "tableDAT": ("DAT", "table"),
    "parameterexecuteDAT": ("DAT", "parexec"),
    "selectDAT": ("DAT", "select"),
    "infoDAT": ("DAT", "info"),
    "noiseTOP": ("TOP", "noise"),
}


class FakeConnector:
    def __init__(self, owner):
        self.owner = owner

    def connect(self, other):
        other.owner.inputs.append(self.owner)


class FakeOP:
    def __init__(self, name, op_type="textDAT", parent=None):
        family, kind = KINDS[op_type]
        self.name = name
        self.OPType = op_type
        self.family = family
        self.type = kind
        self.isDAT = family == "DAT"
        self.text = ""
        self.nodeWidth = 130
        self.nodeHeight = 90
        self.nodeX = self.nodeY = 0
        self.valid = True
        self.parent_op = parent
        self.children = []
        self.inputs = []
        self.inputConnectors = [FakeConnector(self)]
        self.outputConnectors = [FakeConnector(self)]

    @property
    def path(self):
        if self.parent_op is None:
            return "/" + self.name
        head = self.parent_op.path
        return ("" if head == "/" else head) + "/" + self.name

    def create(self, op_type, name=None):
        child = FakeOP(name or op_type, op_type, parent=self)
        self.children.append(child)
        return child

    def errors(self, recurse=False):
        return ""

    def warnings(self, recurse=False):
        return ""

    def pars(self):
        return []

    def destroy(self):
        self.valid = False
        if self.parent_op is not None and self in self.parent_op.children:
            self.parent_op.children.remove(self)


@pytest.fixture
def network(monkeypatch):
    root = FakeOP("", "textDAT")
    root.name = ""
    project = root.create("textDAT", "project1")

    def lookup(path):
        if path in ("/", ""):
            return root
        node = root
        for piece in path.strip("/").split("/"):
            node = next((c for c in node.children if c.name == piece), None)
            if node is None:
                return None
        return node

    monkeypatch.setattr(handler, "op", lookup, raising=False)
    monkeypatch.setattr(handler, "_guard_scopes", lambda params, *paths: None)
    return project


def create(op_type, name, **params):
    request = {"parent": "/project1", "type": op_type, "name": name}
    request.update(params)
    return handler.m_op_create(request)


SHADER = "out vec4 fragColor;\nvoid main() { fragColor = vec4(1.0); }\n"


def test_text_lands_on_the_created_dat(network):
    create("textDAT", "frag", text=SHADER)
    assert network.children[0].text == SHADER


def test_text_is_optional(network):
    """Every existing caller passes no `text`, and must keep working."""
    create("textDAT", "plain")
    assert network.children[0].text == ""


def test_empty_text_is_written_not_skipped(network):
    """`''` is a value a caller can mean; only a missing key means 'leave it'."""
    node = network.create("textDAT", "seed")
    node.text = "junk"
    create("textDAT", "blank", text="")
    assert network.children[-1].text == ""


def test_a_callback_dat_takes_text(network):
    """`_dat_owns_its_text` covers the `*exec` family, and so must this."""
    create("parameterexecuteDAT", "cb", text="def onValueChange(par, prev):\n\tpass\n")
    assert network.children[0].text.startswith("def onValueChange")


def test_text_on_a_non_dat_is_refused_by_name(network):
    with pytest.raises(TypeError) as caught:
        create("noiseTOP", "n1", text=SHADER)
    message = str(caught.value)
    assert "/project1/n1" in message
    assert "noiseTOP" in message
    assert "textDAT" in message, "the refusal must name what to use instead"


def test_text_on_a_computed_dat_is_refused(network):
    """A Select DAT accepts the assignment live and loses it on the next cook."""
    with pytest.raises(TypeError) as caught:
        create("selectDAT", "pick", text=SHADER)
    message = str(caught.value)
    assert "selectDAT" in message
    assert "cook" in message, "the refusal must say why, not just refuse"


def test_an_info_dat_is_refused_too(network):
    with pytest.raises(TypeError):
        create("infoDAT", "i1", text=SHADER)


def test_non_string_text_is_refused(network):
    with pytest.raises(TypeError) as caught:
        create("textDAT", "bad", text=["a", "b"])
    assert "string" in str(caught.value)


def test_text_survives_a_batch(network, monkeypatch):
    """The point of the feature: the text is inside the undo block."""

    class FakeUndo:
        def __init__(self):
            self.blocks = []
            self.depth = 0

        def startBlock(self, name, enable=True):
            self.blocks.append(name)
            self.depth += 1

        def endBlock(self):
            self.depth -= 1

        def undo(self):
            self.depth = 0

    class FakeUI:
        def __init__(self):
            self.undo = FakeUndo()

    fake_ui = FakeUI()
    monkeypatch.setattr(handler, "ui", fake_ui, raising=False)
    monkeypatch.setattr(handler, "_undo_depth", lambda: 0)
    monkeypatch.setattr(handler, "_note_status", lambda **kw: None)

    handler.m_batch(
        {
            "ops": [
                {
                    "method": "op_create",
                    "params": {
                        "parent": "/project1",
                        "type": "textDAT",
                        "name": "frag",
                        "text": SHADER,
                    },
                }
            ],
            "undo_name": "shader",
        }
    )
    assert network.children[0].text == SHADER
    assert fake_ui.undo.blocks == ["shader"]


# -- a refused create leaves nothing behind ---------------------------------

def test_a_refused_create_takes_its_node_with_it(network):
    """Measured live on 2026-08-30: it did not.

    Everything after `create` can refuse — a parameter name that does not
    exist, text on a DAT that computes its own, a source that is not there —
    and the half-made node stayed in the network each time. Inside a batch the
    rollback swept it up; a single `op_create` had nothing to undo it, so the
    caller was told "no" and still had to go and find the leftover.
    """
    with pytest.raises(TypeError):
        create("noiseTOP", "n1", text=SHADER)
    assert [c.name for c in network.children] == []


def test_the_refusal_survives_a_failing_cleanup(network, monkeypatch):
    """The caller has to hear why the create refused, not why the sweep did."""
    original = FakeOP.destroy

    def explode(self):
        raise RuntimeError("destroy blew up")

    monkeypatch.setattr(FakeOP, "destroy", explode)
    with pytest.raises(TypeError):
        create("selectDAT", "pick", text=SHADER)
    monkeypatch.setattr(FakeOP, "destroy", original)
