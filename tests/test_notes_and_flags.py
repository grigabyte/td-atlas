"""Notes in the network, and the flags that decide whether a node runs.

Two halves of the same problem: state that is invisible to every other tool
here. An Annotate is where a person leaves an agent a brief and where an agent
says why it built what it built — neither is an error, a parameter or a name. A
flag is why a correct-looking network produces nothing, and the bridge used to
read exactly one of them, `bypass`, and only inside a health sample.

Nothing here needs TouchDesigner. The fakes carry the behaviours measured on a
live 2025.32460, and each of those is called out where it is imitated, because
they are the reason the code has the shape it has:

- `create(annotateCOMP, 'name')` ignores the name and produces 'annotate1';
  assigning `.name` afterwards works.
- Creating an annotateCOMP closes one undo level from under the caller, so
  `startBlock` / create / `endBlock` fails on the endBlock.
- `pickable` exists on COMP only; `allowCooking` cannot be disabled outside one.
"""

from __future__ import annotations

import pytest

from td_atlas.bridge.client import BridgeError
from td_atlas.component import handler
from td_atlas.mcp import server


# -- a network without TouchDesigner ----------------------------------------

class FakePar:
    def __init__(self, value):
        self.val = value

    def eval(self):
        return self.val


class FakePars:
    """`getattr(target.par, name, None)` is the whole interface used."""

    def __init__(self, values=None):
        for name, value in (values or {}).items():
            setattr(self, name, FakePar(value))


# The default-setup custom parameters a fresh annotateCOMP carries.
ANNOTATE_DEFAULTS = {
    "Titletext": "Annotate",
    "Bodytext": "",
    "Bodyfontsize": 12.0,
    "Bodylimitwidth": 0,
    "Mode": "annotate",
    "Backcolorr": 0.45,
    "Backcolorg": 0.45,
    "Backcolorb": 0.45,
    "Backcoloralpha": 1.0,
}


class FakeOP:
    def __init__(self, name, op_type, parent=None, width=130, height=90, pars=None):
        self.name = name
        self.OPType = op_type
        self.family = "COMP" if op_type.endswith("COMP") else op_type[-3:]
        self.nodeWidth = width
        self.nodeHeight = height
        self.nodeX = 0
        self.nodeY = 0
        self.children = []
        self.inputs = []
        self.parent_op = parent
        self.destroyed = False
        self.par = FakePars(pars)
        # Flags a real operator of this family has. `pickable` on a non-COMP is
        # absent altogether, which is how TouchDesigner behaves.
        self._flags = {
            name: False
            for name in (
                "display",
                "render",
                "bypass",
                "lock",
                "viewer",
                "activeViewer",
                "cloneImmune",
                "selected",
            )
        }
        self._flags["expose"] = True
        self._flags["allowCooking"] = True
        if self.family == "COMP":
            self._flags["pickable"] = True
        # Flags this fake accepts a write to. Everything else raises, standing
        # in for 'This flag can only be disabled for COMPs'.
        self._settable = set(self._flags)
        if self.family != "COMP":
            self._settable.discard("allowCooking")
        # Flags that take a write and quietly do not change, the silent case.
        self._sticky = set()

    # -- flags as attributes, the way an OP presents them
    def __getattr__(self, name):
        flags = self.__dict__.get("_flags", {})
        if name in flags:
            return flags[name]
        raise AttributeError(
            "'td.%s' object has no attribute '%s'" % (self.OPType, name)
        )

    def __setattr__(self, name, value):
        flags = self.__dict__.get("_flags", {})
        if name in flags:
            if name not in self._settable:
                raise RuntimeError("This flag can only be disabled for COMPs.")
            if name not in self._sticky:
                flags[name] = bool(value)
            return
        object.__setattr__(self, name, value)

    @property
    def valid(self):
        """False once destroyed, the way a real OP reports itself.

        The rollback sweep in `m_batch` reads this before touching a note; a
        fake that always said True made the sweep destroy an operator the undo
        had already taken out, which pushed an entry of its own.
        """
        return not self.destroyed

    @property
    def path(self):
        if self.parent_op is None:
            return "/" + self.name if self.name else "/"
        head = self.parent_op.path
        return ("" if head == "/" else head) + "/" + self.name

    def parent(self):
        return self.parent_op

    def create(self, op_type, name=None):
        if op_type == "annotateCOMP":
            # Measured: the name is ignored and TouchDesigner numbers its own.
            existing = len([c for c in self.children if c.OPType == "annotateCOMP"])
            child = FakeOP(
                "annotate%d" % (existing + 1),
                op_type,
                parent=self,
                width=382,
                height=288,
                pars=dict(ANNOTATE_DEFAULTS),
            )
            child.nodeX, child.nodeY = -300, 100
        else:
            child = FakeOP(name or op_type, op_type, parent=self)
        self.children.append(child)
        handler.ui.undo.record(lambda: self._remove(child))
        if op_type == "annotateCOMP":
            # And measured: creating one commits the open block, note included.
            # The work the caller did before this point is now a closed undo
            # entry of its own, which is the whole reason m_batch counts them.
            handler.ui.undo.commit_open_level()
        return child

    def _remove(self, child):
        child.destroyed = True
        self.children = [c for c in self.children if c is not child]

    def _restore(self, child):
        child.destroyed = False
        if child not in self.children:
            self.children.append(child)

    def destroy(self):
        parent = self.parent_op
        self.destroyed = True
        if parent is not None:
            parent.children = [c for c in parent.children if c is not self]
            handler.ui.undo.record(lambda: parent._restore(self))

    def errors(self, recurse=False):
        return ""

    def warnings(self, recurse=False):
        return ""

    def pars(self):
        return []


