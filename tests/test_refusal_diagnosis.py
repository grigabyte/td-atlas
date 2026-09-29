"""A refused connection says whether TouchDesigner is running at all.

On 2026-09-25 an agent was told "Is TouchDesigner running with the td-atlas
bridge installed? Run 'td-atlas install'". TouchDesigner was running with a
new project and the staged files matched the package; the one missing step
was the bootstrap line in the textport, and finding that out took four calls
and an `lsof` (agent report 4, point 3).

No TouchDesigner and no real `ps` here, as in test_timeout_diagnosis: the
transport raises the refusal and the process listing is canned.
"""

from __future__ import annotations

import argparse
import shutil
import urllib.error
from pathlib import Path

import pytest

from td_atlas import cli
from td_atlas.bridge import client as client_mod
from td_atlas.bridge.client import BridgeClient, BridgeUnavailable
from td_atlas.mcp import hints

TD_PATH = "/Applications/TouchDesigner.app/Contents/MacOS/TouchDesigner"
SHIPPED = Path(client_mod._handler.__file__).parent


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("TD_ATLAS_HOME", str(tmp_path))
    return tmp_path


def _stage(home):
    for name in ("bootstrap.py", "handler.py"):
        shutil.copyfile(SHIPPED / name, home / name)


def _refused(monkeypatch, ps_output, can_inspect=True):
    def fake_urlopen(request, timeout=None):
        raise urllib.error.URLError(ConnectionRefusedError(61, "Connection refused"))

    monkeypatch.setattr(client_mod.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(client_mod, "_ps", lambda args: ps_output)
    monkeypatch.setattr(client_mod, "_CAN_INSPECT_PROCESS", can_inspect)
    client = BridgeClient(port=9977, token="", timeout=0.5)
    client._protocol_checked = True
    with pytest.raises(BridgeUnavailable) as caught:
        client.call("ping")
    return caught.value


def test_a_running_touchdesigner_with_current_files_gets_the_textport_line(
    home, monkeypatch
):
    _stage(home)
    exc = _refused(monkeypatch, f" 812 3.0 S    {TD_PATH}\n")

    assert exc.reason == "bridge_not_loaded"
    assert "TouchDesigner is running (pid 812)" in str(exc)
    assert f"exec(open('{home / 'bootstrap.py'}').read())" in str(exc)
    assert "not needed" in str(exc)
    assert "textport" in hints.classify(exc).action


def test_stale_staged_files_send_the_agent_to_install_first(home, monkeypatch):
    _stage(home)
    (home / "handler.py").write_text("# an older bridge\n")
    exc = _refused(monkeypatch, f" 812 3.0 S    {TD_PATH}\n")

    assert exc.reason == "bridge_not_loaded"
    assert "Run 'td-atlas install' first" in str(exc)
    assert "handler.py" in str(exc)


def test_no_touchdesigner_process_says_so(home, monkeypatch):
    exc = _refused(monkeypatch, " 77 0.0 S    /usr/sbin/cfprefsd\n")

    assert exc.reason == "bridge_unreachable"
    assert "No TouchDesigner process is running" in str(exc)


def test_where_the_process_cannot_be_read_the_old_message_stands(home, monkeypatch):
    exc = _refused(monkeypatch, f" 812 3.0 S    {TD_PATH}\n", can_inspect=False)

    assert exc.reason == "bridge_unreachable"
    assert "Is TouchDesigner running" in str(exc)


def test_doctor_names_the_line_for_a_running_touchdesigner(home, monkeypatch):
    _stage(home)
    (home / "config.json").write_text('{"port": 9977}')
    monkeypatch.setattr(client_mod, "_ps", lambda args: f" 812 3.0 S    {TD_PATH}\n")
    monkeypatch.setattr(client_mod, "_CAN_INSPECT_PROCESS", True)
    monkeypatch.setattr(cli.cfg, "port_listening", lambda port: False)

    check = cli.check_bridge(argparse.Namespace(port=None, project=None))

    assert check.status == cli.WARN
    assert "pid 812" in check.detail
    assert "exec(open(" in check.fix
