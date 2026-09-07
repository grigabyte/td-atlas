"""`td_palette_load`: install a palette component in one call.

Nothing here needs TouchDesigner. The index is a scratch database with a
handful of palette rows and real files on disk, and the bridge is a fake that
records the parameters it was handed — which is the point of most of these
cases: the tool's job is to turn a component *name* into a checked absolute
.tox path, and to refuse rather than guess when it cannot.
"""

from __future__ import annotations

import pytest

from td_atlas import config as cfg
from td_atlas.atoms.store import AtomStore
from td_atlas.bridge.client import BridgeError, BridgeUnavailable
from td_atlas.component import handler
from td_atlas.mcp import server


# -- the fake world ----------------------------------------------------------

class FakeBridge:
    """Records one call and replays a prepared result.

    Carries the two warning attributes `server._warn` reads, because a fake
    missing them would fail every tool in this module for the wrong reason.
    """

    def __init__(self, result=None, raises=None):
        self.result = result if result is not None else {
            "path": "/project1/kantanMapper",
            "name": "kantanMapper",
            "type": "baseCOMP",
            "errors": None,
            "warnings": None,
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


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A scratch index whose palette rows point at .tox files that exist.

    `kantanMapper` is unique; `abletonLevel` is one of the fourteen names the
    real palette ships in two folders; `ghost` is indexed but has no file, the
    shape a stale index takes after TouchDesigner is moved or updated.
    """
    tox_dir = tmp_path / "Palette"
    tox_dir.mkdir()
    rows = [
        ("kantanMapper", "Mapping", "kantanMapper.tox", True),
        ("abletonLevel", "Live 11+", "live11/abletonLevel.tox", True),
        ("abletonLevel", "Live 9 & 10", "live9/abletonLevel.tox", True),
        ("ghost", "Tools", "ghost.tox", False),
    ]
    records = []
    for name, category, relative, on_disk in rows:
        tox = tox_dir / relative
        if on_disk:
            tox.parent.mkdir(parents=True, exist_ok=True)
            tox.write_bytes(b"not really a tox, but a file")
        records.append(
            {
                "name": name,
                "category": category,
                "path": str(tox),
                "doc_page": f"Palette-{name}",
                "summary": f"The {name} COMP.",
            }
        )

    db = tmp_path / "atlas.db"
    store = AtomStore(db)
    store.create()
    store.insert_palette(records)
    store.conn.commit()
    store.close()

    monkeypatch.setattr(cfg, "db_path", lambda: db)
    # server.store() caches the first AtomStore it opens for the life of the
    # process, so without this the patched db_path above is ignored whenever
    # another test opened an index first.
    monkeypatch.setattr(server, "_store", None)
    return tmp_path


def load(monkeypatch, bridge, **kwargs):
    monkeypatch.setattr(server, "bridge", lambda: bridge)
    return server.td_palette_load(**kwargs)


# -- turning a name into a path ---------------------------------------------

def test_looks_the_component_up_by_name_and_sends_the_indexed_path(
    world, monkeypatch
):
    fake = FakeBridge()

    text = load(monkeypatch, fake, name="kantanMapper", parent="/project1")

    assert fake.calls == [
        (
            "palette_load",
            {
                "parent": "/project1",
                "file": str(world / "Palette" / "kantanMapper.tox"),
                "name": None,
                "position": None,
                # Empty unless the caller claimed the area with td_claim_scope.
                "owner": "",
            },
        )
    ]
    assert "loaded kantanMapper [Mapping] into /project1" in text
    assert "/project1/kantanMapper" in text


def test_passes_the_rename_and_position_through(world, monkeypatch):
    fake = FakeBridge()

    load(
        monkeypatch,
        fake,
        name="kantanMapper",
        parent="/project1/sub",
        rename="mapper1",
        position=[120, -40],
    )

    _method, params = fake.calls[0]
    assert params["parent"] == "/project1/sub"
    assert params["name"] == "mapper1"
    assert params["position"] == [120, -40]


def test_an_empty_rename_is_sent_as_no_rename(world, monkeypatch):
    # The MCP signature cannot express "absent" for a string, so "" has to mean
    # it: sending name="" would ask TouchDesigner for an unnamed operator.
    fake = FakeBridge()

    load(monkeypatch, fake, name="kantanMapper", rename="")

    assert fake.calls[0][1]["name"] is None


# -- refusing rather than guessing ------------------------------------------

def test_two_components_with_one_name_are_refused_with_the_candidates(
    world, monkeypatch
):
    fake = FakeBridge()

    text = load(monkeypatch, fake, name="abletonLevel")

    assert not fake.calls, "an ambiguous name must not reach TouchDesigner"
    assert "2 palette components are named 'abletonLevel'" in text
    assert "Live 11+" in text and "Live 9 & 10" in text
    assert "category=" in text


def test_a_category_resolves_the_ambiguity(world, monkeypatch):
    fake = FakeBridge()

    text = load(monkeypatch, fake, name="abletonLevel", category="Live 11")

    assert fake.calls[0][1]["file"] == str(
        world / "Palette" / "live11" / "abletonLevel.tox"
    )
    assert "[Live 11+]" in text


def test_a_name_not_in_the_index_points_at_the_search_tool(world, monkeypatch):
    fake = FakeBridge()

    text = load(monkeypatch, fake, name="kantanmapper")

    assert not fake.calls
    assert "No palette component is named 'kantanmapper'" in text
    assert "td_palette" in text


def test_a_category_that_matches_nothing_is_reported_as_such(world, monkeypatch):
    fake = FakeBridge()

    text = load(monkeypatch, fake, name="kantanMapper", category="UI")

    assert not fake.calls
    assert "'UI'" in text


def test_an_indexed_component_missing_from_disk_fails_before_the_bridge(
    world, monkeypatch
):
    """The stale-index case: the row survives an installation that moved."""
    fake = FakeBridge()

    text = load(monkeypatch, fake, name="ghost")

    assert not fake.calls
    assert text.startswith("error: ")
    assert "no file there" in text
    assert "td-atlas build" in text


# -- reporting what actually happened --------------------------------------

def test_a_silently_renumbered_load_is_called_out(world, monkeypatch):
    # Measured: with no rename asked for, a sibling of the same name makes
    # loadTox renumber without complaint. Every later call needs the real path,
    # so the tool must not let that pass unmentioned.
    fake = FakeBridge(
        result={
            "path": "/project1/kantanMapper1",
            "name": "kantanMapper1",
            "type": "baseCOMP",
            "errors": None,
            "warnings": None,
        }
    )

    text = load(monkeypatch, fake, name="kantanMapper")

    assert "/project1/kantanMapper1" in text
    assert "already existed here" in text


def test_the_reported_path_is_the_bridge_path_not_the_requested_name(
    world, monkeypatch
):
    fake = FakeBridge(
        result={
            "path": "/project1/mapper1",
            "name": "mapper1",
            "type": "baseCOMP",
            "errors": None,
            "warnings": None,
        }
    )

    text = load(monkeypatch, fake, name="kantanMapper", rename="mapper1")

    assert "/project1/mapper1" in text
    # A refused rename never comes back as a different name — it comes back as
    # an error — so there is nothing to caveat here.
    assert "already existed here" not in text


def test_node_errors_and_warnings_come_back_with_the_load(world, monkeypatch):
    fake = FakeBridge(
        result={
            "path": "/project1/kantanMapper",
            "name": "kantanMapper",
            "type": "baseCOMP",
            "errors": "missing external .tox",
            "warnings": "clone source not found",
        }
    )

    text = load(monkeypatch, fake, name="kantanMapper")

    assert "ERROR: missing external .tox" in text
    assert "warning: clone source not found" in text


def test_a_bridge_failure_comes_back_as_text_not_an_exception(world, monkeypatch):
    """An exception surfaces to the agent as an opaque ToolError; see AGENTS.md."""
    for failure in (
        BridgeUnavailable("TouchDesigner is not running"),
        BridgeError({"type": "TypeError", "message": "not a COMP"}, "palette_load"),
    ):
        text = load(
            monkeypatch, FakeBridge(raises=failure), name="kantanMapper"
        )
        assert text.startswith("error: ")


# -- the bridge side --------------------------------------------------------

def test_the_handler_registers_palette_load():
    # The tool above is useless against a bridge that does not know the method,
    # and the registry is the only thing that connects the two names.
    assert handler.METHODS["palette_load"] is handler.m_palette_load


class FakeOp:
    """The slice of TouchDesigner's OP that `_op_summary` and the loader touch.

    `name` is a property because assigning it is a rename, and the two name
    collisions behave in opposite ways — measured on a live 2025.32460, see
    handler.m_palette_load. A second `loadTox` of the same file renumbers
    silently (`checker` then `checker1`); assigning a name a sibling already
    holds raises `tdError: Invalid or duplicate operator name.` and changes
    nothing. `tdError` is a TouchDesigner builtin with no host equivalent, so
    it is stood in for by a RuntimeError carrying the same message.
    """

    def __init__(self, path, op_type="baseCOMP", family="COMP", owner=None):
        self._name = path.rsplit("/", 1)[-1]
        self._parent_path = path.rsplit("/", 1)[0]
        self.owner = owner
        self.OPType = op_type
        self.family = family
        self.valid = True
        self.inputs = []
        self.children = []
        self.nodeX = 0.0
        self.nodeY = 0.0

    @property
    def path(self):
        return f"{self._parent_path}/{self._name}"

    @property
    def name(self):
        return self._name

    @name.setter
    def name(self, wanted):
        taken = self.owner.taken if self.owner else set()
        if wanted in taken and wanted != self._name:
            raise RuntimeError("Invalid or duplicate operator name.")
        taken.discard(self._name)
        taken.add(wanted)
        self._name = wanted

    def errors(self, recurse=False):
        return ""

    def warnings(self, recurse=False):
        return ""


class FakeComp(FakeOp):
    """A COMP whose loadTox renames on collision, the way the docs describe."""

    def __init__(self, path, taken=(), fails=False):
        super().__init__(path, op_type="containerCOMP")
        self.taken = set(taken)
        self.fails = fails
        self.loaded: list[str] = []
        self.child = None

    def loadTox(self, filepath):
        self.loaded.append(filepath)
        if self.fails:
            raise RuntimeError("corrupt tox")
        stem = _stem(filepath)
        candidate, n = stem, 0
        while candidate in self.taken:
            n += 1
            candidate = f"{stem}{n}"
        self.taken.add(candidate)
        self.child = FakeOp(f"{self.path}/{candidate}", owner=self)
        return self.child


def _stem(filepath):
    return filepath.rsplit("/", 1)[-1].rsplit(".", 1)[0]


class FakeUndo:
    def __init__(self):
        self.log: list[str] = []

    def startBlock(self, name):
        self.log.append(f"start:{name}")

    def endBlock(self):
        self.log.append("end")

    def undo(self):
        self.log.append("undo")


@pytest.fixture
def td(monkeypatch):
    """Inject the `td` globals the handler expects TouchDesigner to provide."""
    nodes: dict[str, FakeOp] = {}
    undo = FakeUndo()
    monkeypatch.setattr(handler, "op", lambda path: nodes.get(path), raising=False)
    monkeypatch.setattr(
        handler, "ui", type("UI", (), {"undo": undo})(), raising=False
    )
    return nodes, undo


def test_the_handler_loads_into_an_undo_block(td):
    nodes, undo = td
    nodes["/project1"] = FakeComp("/project1")

    result = handler.m_palette_load(
        {"parent": "/project1", "file": "/p/kantanMapper.tox"}
    )

    assert nodes["/project1"].loaded == ["/p/kantanMapper.tox"]
    assert undo.log == ["start:td-atlas load kantanMapper.tox", "end"]
    assert result["path"] == "/project1/kantanMapper"
    assert result["file"] == "/p/kantanMapper.tox"


def test_the_handler_applies_a_free_name_and_a_position(td):
    nodes, _undo = td
    nodes["/project1"] = FakeComp("/project1")

    result = handler.m_palette_load(
        {
            "parent": "/project1",
            "file": "/p/kantanMapper.tox",
            "name": "mapper1",
            "position": [10, 20],
        }
    )

    child = nodes["/project1"].child
    assert result["name"] == "mapper1"
    assert result["path"] == child.path == "/project1/mapper1"
    assert (child.nodeX, child.nodeY) == (10.0, 20.0)


def test_a_load_beside_a_namesake_reports_the_number_TouchDesigner_added(td):
    # Measured: loadTox never fails on a collision, it renumbers. The summary
    # has to carry that name — it is the only handle the caller gets.
    nodes, _undo = td
    nodes["/project1"] = FakeComp("/project1", taken={"kantanMapper"})

    result = handler.m_palette_load(
        {"parent": "/project1", "file": "/p/kantanMapper.tox"}
    )

    assert result["name"] == "kantanMapper1"
    assert result["path"] == "/project1/kantanMapper1"


def test_a_rename_onto_a_taken_name_rolls_the_load_back(td):
    # The opposite of the case above, and the reason the rename sits inside the
    # undo block: TouchDesigner refuses the name rather than numbering it, and
    # a component left behind under a name nobody asked for is worse than an
    # error. Measured live as `tdError: Invalid or duplicate operator name.`
    nodes, undo = td
    nodes["/project1"] = FakeComp("/project1", taken={"mapper1"})

    with pytest.raises(ValueError) as exc:
        handler.m_palette_load(
            {
                "parent": "/project1",
                "file": "/p/kantanMapper.tox",
                "name": "mapper1",
            }
        )

    assert undo.log == ["start:td-atlas load kantanMapper.tox", "end", "undo"]
    # The message has to name both the name asked for and the one the load got,
    # or the caller cannot tell which half failed or what to retry with.
    message = str(exc.value)
    assert "mapper1" in message and "kantanMapper" in message
    assert "/project1" in message


def test_a_failed_load_closes_its_block_and_undoes_it(td):
    nodes, undo = td
    nodes["/project1"] = FakeComp("/project1", fails=True)

    with pytest.raises(RuntimeError):
        handler.m_palette_load(
            {"parent": "/project1", "file": "/p/kantanMapper.tox"}
        )

    # Leaving the block open would swallow every later edit into it.
    assert undo.log == ["start:td-atlas load kantanMapper.tox", "end", "undo"]


def test_a_parent_that_cannot_hold_a_component_is_named_in_the_error(td):
    nodes, undo = td
    nodes["/project1/noise1"] = FakeOp("/project1/noise1", "noiseTOP", "TOP")

    with pytest.raises(TypeError) as exc:
        handler.m_palette_load(
            {"parent": "/project1/noise1", "file": "/p/kantanMapper.tox"}
        )

    assert "noiseTOP" in str(exc.value)
    assert not undo.log, "nothing was started, so nothing needs undoing"


def test_a_missing_file_argument_is_refused_before_any_lookup(td):
    _nodes, undo = td

    with pytest.raises(ValueError):
        handler.m_palette_load({"parent": "/project1"})

    assert not undo.log
