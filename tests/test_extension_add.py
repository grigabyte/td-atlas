"""`td_extension_add`: hang a Python class on a COMP in one call.

Nothing here needs TouchDesigner. The interesting half is the fake COMP below:
it does not accept the Extension Object expression as a string to be echoed
back, it *resolves* it the way TouchDesigner does — find the DAT the
expression names, execute its text, instantiate the class from the resulting
module namespace. That is what lets these tests catch the failure the whole
tool exists for: the wiki's `ClassName(me)` form leaves the extension as None
with nothing reporting an error, and only the read-back notices.

The resolution rule and the silence around its failure were measured on a live
2025.32460 — see `handler._extension_expr`.
"""

from __future__ import annotations

import re

import pytest

from td_atlas.bridge.client import BridgeError, BridgeUnavailable
from td_atlas.component import handler
from td_atlas.mcp import server


GOOD_CODE = (
    "class DemoExt:\n"
    "    def __init__(self, ownerComp):\n"
    "        self.ownerComp = ownerComp\n"
    "    def Hello(self, x):\n"
    "        return 'hello %s' % x\n"
)

BROKEN_INIT = (
    "class DemoExt:\n"
    "    def __init__(self, ownerComp):\n"
    "        raise RuntimeError('boom in init')\n"
)


# -- the fake world ----------------------------------------------------------

class FakePar:
    """One parameter. `_apply_pars` sets `.val` and reads `.eval()`."""

    def __init__(self, name, value="", on_pulse=None):
        self.name = name
        self.val = value
        self.on_pulse = on_pulse
        self.pulses = 0

    def eval(self):
        return self.val

    def pulse(self):
        self.pulses += 1
        if self.on_pulse is not None:
            self.on_pulse()


class FakePars:
    """The `.par` namespace: attribute access, nothing else."""


class FakeSequence:
    """The `ext` sequence, whose block count the handler grows.

    Measured on a fresh baseCOMP: `numBlocks` is 1 while `par.ext1object`
    already exists, and assigning that parameter grows the count to 2. Only
    the next block is reachable that way, so the handler grows the sequence
    explicitly and this fake creates parameters for `numBlocks + 1` blocks.
    """

    def __init__(self, comp, blocks=1):
        self._comp = comp
        self._blocks = 0
        self.numBlocks = blocks

    @property
    def numBlocks(self):
        return self._blocks

    @numBlocks.setter
    def numBlocks(self, count):
        self._blocks = int(count)
        self._comp._make_ext_pars(self._blocks + 1)


class FakeSeq:
    def __init__(self, ext):
        self.ext = ext


# A plausible tile, not a measured one: `_place_node` only needs a size and a
# gap to reason about, and the real numbers come off the operator in
# TouchDesigner. 130x90 is what a live noiseTOP reported (see `_node_box`).
TILE = (130.0, 90.0)


class FakeDAT:
    family = "DAT"
    OPType = "textDAT"

    def __init__(self, path):
        self.path = path
        self.name = path.rsplit("/", 1)[-1]
        self.text = ""
        self.nodeX = 0.0
        self.nodeY = 0.0
        self.nodeWidth, self.nodeHeight = TILE


class FakeOp:
    """A non-COMP, for the refusal case: no Extensions page, no create()."""

    def __init__(self, path, op_type="noiseTOP", family="TOP"):
        self.path = path
        self.name = path.rsplit("/", 1)[-1]
        self.OPType = op_type
        self.family = family
        self.par = FakePars()


