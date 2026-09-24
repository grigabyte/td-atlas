"""A bridge timeout says whether TouchDesigner is busy or asleep.

On 2026-09-24 the bridge stopped answering even `ping`, and the refusal said a
long script was blocking the main thread and to retry in smaller pieces. No
script was running: the process was alive at 0% CPU in state `S`, put to
sleep by macOS overnight (ПРОЧТИ-БОЛИ-АГЕНТА-2, point 5). The advice sent the
agent the wrong way.

No TouchDesigner and no real `ps` here: the transport raises `TimeoutError`
and the process reading is canned, so what is tested is the decision, not
the operating system.
"""

from __future__ import annotations

import pytest

from td_atlas.bridge import client as client_mod
from td_atlas.bridge.client import BridgeClient, BridgeUnavailable
from td_atlas.config import Instance
from td_atlas.mcp import hints

TD_PATH = "/Applications/TouchDesigner.app/Contents/MacOS/TouchDesigner"


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("TD_ATLAS_HOME", str(tmp_path))


def _timing_out(monkeypatch, ps_output, pid=4242, can_inspect=True):
    asked = []

    def fake_urlopen(request, timeout=None):
        raise TimeoutError("timed out")

    def fake_ps(args):
        asked.append(list(args))
        return ps_output

    monkeypatch.setattr(client_mod.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(client_mod, "_ps", fake_ps)
    monkeypatch.setattr(client_mod, "_CAN_INSPECT_PROCESS", can_inspect)
    client = BridgeClient(port=9977, token="", timeout=0.5)
    client._protocol_checked = True
    if pid:
        client.instance = Instance(port=9977, pid=pid)
    return client, asked


def _refusal(client) -> BridgeUnavailable:
    with pytest.raises(BridgeUnavailable) as caught:
        client.call("ping")
    return caught.value


def _one(cpu, state, pid=4242, path=TD_PATH):
    return " %d %s %s    %s\n" % (pid, cpu, state, path)


def test_an_idle_process_is_called_asleep_not_busy(monkeypatch):
    client, asked = _timing_out(monkeypatch, _one("0.0", "S"))
    exc = _refusal(client)
    assert exc.reason == "bridge_asleep"
    assert "0.0" in str(exc)
    assert "front" in str(exc)
    assert asked and "4242" in asked[0], "the registered pid is the one read"
    rendered = hints.failure(exc)
    assert "smaller pieces" not in rendered
    assert "front" in rendered.split("fix:", 1)[1]


def test_a_loaded_process_is_called_busy(monkeypatch):
    client, _ = _timing_out(monkeypatch, _one("98.5", "R"))
    exc = _refusal(client)
    assert exc.reason == "bridge_timeout"
    assert "98.5" in str(exc) and "busy" in str(exc)


def test_a_reading_in_between_is_reported_without_a_verdict(monkeypatch):
    client, _ = _timing_out(monkeypatch, _one("12.0", "S"))
    exc = _refusal(client)
    assert exc.reason == "bridge_timeout"
    assert "12.0" in str(exc)
    assert "asleep" not in str(exc) and "busy" not in str(exc)


def test_a_recycled_pid_is_not_read_as_touchdesigner(monkeypatch):
    client, _ = _timing_out(monkeypatch, _one("0.0", "S", path="/usr/bin/vim"))
    exc = _refusal(client)
    assert exc.reason == "bridge_timeout"
    assert "no TouchDesigner process" in str(exc)


def test_without_a_registered_pid_the_process_is_found_by_name(monkeypatch):
    listing = (
        "  101  3.0 S    /usr/sbin/cfprefsd\n"
        "  4242  0.1 S    %s\n" % TD_PATH
    )
    client, asked = _timing_out(monkeypatch, listing, pid=0)
    exc = _refusal(client)
    assert exc.reason == "bridge_asleep"
    assert "4242" in str(exc)


def test_two_touchdesigners_and_no_pid_give_no_verdict(monkeypatch):
    listing = "  4242  0.0 S  %s\n  4343  90.0 R  %s\n" % (TD_PATH, TD_PATH)
    client, _ = _timing_out(monkeypatch, listing, pid=0)
    exc = _refusal(client)
    assert exc.reason == "bridge_timeout"
    assert "4242" in str(exc) and "4343" in str(exc)


def test_no_process_found_is_said_as_it_is(monkeypatch):
    client, _ = _timing_out(monkeypatch, "", pid=0)
    exc = _refusal(client)
    assert exc.reason == "bridge_timeout"
    assert "no TouchDesigner process" in str(exc)


def test_where_the_process_cannot_be_read_the_old_message_stands(monkeypatch):
    client, asked = _timing_out(monkeypatch, _one("0.0", "S"), can_inspect=False)
    exc = _refusal(client)
    assert exc.reason == "bridge_timeout"
    assert "long-running script" in str(exc)
    assert asked == [], "nothing is spawned where there is no cheap reading"


def test_the_process_is_read_only_on_a_timeout(monkeypatch):
    import json

    class Ok:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return json.dumps({"ok": True, "result": {}}).encode()

    asked = []
    monkeypatch.setattr(client_mod.urllib.request, "urlopen",
                        lambda request, timeout=None: Ok())
    monkeypatch.setattr(client_mod, "_ps", lambda args: asked.append(args) or "")
    client = BridgeClient(port=9977, token="", timeout=0.5)
    client._protocol_checked = True
    client.call("ping")
    assert asked == []


def test_the_asleep_reason_has_its_own_repair():
    assert "front" in hints.from_record("BridgeUnavailable", "bridge_asleep").action
