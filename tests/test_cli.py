"""Offline tests for the install/status CLI plumbing. No TouchDesigner needed."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from td_atlas.cli import (
    broken_editable_install_diagnosis,
    broken_env_diagnosis,
    mcp_command,
    mcp_connection_line,
    write_mcp_json,
)


# -- MCP connection string ---------------------------------------------------

def test_mcp_command_uses_current_interpreter_unresolved():
    command = mcp_command()
    # Not resolve()'d: a venv's bin/python is normally a symlink to a base
    # interpreter, and following it defeats the venv (no pyvenv.cfg found
    # next to the base binary -> its site-packages, and the editable
    # install in it, are invisible). Measured directly: invoking the
    # unresolved venv path answers MCP's initialize; invoking the resolved
    # target raises ModuleNotFoundError for td_atlas.
    assert command[0] == sys.executable
    assert command[1:] == ["-m", "td_atlas.cli", "mcp"]
    # Still robust to cwd: nothing relative, nothing depending on PATH lookup.
    assert Path(command[0]).is_absolute()


def test_mcp_connection_line_shape():
    line = mcp_connection_line()
    assert line.startswith("claude mcp add td-atlas -- ")
    assert line.endswith("-m td_atlas.cli mcp")


# -- --write-mcp-json ---------------------------------------------------------

def test_write_mcp_json_creates_file(tmp_path):
    path = write_mcp_json(tmp_path)
    assert path == tmp_path / ".mcp.json"
    data = json.loads(path.read_text())
    assert "td-atlas" in data["mcpServers"]
    entry = data["mcpServers"]["td-atlas"]
    assert entry["args"] == ["-m", "td_atlas.cli", "mcp"]


def test_write_mcp_json_preserves_other_servers(tmp_path):
    existing = {
        "mcpServers": {
            "other-server": {"command": "/usr/bin/other", "args": ["serve"]}
        }
    }
    path = tmp_path / ".mcp.json"
    path.write_text(json.dumps(existing))

    write_mcp_json(tmp_path)

    data = json.loads(path.read_text())
    assert "other-server" in data["mcpServers"]
    assert data["mcpServers"]["other-server"] == existing["mcpServers"]["other-server"]
    assert "td-atlas" in data["mcpServers"]


def test_write_mcp_json_preserves_other_top_level_keys(tmp_path):
    existing = {"mcpServers": {}, "someOtherTopLevelKey": {"nested": True}}
    path = tmp_path / ".mcp.json"
    path.write_text(json.dumps(existing))

    write_mcp_json(tmp_path)

    data = json.loads(path.read_text())
    assert data["someOtherTopLevelKey"] == {"nested": True}


def test_write_mcp_json_is_idempotent(tmp_path):
    write_mcp_json(tmp_path)
    write_mcp_json(tmp_path)

    path = tmp_path / ".mcp.json"
    data = json.loads(path.read_text())
    assert list(data["mcpServers"].keys()).count("td-atlas") == 1
    assert len(data["mcpServers"]) == 1


def test_write_mcp_json_rejects_corrupt_existing_file(tmp_path):
    path = tmp_path / ".mcp.json"
    path.write_text("{not json")

    try:
        write_mcp_json(tmp_path)
    except ValueError as exc:
        assert "not valid JSON" in str(exc)
    else:
        raise AssertionError("expected ValueError on corrupt .mcp.json")

    # The corrupt file must survive untouched — no silent overwrite.
    assert path.read_text() == "{not json"


# -- broken-environment diagnosis --------------------------------------------

def test_broken_env_diagnosis_flags_missing_base_interpreter(tmp_path):
    venv = tmp_path / "venv"
    venv.mkdir()
    missing_home = tmp_path / "nonexistent-python-install"
    (venv / "pyvenv.cfg").write_text(f"home = {missing_home}\nversion = 3.11.0\n")

    diagnosis = broken_env_diagnosis(venv)

    assert diagnosis is not None
    assert str(missing_home) in diagnosis
    assert "reinstall" in diagnosis.lower() or "uv venv" in diagnosis


def test_broken_env_diagnosis_clean_for_intact_venv(tmp_path):
    venv = tmp_path / "venv"
    venv.mkdir()
    real_home = tmp_path / "real-python-install"
    real_home.mkdir()
    (venv / "pyvenv.cfg").write_text(f"home = {real_home}\nversion = 3.11.0\n")

    assert broken_env_diagnosis(venv) is None


def test_broken_env_diagnosis_none_without_pyvenv_cfg(tmp_path):
    # A system interpreter, or a `uv tool run` environment: no venv, no fault.
    assert broken_env_diagnosis(tmp_path) is None


# -- broken editable install (.pth present, never reaches sys.path) ---------
#
# This reproduces a bug measured live: `uv venv` + `uv pip install -e .` on
# Python 3.14.6 leaves `_editable_impl_td_atlas.pth` in site-packages naming
# the correct `src` directory, but a freshly launched interpreter from that
# same venv cannot `import td_atlas` at all — the directory never reaches
# sys.path. See `broken_editable_install_diagnosis`'s docstring for the
# exact evidence; these tests fabricate the same shape without depending on
# that machine-specific venv.

def test_broken_editable_install_diagnosis_flags_unwired_pth(tmp_path):
    site_packages = tmp_path / "site-packages"
    site_packages.mkdir()
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (site_packages / "_editable_impl_td_atlas.pth").write_text(str(src_dir) + "\n")

    diagnosis = broken_editable_install_diagnosis(
        site_packages, sys_path=["/some/other/path"], env={}
    )

    assert diagnosis is not None
    assert str(src_dir) in diagnosis
    assert "ModuleNotFoundError" in diagnosis


def test_broken_editable_install_diagnosis_clean_when_wired(tmp_path):
    site_packages = tmp_path / "site-packages"
    site_packages.mkdir()
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (site_packages / "_editable_impl_td_atlas.pth").write_text(str(src_dir) + "\n")

    diagnosis = broken_editable_install_diagnosis(
        site_packages, sys_path=[str(src_dir), "/other"], env={}
    )

    assert diagnosis is None


def test_broken_editable_install_diagnosis_flags_pythonpath_masked_case(tmp_path):
    # The documented workaround (PYTHONPATH=<src>) puts the *same* directory
    # the .pth names onto sys.path — a naive "is target on sys.path" check
    # would go quiet here, exactly on the invocation the bug is about.
    site_packages = tmp_path / "site-packages"
    site_packages.mkdir()
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (site_packages / "_editable_impl_td_atlas.pth").write_text(str(src_dir) + "\n")

    diagnosis = broken_editable_install_diagnosis(
        site_packages,
        sys_path=[str(src_dir), "/other"],
        env={"PYTHONPATH": str(src_dir)},
    )

    assert diagnosis is not None
    assert "PYTHONPATH" in diagnosis
    assert "ModuleNotFoundError" in diagnosis


def test_broken_editable_install_diagnosis_none_without_pth(tmp_path):
    site_packages = tmp_path / "site-packages"
    site_packages.mkdir()

    assert (
        broken_editable_install_diagnosis(site_packages, sys_path=[], env={})
        is None
    )


# -- what `release-tox` says about authentication ----------------------------

def _release_epilogue(tmp_path, monkeypatch, capsys, config):
    """Run cmd_release_tox with the .tox build stubbed out; return its output.

    Only the closing advice is under test here — building the file itself is
    test_release.py's job and needs a TouchDesigner install.
    """
    import argparse

    from td_atlas import cli
    from td_atlas.project import release as release_mod

    home = tmp_path / "home"
    home.mkdir()
    (home / "config.json").write_text(json.dumps(config))
    monkeypatch.setenv("TD_ATLAS_HOME", str(home))
    monkeypatch.setattr(release_mod, "build_tox", lambda output: Path(output))

    assert cli.cmd_release_tox(argparse.Namespace(output=str(tmp_path / "T.tox"))) == 0
    return capsys.readouterr().out


def test_release_tox_says_the_token_is_read_from_the_config(tmp_path, monkeypatch, capsys):
    out = _release_epilogue(
        tmp_path, monkeypatch, capsys, {"port": 9977, "token": "s3cret"}
    )

    assert "No token is baked into the .tox" in out
    assert "reads the token itself" in out
    assert str(tmp_path / "home" / "config.json") in out
    assert "accepts any" not in out
    assert "s3cret" not in out


def test_release_tox_names_the_open_bridge_when_there_is_no_token(
    tmp_path, monkeypatch, capsys
):
    out = _release_epilogue(tmp_path, monkeypatch, capsys, {"port": 9977, "token": ""})

    assert "holds no token yet" in out
    assert "accept any caller on this machine" in out
    assert "td-atlas install" in out