class FakeUndo:
    """An undo stack of the shape measured on a live 2025.32460.

    The three behaviours that decide whether a failed batch leaves half a
    network behind, and that a fake without them cannot test:

    - the entry appears when a block is *started*, and collects the work done
      inside it; work inside an open block pushes nothing of its own;
    - creating an annotateCOMP commits the open block — the entry stays on the
      stack holding everything done so far, the note included — so the level
      the caller thought it held is gone;
    - `undo()` pops one entry and reverses only that entry's work.

    An earlier version of this fake modelled none of it and let a batch that
    left a node behind on the live instance pass green.
    """

    def __init__(self):
        self.stack = []
        self.open = []

    def startBlock(self, name, enable=True):
        self.open.append((name, []))

    def endBlock(self):
        if not self.open:
            raise RuntimeError("Cannot end non existent undo operation.")
        self.stack.append(self.open.pop())

    def commit_open_level(self):
        if self.open:
            self.stack.append(self.open.pop())

    def record(self, action):
        if self.open:
            self.open[-1][1].append(action)
        else:
            self.stack.append(("unblocked", [action]))

    def undo(self):
        if not self.stack:
            raise RuntimeError("nothing to undo")
        _, actions = self.stack.pop()
        for action in reversed(actions):
            action()

    @property
    def undoStack(self):
        return [name for name, _ in self.stack]

    @property
    def depth(self):
        return len(self.open)


class FakeUI:
    def __init__(self):
        self.undo = FakeUndo()


@pytest.fixture
def network(monkeypatch):
    """'/project1' with one TOP in it, reachable through handler.op.

    `ui` goes in before the network is built: every create records how to undo
    itself, the way a real one does.
    """
    monkeypatch.setattr(handler, "ui", FakeUI(), raising=False)
    root = FakeOP("", "baseCOMP")
    project = root.create("baseCOMP", "project1")
    node = project.create("noiseTOP", "noise1")
    node.nodeX, node.nodeY = 0, 0

    def lookup(path):
        if path in ("/", ""):
            return root
        current = root
        for piece in str(path).strip("/").split("/"):
            found = None
            for child in current.children:
                if child.name == piece:
                    found = child
                    break
            if found is None:
                return None
            current = found
        return current

    monkeypatch.setattr(handler, "op", lookup, raising=False)
    monkeypatch.setattr(handler, "_guard_scopes", lambda params, *paths: None)
    handler._UNDO_HELD[0] = 0
    handler._BATCH_LEVELS[0] = 0
    del handler._BATCH_NOTES[:]
    return project


