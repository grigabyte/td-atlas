"""Protocol version handshake between the host client and the in-TD bridge.

No TouchDesigner required: the transport (`urllib.request.urlopen`) is
replaced with a fake that answers `ping` with a chosen `protocol` field and
everything else with a stub result, so the version check can be exercised in
isolation.
"""

from __future__ import annotations

import json

import pytest

from td_atlas.bridge import client as client_mod
from td_atlas.bridge.client import (
    EXPECTED_PROTOCOL_VERSION,
    MIN_PROTOCOL_VERSION,
    BridgeClient,
    BridgeUnavailable,
)


class _FakeResponse:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body


def _install_fake_bridge(monkeypatch, protocol, calls):
    """Patch urlopen to answer ping with `protocol` and count calls per method."""

    def fake_urlopen(request, timeout=None):
        body = json.loads(request.data.decode())
        method = body["method"]
        calls.append(method)
        if method == "ping":
            result = {} if protocol is None else {"protocol": protocol}
            return _FakeResponse({"ok": True, "result": result})
        return _FakeResponse({"ok": True, "result": {"method": method}})

    monkeypatch.setattr(client_mod.urllib.request, "urlopen", fake_urlopen)


def _client() -> BridgeClient:
    return BridgeClient(port=1, token="", host="127.0.0.1", timeout=1.0)


# -- the three outcomes ------------------------------------------------------

def test_matching_version_is_silent(monkeypatch):
    calls: list[str] = []
    _install_fake_bridge(monkeypatch, EXPECTED_PROTOCOL_VERSION, calls)
    client = _client()

    result = client.errors()

    assert result == {"method": "errors"}
    assert client.version_warning is None


def test_older_but_supported_bridge_gets_a_warning(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(client_mod, "MIN_PROTOCOL_VERSION", 1)
    monkeypatch.setattr(client_mod, "EXPECTED_PROTOCOL_VERSION", 3)
    _install_fake_bridge(monkeypatch, 2, calls)
    client = _client()

    result = client.errors()

    assert result == {"method": "errors"}
    assert client.version_warning is not None
    assert "2" in client.version_warning
    assert "3" in client.version_warning
    assert "td-atlas install" in client.version_warning
    assert "td-atlas reload" in client.version_warning


@pytest.mark.parametrize(
    "protocol,expect_snippets",
    [
        (0, ["0", "td-atlas install", "td-atlas reload"]),  # below minimum
        (None, ["td-atlas install", "td-atlas reload"]),      # missing entirely
        ("2", ["td-atlas install", "td-atlas reload"]),       # non-numeric
    ],
)
def test_too_old_or_missing_version_refuses(monkeypatch, protocol, expect_snippets):
    calls: list[str] = []
    monkeypatch.setattr(client_mod, "MIN_PROTOCOL_VERSION", 1)
    monkeypatch.setattr(client_mod, "EXPECTED_PROTOCOL_VERSION", 3)
    _install_fake_bridge(monkeypatch, protocol, calls)
    client = _client()

    with pytest.raises(BridgeUnavailable) as excinfo:
        client.errors()

    message = str(excinfo.value)
    for snippet in expect_snippets:
        assert snippet in message


def test_newer_than_expected_refuses_with_host_upgrade_instruction(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(client_mod, "MIN_PROTOCOL_VERSION", 1)
    monkeypatch.setattr(client_mod, "EXPECTED_PROTOCOL_VERSION", 3)
    _install_fake_bridge(monkeypatch, 4, calls)
    client = _client()

    with pytest.raises(BridgeUnavailable) as excinfo:
        client.errors()

    message = str(excinfo.value)
    assert "4" in message
    assert "3" in message
    assert "Update the td-atlas package on this host" in message


def test_a_bridge_one_version_below_the_minimum_is_refused(monkeypatch):
    """The real constants, not monkeypatched ones — the version answers for it.

    This is the case the number exists for. A bridge staged before `errors`
    took a `path` argument accepts the argument, ignores it and walks from `/`,
    so its reply describes a different question than the one asked. Nothing in
    the reply distinguishes that from a correct one; the protocol number does,
    which is why `path` moved the number to 7 and why the minimum went with
    it. The host used to detect that bridge by an absent `root` field in the
    reply — a convention the protocol never stated.
    """
    calls: list[str] = []
    _install_fake_bridge(monkeypatch, MIN_PROTOCOL_VERSION - 1, calls)
    client = _client()

    with pytest.raises(BridgeUnavailable) as excinfo:
        client.errors(path="/project1")

    message = str(excinfo.value)
    assert str(MIN_PROTOCOL_VERSION - 1) in message
    assert "td-atlas reload" in message
    # Refused at connect: the walk was never asked for.
    assert "errors" not in calls


# -- once per client, not per call -------------------------------------------

def test_version_check_runs_once_per_client(monkeypatch):
    calls: list[str] = []
    _install_fake_bridge(monkeypatch, EXPECTED_PROTOCOL_VERSION, calls)
    client = _client()

    client.errors()
    client.errors()
    client.op_info("/project1")

    assert calls.count("ping") == 1
    assert calls.count("errors") == 2
    assert calls.count("op_info") == 1


def test_a_failed_check_does_not_stick(monkeypatch):
    """TouchDesigner being briefly unreachable must not skip the check forever."""
    calls: list[str] = []

    def flaky_urlopen(request, timeout=None):
        body = json.loads(request.data.decode())
        method = body["method"]
        calls.append(method)
        if len(calls) <= 2:
            # First client.errors() call: both the ping check and the real
            # "errors" request hit the same down TouchDesigner.
            raise client_mod.urllib.error.URLError("connection refused")
        if method == "ping":
            return _FakeResponse({"ok": True, "result": {"protocol": 0}})
        return _FakeResponse({"ok": True, "result": {"method": method}})

    monkeypatch.setattr(client_mod.urllib.request, "urlopen", flaky_urlopen)
    monkeypatch.setattr(client_mod, "MIN_PROTOCOL_VERSION", 1)
    client = _client()

    # First call: TouchDesigner unreachable. The transport error surfaces,
    # and it must not be mistaken for "checked".
    with pytest.raises(BridgeUnavailable):
        client.errors()

    # Second call: TouchDesigner is back, but its bridge is below the
    # minimum. The check must still run — not have been consumed already.
    with pytest.raises(BridgeUnavailable) as excinfo:
        client.errors()
    assert "0" in str(excinfo.value)


def test_default_constants_are_sane():
    # EXPECTED_PROTOCOL_VERSION must come from handler.py, not a hand-copied
    # number — this pins that the import wiring actually resolves to an int.
    assert isinstance(EXPECTED_PROTOCOL_VERSION, int)
    assert isinstance(MIN_PROTOCOL_VERSION, int)
    assert MIN_PROTOCOL_VERSION <= EXPECTED_PROTOCOL_VERSION
