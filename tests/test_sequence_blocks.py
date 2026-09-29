"""Members of a parameter sequence past its first block are writable.

The index records a sequence's first block only (`const0name`), and a fresh
Constant CHOP has one block, so `const1name` was refused before sending and,
sent anyway, was no parameter at all. Two agents moved that work into
td_exec, outside the undo block and the name check (reports 3 and 4, a GLSL
TOP's `array1name` the second case). Measured on 2025.32460:
`seq.const.numBlocks = 3` creates const1*/const2*, and `par.const = 5` changes
nothing.
"""

from __future__ import annotations

import pytest

from td_atlas.atoms.store import AtomStore
from td_atlas.atoms.validate import validate_params
from td_atlas.component import handler


# -- the host's name check --------------------------------------------------

@pytest.fixture
def store(tmp_path):
    db = AtomStore(tmp_path / "atlas.db")
    db.create()
    db.insert_ops([{"type": "constantCHOP", "family": "CHOP", "label": "Constant"}])
    db.merge_runtime_params("constantCHOP", [
        {"name": "const", "label": "Constant", "style": "Sequence",
         "is_sequence": True, "page": "Constant"},
        {"name": "const0name", "label": "Name", "style": "Str",
         "is_string": True, "page": "Constant"},
        {"name": "const0value", "label": "Value", "style": "Float",
         "is_number": True, "page": "Constant"},
    ])
    db.conn.commit()
    yield db
    db.close()


def _problems(store, values):
    return [(p.parameter, p.message)
            for p in validate_params(store, "constantCHOP", values).problems]


def test_a_later_block_member_is_judged_as_the_first_blocks(store):
    assert _problems(store, {"const1name": "a", "const7value": 0.5}) == []
    # And by the same rules: a string where the first block takes a string.
    assert _problems(store, {"const2name": 3})[0][0] == "const2name"


def test_a_misspelt_member_is_still_refused(store):
    assert _problems(store, {"const1nme": "a"})[0] == ("const1nme",
                                                      "no such parameter")


def test_the_sequence_parameter_takes_a_block_count(store):
    assert _problems(store, {"const": 3}) == []
    assert "how many blocks" in _problems(store, {"const": "many"})[0][1]


# -- the bridge's write -----------------------------------------------------

class Par:
    def __init__(self, name, sequence=False):
        self.name = name
        self.isSequence = sequence
        self.val = 0

    def eval(self):
        return self.val


class Seq:
    def __init__(self, owner, name, members):
        self.owner, self.name, self.members = owner, name, members
        self._blocks = 0
        self.numBlocks = 1

    @property
    def numBlocks(self):
        return self._blocks

    @numBlocks.setter
    def numBlocks(self, count):
        self._blocks = count
        for index in range(count):
            for member in self.members:
                name = f"{self.name}{index}{member}"
                if not hasattr(self.owner.par, name):
                    setattr(self.owner.par, name, Par(name))

    @property
    def blockPars(self):
        return [Par(f"{self.name}0{m}") for m in self.members]


class Pars:
    pass


class FakeOp:
    path = "/p/c"
    OPType = "constantCHOP"

    def __init__(self):
        self.par = Pars()
        self.par.const = Par("const", sequence=True)
        self.seq = [Seq(self, "const", ["name", "value"])]


@pytest.fixture
def chop(monkeypatch):
    monkeypatch.setattr(handler, "_read_back", lambda t, n, p: p.val)
    return FakeOp()


def test_writing_a_later_member_grows_the_sequence_to_reach_it(chop):
    applied = handler._apply_pars(chop, {"const2name": "b"})
    assert applied == {"const2name": "b"}
    assert chop.seq[0].numBlocks == 3


def test_a_number_on_the_sequence_parameter_sets_the_block_count(chop):
    applied = handler._apply_pars(chop, {"const": 4})
    assert applied == {"const": 4} and chop.seq[0].numBlocks == 4
    assert chop.par.const.val == 0


def test_a_name_that_is_no_member_still_fails(chop):
    with pytest.raises(AttributeError):
        handler._apply_pars(chop, {"const1nme": "b"})