# -- writing a note ---------------------------------------------------------

def test_a_note_carries_the_text_and_the_box_it_was_given(network):
    result = handler.m_annotate(
        {
            "parent": "/project1",
            "text": "noise -> level -> out\nwhy: the brief asked for it",
            "title": "built by td-atlas",
            "size": [400, 220],
            "color": [0.1, 0.2, 0.3],
            "position": [-40, -40],
        }
    )
    note = handler.op(result["path"])
    assert result["created"] is True
    assert note.par.Bodytext.eval() == "noise -> level -> out\nwhy: the brief asked for it"
    assert note.par.Titletext.eval() == "built by td-atlas"
    assert (note.nodeWidth, note.nodeHeight) == (400.0, 220.0)
    assert (note.nodeX, note.nodeY) == (-40.0, -40.0)
    assert note.par.Backcolorr.eval() == 0.1


def test_the_name_is_applied_after_the_create_because_the_create_ignores_it(network):
    """Measured: create(annotateCOMP, 'x') produces 'annotate1'.

    So the caller's name has to be a rename, and the reply has to carry the
    path the note really has rather than the one that was asked for.
    """
    result = handler.m_annotate(
        {"parent": "/project1", "text": "hello", "name": "agent_note"}
    )
    assert result["name"] == "agent_note"
    assert result["path"] == "/project1/agent_note"


def test_a_second_note_does_not_land_on_the_first(network):
    """A fresh Annotate places itself at (-300, 100) every time, measured."""
    first = handler.m_annotate({"parent": "/project1", "text": "one"})
    second = handler.m_annotate({"parent": "/project1", "text": "two"})
    a, b = handler.op(first["path"]), handler.op(second["path"])
    assert not handler._boxes_clash(handler._node_box(a), handler._node_box(b))


def test_rewriting_a_note_does_not_leave_a_second_one(network):
    """What an agent rerunning a build needs, instead of a stack of duplicates."""
    first = handler.m_annotate({"parent": "/project1", "text": "draft"})
    again = handler.m_annotate({"path": first["path"], "text": "final"})
    assert again["created"] is False
    assert again["path"] == first["path"]
    notes = handler.m_annotations({"path": "/project1"})
    assert notes["count"] == 1
    assert notes["annotations"][0]["text"] == "final"


def test_a_path_that_is_not_a_note_is_refused_by_kind(network):
    with pytest.raises(TypeError, match="not an annotateCOMP"):
        handler.m_annotate({"path": "/project1/noise1", "text": "nope"})


def test_a_note_without_the_default_setup_parameters_says_so(network):
    """An Annotate stripped of its extension has no 'Bodytext'.

    The honest answer is which parameter is missing, not an AttributeError
    traceback out of the bridge.
    """
    bare = network.create("annotateCOMP")
    bare.par = FakePars({})
    with pytest.raises(ValueError, match="Bodytext"):
        handler.m_annotate({"path": bare.path, "text": "nowhere to put this"})


def test_the_undo_level_the_create_ate_is_given_back_to_a_batch(network):
    """Otherwise a batch that applied cleanly is reported as a failure.

    `m_batch` opens one block and closes it at the end; creating an Annotate
    closes that block from underneath, and the endBlock then raises.
    """
    monkey = handler.ui.undo
    result = handler.m_batch(
        {
            "ops": [
                {
                    "method": "annotate",
                    "params": {"parent": "/project1", "text": "inside a batch"},
                }
            ],
            "undo_name": "one note",
        }
    )
    assert result["applied"] == 1
    assert monkey.depth == 0


