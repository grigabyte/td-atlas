"""`td-atlas reload` is the one command a protocol refusal must not stop.

A bridge older than MIN_PROTOCOL_VERSION is refused, and the
refusal names `td-atlas reload` as the repair. But reload dials the same client
and would be refused for the same reason — the command whose job is to replace
the out-of-date handler would be the one command that cannot run against it.

It rides on `exec`, which has been in the handler's method table since protocol
1 (checked against the first commit of `component/handler.py`), so the request
shape is one any bridge understands. Not verified against a live protocol-1
bridge — none exists on this machine.
"""

from __future__ import annotations

import argparse
import json

import pytest

from td_atlas import cli
from td_atlas.bridge import client as client_mod
from td_atlas.bridge.client import BridgeClient, BridgeUnavailable


class _FakeResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body


@pytest.fixture
def bridge_speaking(monkeypatch):
    """A fake bridge answering `ping` with the protocol version asked for."""

    def install(protocol):
        seen = []

        def fake_urlopen(request, timeout=None):
            body = json.loads(request.data.decode())
            seen.append(body["method"])
            if body["method"] == "ping":
                result = {} if protocol is None else {"protocol": protocol}
                return _FakeResponse({"ok": True, "result": result})
            return _FakeResponse(
                {"ok": True, "result": {"stdout": "handler replaced\n"}}
            )

        monkeypatch.setattr(client_mod.urllib.request, "urlopen", fake_urlopen)
        return seen

    return install


def _client():
    return BridgeClient(port=1, token="", host="127.0.0.1", timeout=1.0)


def test_an_ordinary_client_still_refuses_a_bridge_below_the_minimum(
    bridge_speaking,
):
    bridge_speaking(1)
    client = _client()

    with pytest.raises(BridgeUnavailable):
        client.errors()


def test_a_reload_client_reaches_a_bridge_below_the_minimum(bridge_speaking):
    seen = bridge_speaking(1)
    client = _client()
    client.enforce_protocol = False

    result = client.exec("1 + 1")

    assert "exec" in seen
    assert result["stdout"] == "handler replaced\n"
    assert "below the minimum" in client.version_warning


def test_a_reload_client_also_reaches_a_bridge_newer_than_this_host(
    bridge_speaking,
):
    """Reload replaces the handler in both directions, so both refusals lift."""
    bridge_speaking(client_mod.EXPECTED_PROTOCOL_VERSION + 1)
    client = _client()
    client.enforce_protocol = False

    client.exec("1 + 1")

    assert "newer than" in client.version_warning


def test_a_bridge_that_reports_no_version_at_all_is_still_reloadable(
    bridge_speaking,
):
    bridge_speaking(None)
    client = _client()
    client.enforce_protocol = False

    client.exec("1 + 1")

    assert "no protocol version" in client.version_warning


def test_the_reload_command_lifts_the_check_and_prints_the_mismatch(
    monkeypatch, tmp_path, bridge_speaking, capsys
):
    bridge_speaking(1)
    monkeypatch.setenv("TD_ATLAS_HOME", str(tmp_path))
    client = _client()
    monkeypatch.setattr(cli, "_client", lambda args: client)

    code = cli.cmd_reload(argparse.Namespace(port=None, project=None))
    captured = capsys.readouterr()
    text = captured.out + captured.err

    assert code == 0
    assert client.enforce_protocol is False
    assert "bridge reloaded" in text
    assert "warning:" in text


def test_the_refusal_still_names_reload_as_the_way_out(bridge_speaking):
    bridge_speaking(1)
    client = _client()

    with pytest.raises(BridgeUnavailable) as raised:
        client.errors()

    assert "td-atlas reload" in str(raised.value)