class FakeComp:
    """A COMP that resolves its own Extension Object expressions.

    `extensions` is recomputed on every `reinitextensions` pulse, exactly as
    the parameter does, and a class that raises during instantiation leaves
    None behind without any error surfacing on the COMP — the silent failure
    measured live.
    """

    OPType = "baseCOMP"
    family = "COMP"

    def __init__(self, path, blocks=1):
        self.path = path
        self.name = path.rsplit("/", 1)[-1]
        self.children_by_name = {}
        self.par = FakePars()
        self.nodeX = 0.0
        self.nodeY = 0.0
        self.nodeWidth, self.nodeHeight = TILE
        self.reinits = 0
        self.evals = []
        self.extensions = [None]
        self.ext = FakePars()
        self.par.reinitextensions = FakePar("reinitextensions", on_pulse=self._reinit)
        self.seq = FakeSeq(FakeSequence(self, blocks))

    # -- structure

    @property
    def children(self):
        """What `_occupied_boxes` walks to find the tiles already placed."""
        return list(self.children_by_name.values())

    def _make_ext_pars(self, count):
        for index in range(count):
            for suffix, default in (("object", ""), ("name", ""), ("promote", False)):
                attr = "ext%d%s" % (index, suffix)
                if getattr(self.par, attr, None) is None:
                    setattr(self.par, attr, FakePar(attr, default))

    def op(self, path):
        return self.children_by_name.get(path.lstrip("./"))

    def create(self, op_type, name):
        child = (
            FakeComp("%s/%s" % (self.path, name))
            if op_type is handler_type("baseCOMP")
            else FakeDAT("%s/%s" % (self.path, name))
        )
        self.children_by_name[name] = child
        return child

    # -- extension resolution, the way TouchDesigner does it

    def evalExpression(self, expr):
        self.evals.append(expr)
        match = re.fullmatch(r"op\('\./(\w+)'\)\.module\.(\w+)\(me\)", expr)
        if match is None:
            # Every other form measured — `DemoExt(me)`, `mod('DemoExt')...`,
            # `iop.DemoExt` — resolves to nothing.
            raise NameError("name %r is not defined" % expr.split("(")[0])
        dat_name, class_name = match.groups()
        dat = self.children_by_name.get(dat_name)
        if not isinstance(dat, FakeDAT):
            raise AttributeError("no DAT named %r inside %s" % (dat_name, self.path))
        namespace: dict = {}
        exec(compile(dat.text, dat.path, "exec"), namespace)
        if class_name not in namespace:
            raise AttributeError(
                "module %r has no attribute %r" % (dat_name, class_name)
            )
        return namespace[class_name](self)

    def _reinit(self):
        self.reinits += 1
        blocks = self.seq.ext.numBlocks
        built = []
        self.ext = FakePars()
        for index in range(blocks):
            expr = getattr(self.par, "ext%dobject" % index).val
            if not expr:
                built.append(None)
                continue
            try:
                obj = self.evalExpression(expr)
            except Exception:
                # The measured behaviour: no error, no warning, just None.
                obj = None
            built.append(obj)
            if obj is not None:
                name = getattr(self.par, "ext%dname" % index).val or type(obj).__name__
                setattr(self.ext, name, obj)
        self.extensions = built


def handler_type(name):
    """The operator-type object the handler names (`baseCOMP`, `textDAT`).

    Injected into the handler's globals by the fixture below, because
    TouchDesigner puts them in the module's namespace and the host has none.
    """
    return _TYPES[name]


class _Type:
    def __init__(self, name):
        self.name = name


_TYPES = {"baseCOMP": _Type("baseCOMP"), "textDAT": _Type("textDAT")}


class FakeUndo:
    def __init__(self):
        self.log: list[str] = []

    def startBlock(self, name):
        self.log.append("start:%s" % name)

    def endBlock(self):
        self.log.append("end")

    def undo(self):
        self.log.append("undo")


@pytest.fixture
def td(monkeypatch):
    """The `td` globals the handler expects TouchDesigner to provide."""
    nodes: dict = {}
    undo = FakeUndo()
    monkeypatch.setattr(handler, "op", lambda path: nodes.get(path), raising=False)
    monkeypatch.setattr(
        handler, "ui", type("UI", (), {"undo": undo})(), raising=False
    )
    monkeypatch.setattr(handler, "baseCOMP", _TYPES["baseCOMP"], raising=False)
    monkeypatch.setattr(handler, "textDAT", _TYPES["textDAT"], raising=False)
    return nodes, undo