def test_a_failed_batch_takes_its_note_back_out(network):
    """m_batch promises no partial network; a note is outside its undo block.

    Measured: one `ui.undo.undo()` does not remove it, and destroying it
    *before* that undo puts it straight back, because the destroy is what the
    undo then pops.
    """
    with pytest.raises(ValueError):
        handler.m_batch(
            {
                "ops": [
                    {
                        "method": "annotate",
                        "params": {"parent": "/project1", "text": "doomed"},
                    },
                    {"method": "op_create", "params": {"parent": "/project1"}},
                ]
            }
        )
    assert handler.m_annotations({"path": "/project1"})["count"] == 0


def test_a_failed_batch_leaves_nothing_behind_with_a_note_in_the_middle(network):
    """The defect acceptance found, and the one order that used to survive.

    A note commits the batch's undo entry and a new one is opened after it, so
    the batch finishes as two entries. One `undo()` popped the later one only,
    and the node created before the note stayed in the project — a half-built
    network out of the call that promises never to leave one.
    """
    before = len(handler.ui.undo.undoStack)
    with pytest.raises(ValueError):
        handler.m_batch(
            {
                "ops": [
                    {
                        "method": "op_create",
                        "params": {"parent": "/project1", "type": "noiseTOP", "name": "first"},
                    },
                    {
                        "method": "annotate",
                        "params": {"parent": "/project1", "text": "middle"},
                    },
                    {"method": "op_create", "params": {"parent": "/project1"}},
                ]
            }
        )
    assert [child.name for child in network.children] == ["noise1"]
    assert handler.m_annotations({"path": "/project1"})["count"] == 0
    assert len(handler.ui.undo.undoStack) == before


@pytest.mark.parametrize("notes", [1, 2, 3])
def test_a_failed_batch_rolls_back_however_many_notes_it_wrote(network, notes):
    """One undo per level opened: the count has to follow the notes."""
    ops = []
    for index in range(notes):
        ops.append(
            {
                "method": "op_create",
                "params": {"parent": "/project1", "type": "noiseTOP", "name": "n%d" % index},
            }
        )
        ops.append(
            {"method": "annotate", "params": {"parent": "/project1", "text": "note %d" % index}}
        )
    ops.append({"method": "op_create", "params": {"parent": "/project1"}})

    before = len(handler.ui.undo.undoStack)
    with pytest.raises(ValueError):
        handler.m_batch({"ops": ops})
    assert [child.name for child in network.children] == ["noise1"]
    assert len(handler.ui.undo.undoStack) == before


def test_a_note_is_swept_up_when_an_undo_itself_fails(network):
    """The sweep behind the counter, for the one case the counter cannot cover.

    If `undo()` raises part way through the rollback, the levels below it stay
    committed and the note the batch wrote is still in the network. Destroying
    it afterwards is the last thing that can still be done honestly.
    """
    undo = handler.ui.undo
    calls = []
    original = undo.undo

    def failing_undo():
        calls.append(1)
        raise RuntimeError("undo is unavailable")

    with pytest.raises(ValueError):
        undo.undo = failing_undo
        try:
            handler.m_batch(
                {
                    "ops": [
                        {
                            "method": "annotate",
                            "params": {"parent": "/project1", "text": "swept"},
                        },
                        {"method": "op_create", "params": {"parent": "/project1"}},
                    ]
                }
            )
        finally:
            undo.undo = original

    assert calls, "the rollback did try to undo"
    assert handler.m_annotations({"path": "/project1"})["count"] == 0


