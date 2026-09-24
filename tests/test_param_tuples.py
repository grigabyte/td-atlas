"""Tuple members a size menu adds, which the index used to lose.

Second agent report, item 4: `td_build` refused `amp2` on a noisePOP with
"no such parameter (try: amp0, map)", while the running TouchDesigner has it.

Why `tx ty tz` survived and `amp1 amp2` did not. The shipped help documents
both as one group ('t', 'amp'), so the members only ever come from the probe,
which lists `node.pars()` on a fresh node. Translate is a fixed XYZW tuple and
all three members are listed. Amplitude is sized by the node's own
`parsize` menu, default '1', and `pars()` lists only the members the current
size shows. Measured on 2025.32460 (2026-09-24), a noisePOP in a non-cooking
sandbox, created with initialize=False:

    parsize '1' -> amp0            '3' -> amp0 amp1 amp2
    parsize '2' -> amp0 amp1       '4' -> amp0 amp1 amp2 amp3

with exp, offset and positive following the same menu, and the set back to
amp0 once parsize is restored. Setting parsize needs no cook. `par.amp2` does
answer at parsize '1' (so the report's "nz.par.amp2 is there" held), but no
listing shows it, and writing to it there raises "Index out of range".

So the probe steps every size menu — a Menu whose entries are all digits —
through its values, records the parameters each step adds with the first
value that shows them, and restores the menu. Nothing here needs
TouchDesigner: the probe snippet runs against a fake node that behaves as
measured above, and the merge, the schema and the validator run on the
payload it produces.
"""

from __future__ import annotations

import json

import pytest

from td_atlas.atoms import probe
from td_atlas.atoms.store import AtomStore
from td_atlas.atoms.validate import validate_params
from td_atlas.mcp import server

# -- a noisePOP as measured -----------------------------------------------------


class _Named:
    def __init__(self, name):
        self.name = name


class FakePar:
    def __init__(self, node, name, style, group, vec_index=0, default=0.0,
                 menu=None, page="Noise"):
        self._node = node
        self.name = name
        self.label = name
        self.style = style
        self.default = default
        self.page = _Named(page)
        self.order = 0
        self.vecIndex = vec_index
        self.parGroup = _Named(group)
        self.readOnly = False
        self.hidden = False
        self.enableExpr = None
        self.isMenu = menu is not None
        self.isPulse = False
        self.isToggle = False
        self.isNumber = menu is None
        self.isString = False
        self.isOP = False
        self.isSequence = False
        self.menuNames = menu
        self.menuLabels = menu
        self.min = 0.0
        self.max = 1.0
        self.clampMin = False
        self.clampMax = False
        self.normMin = 0.0
        self.normMax = 1.0
        self._val = default

    @property
    def val(self):
        return self._val

    @val.setter
    def val(self, value):
        self._val = value
        self._node.writes.append((self.name, value))


class FakeNoisePOP:
    """`pars()` shows amp0..amp{parsize-1}, the way the live node does."""

    family = "POP"
    minInputs = 0
    maxInputs = 1
    outputConnectors = [object()]
    isCOMP = False
    isFilter = False

    def __init__(self):
        self.writes = []
        self.pages = [_Named("Noise"), _Named("Transform")]
        self.parsize = FakePar(self, "parsize", "Menu", "parsize",
                               default="1", menu=["1", "2", "3", "4"])
        self.seed = FakePar(self, "seed", "Float", "seed", default=1.0)
        self.tx = FakePar(self, "tx", "XYZW", "t", 0, page="Transform")
        self.ty = FakePar(self, "ty", "XYZW", "t", 1, page="Transform")
        self.tz = FakePar(self, "tz", "XYZW", "t", 2, page="Transform")
        self.amps = [FakePar(self, f"amp{i}", "Float", "amp", i, default=0.1)
                     for i in range(4)]
        self.destroyed = False

    def pars(self):
        size = int(self.parsize.val)
        return [self.parsize, self.seed, *self.amps[:size],
                self.tx, self.ty, self.tz]

    def destroy(self):
        self.destroyed = True


class FakeSandbox:
    def __init__(self):
        self.allowCooking = True
        self.made = []

    def create(self, op_type, name, initialize=True):
        assert initialize is False
        node = FakeNoisePOP()
        self.made.append(node)
        return node


class FakeContainer:
    def __init__(self):
        self.sandbox = FakeSandbox()

    def op(self, name):
        return self.sandbox if name == "probe_sandbox" else None


def _run_probe_chunk(types):
    container = FakeContainer()
    scope = {
        "op": lambda path: container if path == "/tdatlas" else None,
        "baseCOMP": object(),
    }
    exec(probe._PROBE_CHUNK % {"types": json.dumps(types)}, scope)
    return scope["result"], container.sandbox


@pytest.fixture(scope="module")
def probed():
    result, sandbox = _run_probe_chunk(["noisePOP"])
    return result["noisePOP"], sandbox


def test_the_probe_records_the_members_a_size_menu_adds(probed):
    entry, _ = probed
    names = [p["name"] for p in entry["params"]]
    for member in ("amp0", "amp1", "amp2", "amp3", "tx", "ty", "tz"):
        assert names.count(member) == 1, names