def build(nodes, **params):
    return handler.m_extension_add(params)


# -- the fake earns its keep ------------------------------------------------

def test_the_fake_refuses_the_form_the_wiki_shows(td):
    """Guards the guard: if the fake resolved `DemoExt(me)`, every test below
    would pass against a tool that generates an expression TouchDesigner
    silently ignores."""
    nodes, _undo = td
    comp = FakeComp("/project1/box")

    with pytest.raises(NameError):
        comp.evalExpression("DemoExt(me)")


# -- building on an existing COMP -------------------------------------------

def test_it_sets_the_three_parameters_and_the_expression_that_resolves(td):
    nodes, undo = td
    nodes["/project1/box"] = comp = FakeComp("/project1/box")

    result = build(
        nodes, path="/project1/box", class_name="DemoExt", code=GOOD_CODE
    )

    assert comp.par.ext0object.val == "op('./DemoExt').module.DemoExt(me)"
    assert comp.par.ext0name.val == ""
    assert comp.par.ext0promote.val is True
    assert comp.par.reinitextensions.pulses == 1
    assert result["ok"] is True
    assert result["path"] == "/project1/box"
    assert result["dat"] == "/project1/box/DemoExt"
    assert result["createdComp"] is False
    assert result["extension"] == "DemoExt"
    assert result["error"] is None
    assert result["reachable"] is True
    # One block, opened and closed, with nothing rolled back.
    assert undo.log == ["start:td-atlas extension DemoExt", "end"]


def test_the_class_is_actually_callable_afterwards(td):
    """The end the whole call exists for, checked through the fake's own
    resolution rather than through what the handler reports."""
    nodes, _undo = td
    nodes["/project1/box"] = comp = FakeComp("/project1/box")

    build(nodes, path="/project1/box", class_name="DemoExt", code=GOOD_CODE)

    assert comp.extensions[0].Hello(7) == "hello 7"
    assert comp.ext.DemoExt.Hello(8) == "hello 8"


def test_the_code_lands_in_a_dat_named_after_the_class(td):
    nodes, _undo = td
    nodes["/project1/box"] = comp = FakeComp("/project1/box")

    build(nodes, path="/project1/box", class_name="DemoExt", code=GOOD_CODE)

    assert comp.children_by_name["DemoExt"].text == GOOD_CODE


def test_an_existing_dat_of_that_name_is_rewritten_not_duplicated(td):
    nodes, _undo = td
    nodes["/project1/box"] = comp = FakeComp("/project1/box")
    stale = FakeDAT("/project1/box/DemoExt")
    stale.text = "class DemoExt:\n    pass\n"
    comp.children_by_name["DemoExt"] = stale

    build(nodes, path="/project1/box", class_name="DemoExt", code=GOOD_CODE)

    assert comp.children_by_name["DemoExt"] is stale
    assert stale.text == GOOD_CODE


def test_a_name_already_taken_by_a_non_dat_is_refused(td):
    nodes, undo = td
    nodes["/project1/box"] = comp = FakeComp("/project1/box")
    comp.children_by_name["DemoExt"] = FakeComp("/project1/box/DemoExt")

    with pytest.raises(TypeError, match="DemoExt"):
        build(nodes, path="/project1/box", class_name="DemoExt", code=GOOD_CODE)

    assert undo.log == ["start:td-atlas extension DemoExt", "end", "undo"]


def test_promote_off_and_a_chosen_extension_name_are_both_honoured(td):
    nodes, _undo = td
    nodes["/project1/box"] = comp = FakeComp("/project1/box")

    result = build(
        nodes,
        path="/project1/box",
        class_name="DemoExt",
        code=GOOD_CODE,
        extension_name="MyName",
        promote=False,
    )

    assert comp.par.ext0name.val == "MyName"
    assert comp.par.ext0promote.val is False
    assert result["extension"] == "MyName"
    assert comp.ext.MyName.Hello(1) == "hello 1"