def test_a_failed_batch_undoes_its_own_levels_and_no_more(network):
    """One undo too many reaches into the artist's history.

    Measured on the live instance: undoing past this batch's own entries began
    resurrecting operators deleted before it started.
    """
    handler.ui.undo.startBlock("artist edit")
    artist = network.create("noiseTOP", "artist_node")
    handler.ui.undo.endBlock()
    before = len(handler.ui.undo.undoStack)

    with pytest.raises(ValueError):
        handler.m_batch(
            {
                "ops": [
                    {
                        "method": "op_create",
                        "params": {"parent": "/project1", "type": "noiseTOP", "name": "mine"},
                    },
                    {"method": "annotate", "params": {"parent": "/project1", "text": "note"}},
                    {"method": "op_create", "params": {"parent": "/project1"}},
                ]
            }
        )
    assert artist in network.children
    assert artist.destroyed is False
    assert len(handler.ui.undo.undoStack) == before


def test_a_note_that_fails_halfway_is_not_left_behind(network):
    with pytest.raises(ValueError, match="three components"):
        handler.m_annotate({"parent": "/project1", "text": "x", "color": [0.5]})
    assert handler.m_annotations({"path": "/project1"})["count"] == 0
    assert handler.ui.undo.depth == 0


# -- reading notes ----------------------------------------------------------

def test_a_note_written_by_somebody_else_is_read_the_same_way(network):
    """The reason this exists: a person's brief left in the project itself."""
    human = network.create("annotateCOMP")
    human.par.Titletext.val = "TODO for the agent"
    human.par.Bodytext.val = "please add a feedback loop here"
    result = handler.m_annotations({"path": "/project1"})
    assert result["count"] == 1
    note = result["annotations"][0]
    assert note["title"] == "TODO for the agent"
    assert note["text"] == "please add a feedback loop here"


def test_a_note_reports_the_nodes_its_box_sits_over(network):
    """What says which part of the network a note is about."""
    note = network.create("annotateCOMP")
    note.nodeX, note.nodeY = -50, -50
    note.nodeWidth, note.nodeHeight = 400, 300
    outside = network.create("noiseTOP", "far_away")
    outside.nodeX, outside.nodeY = 5000, 5000
    covers = handler.m_annotations({"path": "/project1"})["annotations"][0]["covers"]
    assert covers == ["/project1/noise1"]


def test_notes_do_not_report_each_other_as_covered(network):
    """Annotates overlap by design; listing them as contents is noise."""
    big = network.create("annotateCOMP")
    big.nodeX, big.nodeY = -1000, -1000
    big.nodeWidth, big.nodeHeight = 4000, 4000
    small = network.create("annotateCOMP")
    small.nodeX, small.nodeY = 0, 0
    for note in handler.m_annotations({"path": "/project1"})["annotations"]:
        assert small.path not in note["covers"]


def test_notes_are_found_below_the_path_that_was_asked_for(network):
    inner = network.create("baseCOMP", "inner")
    inner.create("annotateCOMP")
    assert handler.m_annotations({"path": "/project1"})["count"] == 1
    assert handler.m_annotations({"path": "/project1/inner"})["count"] == 1
    assert handler.m_annotations({"path": "/project1/noise1"})["count"] == 0


def test_a_depth_limit_stops_the_walk(network):
    inner = network.create("baseCOMP", "inner")
    deeper = inner.create("baseCOMP", "deeper")
    deeper.create("annotateCOMP")
    assert handler.m_annotations({"path": "/project1", "depth": 1})["count"] == 0
    assert handler.m_annotations({"path": "/project1", "depth": 3})["count"] == 1


def test_a_note_missing_its_text_parameter_reads_as_a_gap_not_a_failure(network):
    """A null field says 'this Annotate has no such parameter'.

    Refusing the whole read because one note is unusual would hide the others.
    """
    bare = network.create("annotateCOMP")
    bare.par = FakePars({})
    network.create("annotateCOMP").par.Bodytext.val = "readable"
    texts = sorted(
        str(note["text"])
        for note in handler.m_annotations({"path": "/project1"})["annotations"]
    )
    assert texts == ["None", "readable"]


# -- flags ------------------------------------------------------------------

