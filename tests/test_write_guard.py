"""Neither surface repacks over a file that is already there.

The guard existed only in the MCP tool. `td-atlas project write` went straight
into `rebuild()`, and `toecollapse` moves whatever sits at its destination
aside to a `.bkp1` name — so the CLI could quietly displace a user's file
while the MCP tool
refused the same request. The check now lives in `rebuild()`, which both reach.

Nothing here unpacks anything: the refusal happens before `toeexpand` is looked
for, which is also why it is testable without a TouchDesigner installation.
"""

from __future__ import annotations

import json

import pytest

from td_atlas import cli
from td_atlas.mcp import server
from td_atlas.project.rebuild import ExpandError, OutputExists, rebuild

DUMP = json.dumps({"operators": {}})


def test_rebuild_refuses_an_output_that_exists(tmp_path):
    source = tmp_path / "source.toe"
    source.write_bytes(b"toe")
    output = tmp_path / "out.toe"
    output.write_bytes(b"someone's work")

    with pytest.raises(OutputExists) as raised:
        rebuild(source, DUMP, output)

    assert str(output) in str(raised.value)
    assert output.read_bytes() == b"someone's work"


def test_the_refusal_is_an_expand_error_so_existing_handlers_still_catch_it():
    assert issubclass(OutputExists, ExpandError)


def test_the_refusal_comes_before_anything_is_unpacked(tmp_path, monkeypatch):
    """No installation is looked for, so the guard holds without TouchDesigner."""
    source = tmp_path / "source.toe"
    source.write_bytes(b"toe")
    output = tmp_path / "out.toe"
    output.write_text("x")

    def explode(*args, **kwargs):
        raise AssertionError("expand() must not run for a refused write")

    monkeypatch.setattr("td_atlas.project.rebuild.expand", explode)

    with pytest.raises(OutputExists):
        rebuild(source, DUMP, output)


def test_the_cli_refuses_and_says_why(tmp_path, capsys):
    source = tmp_path / "source.toe"
    source.write_bytes(b"toe")
    output = tmp_path / "out.toe"
    output.write_bytes(b"someone's work")
    dump = tmp_path / "dump.json"
    dump.write_text(DUMP)

    code = cli.main(
        ["project", "write", str(source), "--text", str(dump), "-o", str(output)]
    )
    captured = capsys.readouterr()
    text = captured.err + captured.out

    assert code == 1
    assert "already exists" in text
    assert output.read_bytes() == b"someone's work"


def test_the_mcp_tool_still_answers_with_the_recovery_hint(tmp_path):
    source = tmp_path / "source.toe"
    source.write_bytes(b"toe")
    output = tmp_path / "out.toe"
    output.write_text("x")

    text = server.td_project_write(str(source), DUMP, str(output))

    assert "already exists" in text
    assert "cause: " in text and "fix: " in text
    assert output.read_text() == "x"