def test_a_later_slot_grows_the_sequence_and_writes_its_own_parameters(td):
    nodes, _undo = td
    nodes["/project1/box"] = comp = FakeComp("/project1/box")

    result = build(
        nodes, path="/project1/box", class_name="DemoExt", code=GOOD_CODE, index=2
    )

    assert comp.seq.ext.numBlocks == 3
    assert comp.par.ext2object.val == "op('./DemoExt').module.DemoExt(me)"
    assert comp.par.ext0object.val == ""
    assert result["index"] == 2
    assert result["ok"] is True


# -- creating the COMP as well ----------------------------------------------

def test_it_can_create_the_component_it_extends(td):
    nodes, undo = td
    nodes["/project1"] = parent = FakeComp("/project1")

    result = build(
        nodes,
        parent="/project1",
        name="box",
        class_name="DemoExt",
        code=GOOD_CODE,
        position=[40, -10],
    )

    created = parent.children_by_name["box"]
    assert result["createdComp"] is True
    assert result["path"] == "/project1/box"
    assert result["dat"] == "/project1/box/DemoExt"
    assert (created.nodeX, created.nodeY) == (40.0, -10.0)
    assert created.extensions[0].Hello(2) == "hello 2"
    assert undo.log == ["start:td-atlas extension DemoExt", "end"]


def test_a_created_comp_lands_beside_its_neighbours_not_on_top_of_them(td):
    """The failure this closes: two calls in a row both left a COMP at (0, 0),
    one visible node with another underneath it. Placement goes through
    `_place_node`, the same one `m_op_create` uses — a second copy of the rule
    in this file would drift away from it."""
    nodes, _undo = td
    nodes["/project1"] = parent = FakeComp("/project1")
    sitting = FakeComp("/project1/already")
    sitting.nodeX, sitting.nodeY = 0.0, 0.0
    parent.children_by_name["already"] = sitting

    build(nodes, parent="/project1", name="box", class_name="DemoExt",
          code=GOOD_CODE)

    created = parent.children_by_name["box"]
    assert (created.nodeX, created.nodeY) != (0.0, 0.0)
    assert not handler._boxes_clash(
        handler._node_box(created), handler._node_box(sitting)
    )


def test_two_created_comps_in_a_row_do_not_share_a_spot(td):
    nodes, _undo = td
    nodes["/project1"] = parent = FakeComp("/project1")

    build(nodes, parent="/project1", name="one", class_name="DemoExt",
          code=GOOD_CODE)
    build(nodes, parent="/project1", name="two", class_name="DemoExt",
          code=GOOD_CODE)

    one = parent.children_by_name["one"]
    two = parent.children_by_name["two"]
    assert not handler._boxes_clash(
        handler._node_box(one), handler._node_box(two)
    )


def test_a_position_the_caller_asked_for_is_never_second_guessed(td):
    nodes, _undo = td
    nodes["/project1"] = parent = FakeComp("/project1")
    parent.children_by_name["already"] = FakeComp("/project1/already")

    build(nodes, parent="/project1", name="box", class_name="DemoExt",
          code=GOOD_CODE, position=[300, -120])

    created = parent.children_by_name["box"]
    assert (created.nodeX, created.nodeY) == (300.0, -120.0)


def test_an_existing_comp_is_not_moved(td):
    """`position` describes where to put a COMP being created. Aimed at one
    that is already in the network, the call adds an extension and leaves the
    layout alone."""
    nodes, _undo = td
    nodes["/project1/box"] = comp = FakeComp("/project1/box")
    comp.nodeX, comp.nodeY = 55.0, 66.0

    build(nodes, path="/project1/box", class_name="DemoExt", code=GOOD_CODE,
          position=[300, -120])

    assert (comp.nodeX, comp.nodeY) == (55.0, 66.0)


# -- the silent failure ----------------------------------------------------