def test_each_added_member_carries_the_setting_that_shows_it(probed):
    entry, _ = probed
    by_name = {p["name"]: p for p in entry["params"]}
    assert by_name["amp0"].get("appears_when") is None
    assert by_name["amp1"]["appears_when"] == "parsize=2"
    assert by_name["amp2"]["appears_when"] == "parsize=3"
    assert by_name["amp3"]["appears_when"] == "parsize=4"
    assert by_name["amp2"]["group"] == "amp"
    assert by_name["amp2"]["vec_index"] == 2


def test_a_size_menu_that_cannot_be_stepped_is_reported_not_swallowed(monkeypatch):
    real = FakePar.val.fset

    def refuse_three(self, value):
        if self.name == "parsize" and value == "3":
            raise RuntimeError("refused")
        real(self, value)

    monkeypatch.setattr(FakePar, "val", FakePar.val.setter(refuse_three))
    result, _ = _run_probe_chunk(["noisePOP"])
    entry = result["noisePOP"]
    assert entry["size_menu_errors"] == ["parsize: RuntimeError: refused"]
    # What the steps before the failure found is still kept.
    assert "amp1" in [p["name"] for p in entry["params"]]


def test_the_probe_puts_the_menu_back(probed):
    _, sandbox = probed
    node = sandbox.made[0]
    assert node.writes[-1] == ("parsize", "1")
    assert node.destroyed


# -- the index, the schema and the validator ------------------------------------


@pytest.fixture
def store(tmp_path, probed):
    entry, _ = probed
    db = AtomStore(tmp_path / "atlas.db")
    db.create()
    db.insert_ops([{"type": "noisePOP", "family": "POP", "label": "Noise"}])
    # The shipped help documents the groups, not the members.
    db.insert_params([
        {"op_type": "noisePOP", "name": "amp", "label": "Amplitude",
         "summary": "The noise values amplitude.", "par_type": "Float"},
        {"op_type": "noisePOP", "name": "t", "label": "Translate",
         "summary": "Translate the points.", "par_type": "XYZW"},
    ])
    db.merge_runtime_params("noisePOP", entry["params"])
    db.conn.commit()
    yield db
    db.close()


def test_the_index_knows_every_member(store):
    rows = {p["name"]: p for p in store.parameters("noisePOP")}
    for member in ("amp0", "amp1", "amp2", "amp3"):
        assert rows[member]["settable"], member
    assert rows["amp2"]["summary"] == "The noise values amplitude."
    assert rows["amp2"]["appears_when"] == "parsize=3"


def test_amp2_is_accepted_where_the_report_saw_it_refused(store):
    result = validate_params(store, "noisePOP", {"parsize": "3", "amp2": 0.5})
    assert result.ok, result.render()
    # On an existing node the size is unknown here, so it is not second-guessed.
    assert validate_params(store, "noisePOP", {"amp2": 0.5}).ok


def test_a_member_written_below_its_size_is_refused_with_the_size(store):
    result = validate_params(store, "noisePOP", {"amp2": 0.5}, fresh=True)
    assert not result.ok
    assert result.problems[0].suggestions == ["parsize='3'"]
    also = validate_params(store, "noisePOP", {"parsize": "2", "amp2": 0.5})
    assert not also.ok


def test_the_size_has_to_come_before_the_member_it_shows(store):
    # `pars` are applied in the order given (handler `_apply_pars`).
    late = validate_params(store, "noisePOP", {"amp2": 0.5, "parsize": "3"})
    assert not late.ok
    assert "first" in late.problems[0].message


def test_a_miss_inside_a_tuple_names_the_tuple_not_the_nearest_string(store):
    result = validate_params(store, "noisePOP", {"amp7": 0.5})
    assert not result.ok
    problem = result.problems[0]
    assert problem.suggestions == ["amp0", "amp1", "amp2", "amp3"]
    assert "tuple" in problem.message
    beside = validate_params(store, "noisePOP", {"tw": 0.5})
    assert beside.problems[0].suggestions == ["tx", "ty", "tz"]


def test_the_schema_says_which_setting_shows_a_member(store):
    rows = {p["name"]: p for p in store.parameters("noisePOP")}
    assert "parsize" in server._fmt_param(rows["amp2"])
    assert "parsize" not in server._fmt_param(rows["amp0"])


def test_an_index_built_before_the_column_still_takes_a_probe(tmp_path, probed):
    """`td-atlas probe` over an index `build` wrote with the old schema."""
    entry, _ = probed
    db = AtomStore(tmp_path / "old.db")
    db.create()
    db.conn.execute("ALTER TABLE params DROP COLUMN appears_when")
    db.insert_ops([{"type": "noisePOP", "family": "POP", "label": "Noise"}])
    db.merge_runtime_params("noisePOP", entry["params"])
    rows = {p["name"]: p for p in db.parameters("noisePOP")}
    assert rows["amp2"]["appears_when"] == "parsize=3"
    db.close()