def test_a_snapshot_separates_off_from_not_available(network):
    """'off' and 'this operator has no such flag' are different answers."""
    top = handler.m_flags({"path": "/project1/noise1"})["ops"][0]
    assert top["flags"]["bypass"] is False
    assert "pickable" in top["unavailable"]
    comp = handler.m_flags({"path": "/project1"})["ops"][0]
    assert comp["flags"]["pickable"] is True
    assert comp["unavailable"] == []


def test_a_flag_is_set_and_reads_back_changed(network):
    result = handler.m_flags_set(
        {"path": "/project1/noise1", "flags": {"bypass": True}}
    )
    assert result["before"] == {"bypass": False}
    assert result["applied"] == {"bypass": True}
    read = handler.m_flags({"path": "/project1/noise1"})["ops"][0]
    assert read["flags"]["bypass"] is True


def test_a_flag_name_that_does_not_exist_is_refused_with_the_list(network):
    with pytest.raises(ValueError, match="unknown flag"):
        handler.m_flags_set({"path": "/project1/noise1", "flags": {"visible": True}})


def test_a_flag_the_family_does_not_have_is_refused_by_name(network):
    """Measured: reading `pickable` on a TOP raises tdAttributeError.

    Which reaches an agent as an unmapped bridge error unless it is turned
    into a refusal that names the flag and the family.
    """
    with pytest.raises(ValueError, match="has no 'pickable' flag"):
        handler.m_flags_set({"path": "/project1/noise1", "flags": {"pickable": True}})


def test_a_flag_the_family_will_not_take_is_refused_not_swallowed(network):
    """`allowCooking = False` outside a COMP: 'This flag can only be disabled'."""
    with pytest.raises(ValueError, match="refused 'allowCooking'"):
        handler.m_flags_set(
            {"path": "/project1/noise1", "flags": {"allowCooking": False}}
        )


def test_a_write_that_is_accepted_but_does_nothing_is_still_refused(network):
    """The silent case, and the reason every write is read back.

    A flag that takes the assignment and reads back unchanged would otherwise
    be reported as applied, and the agent would build on a lie.
    """
    node = handler.op("/project1/noise1")
    node._sticky.add("display")
    with pytest.raises(ValueError, match="reads back"):
        handler.m_flags_set({"path": "/project1/noise1", "flags": {"display": True}})


def test_a_refused_flag_leaves_the_ones_before_it_as_they_were(network):
    """Half-applied flags are worse than none: the network would look built."""
    node = handler.op("/project1/noise1")
    with pytest.raises(ValueError):
        handler.m_flags_set(
            {
                "path": "/project1/noise1",
                "flags": {"bypass": True, "allowCooking": False},
            }
        )
    assert node.bypass is False
    assert handler.ui.undo.depth == 0


def test_an_empty_request_is_refused_rather_than_reported_as_done(network):
    with pytest.raises(ValueError, match="needs 'flags'"):
        handler.m_flags_set({"path": "/project1/noise1", "flags": {}})


# -- the MCP surface --------------------------------------------------------

class FakeBridge:
    def __init__(self, result=None, raises=None):
        self.result = result or {}
        self.raises = raises
        self.calls: list[tuple[str, dict]] = []
        self.ambiguity_warning = None
        self.version_warning = None

    def call(self, method, **params):
        self.calls.append((method, params))
        if self.raises is not None:
            raise self.raises
        return self.result


def test_td_set_flags_refuses_a_bad_name_without_a_round_trip(monkeypatch):
    """A typo should cost nothing, and answer the same with the bridge down."""
    fake = FakeBridge()
    monkeypatch.setattr(server, "bridge", lambda: fake)
    text = server.td_set_flags(path="/project1/noise1", flags={"visible": True})
    assert "no node flag is named 'visible'" in text
    assert "continue with: td_flags" in text
    assert fake.calls == []


