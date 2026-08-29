"""Building the bridge .tox offline, and reading it back.

The build shells out to `toecollapse` and the check to `toeexpand`, so both
need a TouchDesigner installation — but never a running one. They skip when
there is no install, like the other integration tests.
"""

from __future__ import annotations

import json
import struct
import subprocess
from pathlib import Path

import pytest

from td_atlas.component import handler as bridge_handler
from td_atlas.project.release import (
    COMPONENT_NAME,
    HANDLER_DAT,
    PANEL_TOP,
    SERVER_DAT,
    build_tox,
    write_tree,
)


def _installed():
    from td_atlas.install import InstallNotFound, discover

    try:
        return discover()
    except InstallNotFound:
        return None


needs_td = pytest.mark.skipif(
    _installed() is None, reason="no TouchDesigner installation"
)

PORT = 9312  # deliberately not the default, so a stale default cannot pass


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """Point config and the expansion cache at a scratch directory."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("TD_ATLAS_HOME", str(home))
    (home / "config.json").write_text(json.dumps({"port": PORT, "token": ""}))
    return home


def _reexpand(tox: Path, into: Path, install) -> Path:
    """Unpack `tox` with toeexpand and return the resulting `.dir`."""
    from td_atlas.project.expand import _tool

    into.mkdir(parents=True, exist_ok=True)
    working = into / tox.name
    working.write_bytes(tox.read_bytes())
    subprocess.run(
        [str(_tool(install, "toeexpand")), working.name],
        cwd=into, capture_output=True, text=True, timeout=600,
    )
    # toeexpand exits non-zero on success; the directory is the only signal.
    expanded = into / f"{tox.name}.dir"
    assert expanded.is_dir(), "toeexpand did not unpack the built component"
    return expanded


def test_the_laid_out_tree_carries_the_status_panel(tmp_path):
    """Host-only: the panel's files are written, whatever toecollapse does.

    Kept separate from the round trip below so that "the .tox has a panel" is
    checked on a machine with no TouchDesigner on it too.
    """
    from td_atlas.install import TDInstall

    root = tmp_path / "TdAtlas.tox.dir"
    names = write_tree(root, PORT, TDInstall(Path("/nowhere"), Path("/nowhere/tfs"), "2025.0", None))

    assert f"{COMPONENT_NAME}/{PANEL_TOP}.n" in names
    assert f"{COMPONENT_NAME}/{PANEL_TOP}.parm" in names
    assert (root / COMPONENT_NAME / f"{PANEL_TOP}.n").read_text().splitlines()[0] == (
        "TOP:text"
    )
    parms = (root / COMPONENT_NAME / f"{PANEL_TOP}.parm").read_text()
    values = dict(
        line.split(" 0 ", 1)
        for line in parms.splitlines()
        if line.strip() and line.strip() != "?"
    )
    assert values["text"] == bridge_handler.PANEL_PLACEHOLDER
    assert values == dict(
        [("text", bridge_handler.PANEL_PLACEHOLDER)]
        + list(bridge_handler.PANEL_PARS)
    )


def test_the_shipped_placeholder_fits_what_a_parm_line_can_hold():
    """One ASCII line, no leading or trailing space — see `_parms`."""
    text = bridge_handler.PANEL_PLACEHOLDER
    assert text.strip() == text and text
    assert "\n" not in text and "\r" not in text
    assert text.isascii()


@needs_td
def test_builds_a_tox_that_expands_back_into_the_bridge(
    tmp_path, isolated_home
):
    install = _installed()
    output = tmp_path / "out" / "TdAtlas.tox"

    built = build_tox(output, install=install)
    assert built == output and output.exists()
    # Nothing was written beside the requested output but the output itself.
    assert sorted(p.name for p in output.parent.iterdir()) == ["TdAtlas.tox"]

    tree = _reexpand(output, tmp_path / "check", install)

    # Both nodes are present, at the right place in the tree.
    handler_n = tree / COMPONENT_NAME / f"{HANDLER_DAT}.n"
    bridge_n = tree / COMPONENT_NAME / f"{SERVER_DAT}.n"
    assert (tree / f"{COMPONENT_NAME}.n").exists()
    assert handler_n.exists() and bridge_n.exists()

    # ...and of the right type.
    assert (tree / f"{COMPONENT_NAME}.n").read_text().splitlines()[0] == "COMP:base"
    assert handler_n.read_text().splitlines()[0] == "DAT:text"
    assert bridge_n.read_text().splitlines()[0] == "DAT:webserver"

    # The status panel survived the round trip through toecollapse, as a
    # Text TOP carrying the parameters bootstrap.py gives its own copy.
    panel_n = tree / COMPONENT_NAME / f"{PANEL_TOP}.n"
    assert panel_n.exists()
    assert panel_n.read_text().splitlines()[0] == "TOP:text"
    panel_parms = (tree / COMPONENT_NAME / f"{PANEL_TOP}.parm").read_text()
    panel_values = dict(
        line.split(" 0 ", 1)
        for line in panel_parms.splitlines()
        if line.strip() and line.strip() != "?"
    )
    assert panel_values["text"] == bridge_handler.PANEL_PLACEHOLDER
    for name, value in bridge_handler.PANEL_PARS:
        assert panel_values[name] == value

    # The server is wired to the handler, on the configured port, active.
    parms = (tree / COMPONENT_NAME / f"{SERVER_DAT}.parm").read_text()
    values = dict(
        line.split(" 0 ", 1)
        for line in parms.splitlines()
        if line.strip() and line.strip() != "?"
    )
    assert values["callbacks"] == HANDLER_DAT
    assert values["port"] == str(PORT)
    assert values["active"] == "1"

    # The handler text is the module source, byte for byte. The payload is
    # everything after the 27-byte prologue, whose last field is its length.
    raw = (tree / COMPONENT_NAME / f"{HANDLER_DAT}.text").read_bytes()
    length = struct.unpack(">I", raw[23:27])[0]
    payload = raw[27:]
    assert len(payload) == length
    source = (
        Path(__file__).parent.parent
        / "src" / "td_atlas" / "component" / "handler.py"
    ).read_bytes()
    assert payload == source


@needs_td
def test_the_built_tox_reads_as_a_project(tmp_path, isolated_home):
    """The offline reader sees the same two operators, canonically typed."""
    from td_atlas.project import load_file

    output = tmp_path / "TdAtlas.tox"
    build_tox(output, install=_installed())

    project = load_file(output)
    by_name = {node.name: node for node in project.walk()}
    assert by_name[HANDLER_DAT].op_type == "textDAT"
    assert by_name[SERVER_DAT].op_type == "webserverDAT"
    assert by_name[PANEL_TOP].op_type == "textTOP"
    assert by_name[HANDLER_DAT].text.startswith('"""The td-atlas RPC handler.')