def test_a_class_that_raises_in_init_comes_back_as_a_reported_failure(td):
    """Measured: `extensions[0]` is None and the COMP reports no error at all.
    The message is only recoverable by re-evaluating the expression."""
    nodes, undo = td
    nodes["/project1/box"] = comp = FakeComp("/project1/box")

    result = build(
        nodes, path="/project1/box", class_name="DemoExt", code=BROKEN_INIT
    )

    assert result["ok"] is False
    assert "RuntimeError" in result["error"]
    assert "boom in init" in result["error"]
    assert comp.extensions[0] is None
    # Both paths still come back: the caller fixes the DAT in place.
    assert result["path"] == "/project1/box"
    assert result["dat"] == "/project1/box/DemoExt"


def test_a_failed_init_is_not_rolled_back(td):
    """The structure asked for is correct; only the class body is wrong. A
    rollback would delete the DAT the caller now has to edit."""
    nodes, undo = td
    nodes["/project1/box"] = comp = FakeComp("/project1/box")

    build(nodes, path="/project1/box", class_name="DemoExt", code=BROKEN_INIT)

    assert undo.log == ["start:td-atlas extension DemoExt", "end"]
    assert "DemoExt" in comp.children_by_name


# -- refusing rather than guessing -----------------------------------------

def test_a_non_comp_target_is_refused_before_the_block_opens(td):
    nodes, undo = td
    nodes["/project1/noise1"] = FakeOp("/project1/noise1")

    with pytest.raises(TypeError) as exc:
        build(nodes, path="/project1/noise1", class_name="DemoExt", code=GOOD_CODE)

    assert "noiseTOP" in str(exc.value)
    assert not undo.log, "nothing was started, so nothing needs undoing"


def test_a_parent_that_cannot_create_is_refused(td):
    nodes, undo = td
    nodes["/project1/noise1"] = FakeOp("/project1/noise1")

    with pytest.raises(TypeError, match="noiseTOP"):
        build(
            nodes,
            parent="/project1/noise1",
            name="box",
            class_name="DemoExt",
            code=GOOD_CODE,
        )

    assert not undo.log


@pytest.mark.parametrize(
    "params",
    [
        {"path": "/project1/box", "parent": "/project1", "name": "box2"},
        {},
        {"parent": "/project1"},
        {"name": "box"},
    ],
)
def test_the_target_must_be_one_thing_or_the_other(td, params):
    nodes, undo = td
    nodes["/project1"] = FakeComp("/project1")
    nodes["/project1/box"] = FakeComp("/project1/box")

    with pytest.raises(ValueError):
        build(nodes, class_name="DemoExt", code=GOOD_CODE, **params)

    assert not undo.log


@pytest.mark.parametrize(
    "params",
    [
        {"class_name": "", "code": GOOD_CODE},
        {"class_name": "not an identifier", "code": GOOD_CODE},
        {"class_name": "DemoExt", "code": ""},
        {"class_name": "DemoExt", "code": GOOD_CODE, "index": -1},
        {"class_name": "DemoExt", "code": GOOD_CODE, "extension_name": "no spaces"},
    ],
)
def test_the_arguments_are_checked_before_anything_is_touched(td, params):
    nodes, undo = td
    nodes["/project1/box"] = FakeComp("/project1/box")

    with pytest.raises(ValueError):
        build(nodes, path="/project1/box", **params)

    assert not undo.log


# -- the scope guard -------------------------------------------------------

class FakeTableDAT:
    path = "/tdatlas/tdatlas_scopes"

    def __init__(self):
        self._rows: list[list[str]] = []

    def clear(self):
        self._rows = []

    def appendRow(self, cells):
        self._rows.append([str(cell) for cell in cells])

    def rows(self):
        return [list(row) for row in self._rows]

    @property
    def numRows(self):
        return len(self._rows)


@pytest.fixture
def claimed(monkeypatch):
    table = FakeTableDAT()
    monkeypatch.setattr(handler, "_scope_table", lambda create=False: table)
    monkeypatch.setattr(handler.time, "time", lambda: 1000.0)
    handler.m_claim_scope({"path": "/project1/audio", "owner": "agent-a", "ttl": 60})
    return table