def test_td_set_flags_reports_the_change_in_both_directions(monkeypatch):
    fake = FakeBridge(
        {
            "path": "/project1/noise1",
            "type": "noiseTOP",
            "family": "TOP",
            "before": {"bypass": False},
            "applied": {"bypass": True},
        }
    )
    monkeypatch.setattr(server, "bridge", lambda: fake)
    text = server.td_set_flags(
        path="/project1/noise1", flags={"bypass": True}, owner="me"
    )
    assert "bypass: False -> True" in text
    assert fake.calls[0][1]["owner"] == "me"


def test_td_flags_shows_what_the_operator_does_not_have(monkeypatch):
    fake = FakeBridge(
        {
            "ops": [
                {
                    "path": "/project1/noise1",
                    "type": "noiseTOP",
                    "family": "TOP",
                    "flags": {"bypass": True, "display": False},
                    "unavailable": ["pickable"],
                }
            ]
        }
    )
    monkeypatch.setattr(server, "bridge", lambda: fake)
    text = server.td_flags(path="/project1/noise1")
    assert "on:  bypass" in text
    assert "off: display" in text
    assert "not on this operator: pickable" in text


def test_td_flags_names_every_flag_the_bridge_knows():
    """The two lists cannot drift: the tool validates against the bridge's own.

    A flag added to the handler and forgotten in the hint would be refused by
    name with advice that does not mention it.
    """
    from td_atlas.mcp.hints import HINTS

    advice = HINTS["flag_unknown"].action
    missing = [name for name in handler.NODE_FLAGS if name not in advice]
    assert missing == []


def test_td_annotations_says_nothing_is_there_with_a_way_forward(monkeypatch):
    fake = FakeBridge({"root": "/project1", "count": 0, "annotations": []})
    monkeypatch.setattr(server, "bridge", lambda: fake)
    text = server.td_annotations(path="/project1")
    assert "No note in /project1" in text
    assert "continue with: td_network, td_annotate" in text


def test_td_annotations_prints_the_body_and_what_it_covers(monkeypatch):
    fake = FakeBridge(
        {
            "root": "/project1",
            "count": 1,
            "annotations": [
                {
                    "path": "/project1/note",
                    "name": "note",
                    "title": "TODO",
                    "text": "add a feedback loop\nsecond line",
                    "covers": ["/project1/noise1"],
                }
            ],
        }
    )
    monkeypatch.setattr(server, "bridge", lambda: fake)
    text = server.td_annotations(path="/project1")
    assert "'TODO'" in text
    assert "add a feedback loop" in text
    assert "second line" in text
    assert "over: /project1/noise1" in text


def test_td_annotate_reports_the_path_the_note_really_got(monkeypatch):
    """The name asked for is not always the name given — see the rename above."""
    fake = FakeBridge(
        {
            "path": "/project1/annotate1",
            "name": "annotate1",
            "type": "annotateCOMP",
            "created": True,
            "written": {"text": "why"},
        }
    )
    monkeypatch.setattr(server, "bridge", lambda: fake)
    text = server.td_annotate(text="why", parent="/project1", name="agent_note")
    assert "/project1/annotate1" in text
    assert "not 'agent_note'" in text


def test_td_annotate_sends_only_the_fields_it_was_given(monkeypatch):
    """An empty title must not overwrite a title that is already there."""
    fake = FakeBridge(
        {"path": "/project1/note", "name": "note", "created": False, "written": {}}
    )
    monkeypatch.setattr(server, "bridge", lambda: fake)
    server.td_annotate(text="body only", path="/project1/note")
    _, params = fake.calls[0]
    assert "title" not in params
    assert "size" not in params
    assert params["path"] == "/project1/note"


def test_a_bridge_failure_comes_back_as_text_with_a_hint(monkeypatch):
    monkeypatch.setattr(
        server,
        "bridge",
        lambda: FakeBridge(raises=BridgeError({"type": "LookupError", "message": "no op"}, "flags")),
    )
    text = server.td_flags(path="/project1/nope")
    assert "cause:" in text and "fix:" in text
