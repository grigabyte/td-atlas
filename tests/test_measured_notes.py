"""Measured facts print under the parameter and the operator they are about.

The names are checked against the built index by hand when a note is added
(tests do not read the host's index); what is held here is that a note
reaches both surfaces, the MCP schema and `td-atlas op`.
"""

from __future__ import annotations

import argparse

import pytest

from td_atlas import cli
from td_atlas.atoms.store import AtomStore
from td_atlas.mcp import server


@pytest.fixture
def db(tmp_path, monkeypatch):
    store = AtomStore(tmp_path / "atlas.db")
    store.create()
    store.insert_ops([{"type": "rampTOP", "family": "TOP", "label": "Ramp"}])
    store.merge_runtime_params("rampTOP", [
        {"name": "type", "label": "Type", "style": "Menu", "is_menu": True,
         "menu_names": ["radial", "circular"], "page": "Ramp"},
    ])
    store.conn.commit()
    monkeypatch.setattr(server, "store", lambda: store)
    yield tmp_path / "atlas.db"
    store.close()


def test_the_schema_prints_the_note_under_its_parameter(db):
    text = server.td_operator_schema("rampTOP")
    assert "measured: radial is the ANGLE" in text


def test_the_cli_prints_it_too(db, capsys):
    args = argparse.Namespace(db=str(db), type="rampTOP", page="",
                              include_hidden=False, groups=False)
    assert cli.cmd_op(args) == 0
    assert "measured: radial is the ANGLE" in capsys.readouterr().out