def test_another_owners_claim_refuses_the_build_on_an_existing_comp(td, claimed):
    nodes, undo = td
    nodes["/project1/audio/box"] = FakeComp("/project1/audio/box")

    with pytest.raises(handler.ScopeHeld, match="agent-a"):
        build(
            nodes,
            path="/project1/audio/box",
            class_name="DemoExt",
            code=GOOD_CODE,
            owner="agent-b",
        )

    assert not undo.log


def test_the_claim_also_covers_the_component_about_to_be_created(td, claimed):
    """The parent is outside the claim and the child would be inside it — the
    guard has to see both paths, or the write slips in under the parent."""
    nodes, undo = td
    nodes["/project1"] = FakeComp("/project1")

    with pytest.raises(handler.ScopeHeld, match="agent-a"):
        build(
            nodes,
            parent="/project1",
            name="audio",
            class_name="DemoExt",
            code=GOOD_CODE,
            owner="agent-b",
        )

    assert not undo.log


def test_the_owner_of_the_claim_is_let_through(td, claimed):
    nodes, _undo = td
    nodes["/project1/audio/box"] = FakeComp("/project1/audio/box")

    result = build(
        nodes,
        path="/project1/audio/box",
        class_name="DemoExt",
        code=GOOD_CODE,
        owner="agent-a",
    )

    assert result["ok"] is True


def test_the_handler_registers_the_method():
    assert handler.METHODS["extension_add"] is handler.m_extension_add


# -- the MCP tool ----------------------------------------------------------

class FakeBridge:
    def __init__(self, result=None, raises=None):
        self.result = result if result is not None else {
            "path": "/project1/box",
            "dat": "/project1/box/DemoExt",
            "createdComp": True,
            "index": 0,
            "extension": "DemoExt",
            "promote": True,
            "set": {
                "ext0object": "op('./DemoExt').module.DemoExt(me)",
                "ext0name": "",
                "ext0promote": True,
            },
            "ok": True,
            "object": "<DemoExt object>",
            "error": None,
            "reachable": True,
        }
        self.raises = raises
        self.calls: list[tuple[str, dict]] = []
        self.ambiguity_warning = None
        self.version_warning = None

    def call(self, method, **params):
        self.calls.append((method, params))
        if self.raises is not None:
            raise self.raises
        return self.result


def tool(monkeypatch, fake, **kwargs):
    monkeypatch.setattr(server, "bridge", lambda: fake)
    return server.td_extension_add(**kwargs)


def test_the_tool_sends_the_code_the_class_and_the_owner(monkeypatch):
    fake = FakeBridge()

    text = tool(
        monkeypatch,
        fake,
        class_name="DemoExt",
        code=GOOD_CODE,
        parent="/project1",
        name="box",
        owner="agent-a",
    )

    assert fake.calls == [
        (
            "extension_add",
            {
                "path": None,
                "parent": "/project1",
                "name": "box",
                "class_name": "DemoExt",
                "code": GOOD_CODE,
                "extension_name": "",
                "promote": True,
                "index": 0,
                "position": None,
                "owner": "agent-a",
            },
        )
    ]
    assert "/project1/box" in text
    assert "/project1/box/DemoExt" in text
    assert "ext0object" in text


def test_an_existing_comp_is_sent_as_a_path_with_no_parent(monkeypatch):
    fake = FakeBridge()

    tool(monkeypatch, fake, class_name="DemoExt", code=GOOD_CODE, path="/project1/box")

    _method, params = fake.calls[0]
    assert params["path"] == "/project1/box"
    assert params["parent"] is None and params["name"] is None


def test_a_position_reaches_the_bridge(monkeypatch):
    """The handler reads `position`; without this parameter on the surface
    there is no road to it — the shape of defect this project has shipped
    three times already."""
    fake = FakeBridge()

    tool(
        monkeypatch,
        fake,
        class_name="DemoExt",
        code=GOOD_CODE,
        parent="/project1",
        name="box",
        position=[300, -120],
    )

    assert fake.calls[0][1]["position"] == [300, -120]


