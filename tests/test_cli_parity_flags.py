"""The three knobs the CLI grew when the parity gaps were closed, exercised.

`test_cli_mcp_parity.py` proves the flags *exist* on both surfaces; nothing
there proves the CLI's new ones are wired to anything. A flag that parses and
is then ignored is the worse failure of the two, because it answers.

The index is built here rather than borrowed: four operators and a handful of
parameters are enough to tell "narrowed to CHOP" from "not narrowed", and the
tests then run on a machine with no TouchDesigner and no built index.
"""

from __future__ import annotations

import pytest

from td_atlas.atoms.store import AtomStore
from td_atlas.cli import main

_OPS = [
    {"type": "noiseTOP", "family": "TOP", "label": "Noise",
     "summary": "generates noise patterns for images"},
    {"type": "noiseCHOP", "family": "CHOP", "label": "Noise",
     "summary": "generates noise channels"},
    {"type": "blurTOP", "family": "TOP", "label": "Blur",
     "summary": "blurs an image"},
    {"type": "levelTOP", "family": "TOP", "label": "Level",
     "summary": "adjusts brightness of an image"},
]

_PARAMS = [
    {"op_type": "noiseTOP", "name": "amp", "label": "Amplitude"},
    {"op_type": "noiseTOP", "name": "period", "label": "Period"},
]


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "atlas.db"
    store = AtomStore(path)
    store.create()
    store.insert_ops(_OPS)
    store.insert_params(_PARAMS)
    # `style` is what the runtime pass fills in, and it is also what makes a
    # row settable (see AtomStore.parameters). One visible, one hidden.
    store.conn.execute(
        "UPDATE params SET style='Float', hidden=0 WHERE name='amp'"
    )
    store.conn.execute(
        "UPDATE params SET style='Float', hidden=1 WHERE name='period'"
    )
    store.conn.execute("UPDATE ops SET probed=1")
    store.rebuild_fts()
    store.conn.commit()
    store.close()
    return str(path)


def _run(capsys, db, *argv) -> str:
    assert main(["--db", db, *argv]) == 0
    return capsys.readouterr().out


def test_search_family_narrows_to_one_family(capsys, db):
    everything = _run(capsys, db, "search", "noise")
    assert "noiseTOP" in everything and "noiseCHOP" in everything

    chops = _run(capsys, db, "search", "noise", "--family", "CHOP")
    assert "noiseCHOP" in chops
    assert "noiseTOP" not in chops


def test_search_limit_caps_the_rows(capsys, db):
    wide = _run(capsys, db, "search", "image", "--limit", "3")
    narrow = _run(capsys, db, "search", "image", "--limit", "1")
    assert len(narrow.strip().splitlines()) == 1
    assert len(wide.strip().splitlines()) > 1


def test_op_hides_hidden_parameters_unless_asked(capsys, db):
    plain = _run(capsys, db, "op", "noiseTOP")
    assert "amp" in plain
    assert "period" not in plain

    shown = _run(capsys, db, "op", "noiseTOP", "--include-hidden")
    assert "period" in shown