def test_the_reply_says_how_to_call_the_extension(monkeypatch):
    text = tool(
        monkeypatch, FakeBridge(), class_name="DemoExt", code=GOOD_CODE, path="/p/box"
    )

    assert "op('/project1/box').ext.DemoExt" in text


def test_a_syntax_error_is_refused_before_the_bridge(monkeypatch):
    fake = FakeBridge()

    text = tool(
        monkeypatch,
        fake,
        class_name="DemoExt",
        code="class DemoExt:\n    def __init__(self ownerComp):\n        pass\n",
        path="/project1/box",
    )

    assert not fake.calls, "broken code must not reach TouchDesigner"
    assert "does not parse" in text
    assert "line 2" in text
    # The honest caveat: this host's Python is not TouchDesigner's 3.11.
    assert "3.11" in text


def test_code_that_does_not_define_the_class_is_refused(monkeypatch):
    fake = FakeBridge()

    text = tool(
        monkeypatch,
        fake,
        class_name="DemoExt",
        code="class OtherExt:\n    pass\n",
        path="/project1/box",
    )

    assert not fake.calls
    assert "no top-level class 'DemoExt'" in text
    assert "OtherExt" in text


def test_a_class_nested_out_of_reach_is_refused(monkeypatch):
    """TouchDesigner reads the class off the DAT's module, so only a top-level
    class can be reached — a nested one would fail silently inside."""
    fake = FakeBridge()

    text = tool(
        monkeypatch,
        fake,
        class_name="DemoExt",
        code="def make():\n    class DemoExt:\n        pass\n",
        path="/project1/box",
    )

    assert not fake.calls
    assert "no top-level class" in text


@pytest.mark.parametrize(
    "kwargs",
    [
        {"class_name": "not valid", "path": "/project1/box"},
        {"class_name": "DemoExt", "extension_name": "not valid",
         "path": "/project1/box"},
        {"class_name": "DemoExt"},
        {"class_name": "DemoExt", "path": "/project1/box", "parent": "/project1",
         "name": "box2"},
    ],
)
def test_the_tool_refuses_a_bad_target_or_name_locally(monkeypatch, kwargs):
    fake = FakeBridge()

    text = tool(monkeypatch, fake, code=GOOD_CODE, **kwargs)

    assert not fake.calls
    assert text.startswith("error: ")
    assert "fix: " in text


def test_empty_code_is_refused(monkeypatch):
    fake = FakeBridge()

    text = tool(monkeypatch, fake, class_name="DemoExt", code="  \n",
                path="/project1/box")

    assert not fake.calls
    assert text.startswith("error: ")


def test_a_silent_init_failure_is_reported_as_a_failure(monkeypatch):
    fake = FakeBridge(
        result={
            "path": "/project1/box",
            "dat": "/project1/box/DemoExt",
            "createdComp": False,
            "index": 0,
            "extension": "DemoExt",
            "promote": True,
            "set": {"ext0object": "op('./DemoExt').module.DemoExt(me)"},
            "ok": False,
            "object": None,
            "error": "RuntimeError: boom in init",
            "reachable": None,
        }
    )

    text = tool(
        monkeypatch, fake, class_name="DemoExt", code=GOOD_CODE, path="/project1/box"
    )

    assert "NOT INITIALISED" in text
    assert "RuntimeError: boom in init" in text
    assert "/project1/box/DemoExt" in text
    assert "td_undo" in text
    assert "fix: " in text


def test_a_bridge_failure_comes_back_as_text_not_an_exception(monkeypatch):
    for failure in (
        BridgeUnavailable("TouchDesigner is not running"),
        BridgeError({"type": "ScopeHeld", "message": "agent-a holds it"},
                    "extension_add"),
    ):
        text = tool(
            monkeypatch,
            FakeBridge(raises=failure),
            class_name="DemoExt",
            code=GOOD_CODE,
            path="/project1/box",
        )
        assert text.startswith("error: ")
