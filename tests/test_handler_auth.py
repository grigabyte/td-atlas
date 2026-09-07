"""The handler reads its own token, and is loud when it has none.

`bootstrap.py` used to bake the token into the handler text before writing it
into the Text DAT. A released `.tox` has nowhere to bake anything — one file
goes to every machine — so the handler reads `~/.td-atlas/config.json` itself.
None of this needs TouchDesigner: the module level is plain stdlib, and the
Web Server DAT hands `onHTTPRequest` ordinary dicts.
"""

from __future__ import annotations

import importlib
import json
import os
from pathlib import Path

import pytest

from td_atlas.component import handler
from windows_gaps import posix_access_refusal_only


@pytest.fixture
def td_home(tmp_path, monkeypatch):
    """A scratch ~/.td-atlas, with the handler's token state wound back."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("TD_ATLAS_HOME", str(home))
    monkeypatch.setattr(handler, "AUTH_TOKEN", "")
    monkeypatch.setattr(handler, "_TOKEN_LOADED", False)
    return home


def _write_config(home: Path, payload) -> Path:
    path = home / "config.json"
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload))
    return path


def test_importing_the_module_reads_nothing_and_prints_nothing(td_home, capsys):
    """The host imports this module for PROTOCOL_VERSION; it must stay inert.

    Reading the config at import time would give `td-atlas` on the host a
    surprise side effect, and would print into anything that imports it.
    """
    _write_config(td_home, {"token": "s3cret-token"})

    importlib.reload(handler)

    # The number itself is not this test's business — that it survives a
    # reload without the module touching disk is.
    assert isinstance(handler.PROTOCOL_VERSION, int)
    assert handler.AUTH_TOKEN == ""
    assert handler._TOKEN_LOADED is False
    assert capsys.readouterr().out == ""


def test_token_comes_from_the_config_file(td_home, capsys):
    _write_config(td_home, {"port": 9977, "token": "s3cret-token"})

    assert handler._load_token() == "s3cret-token"
    assert handler.AUTH_TOKEN == "s3cret-token"
    assert capsys.readouterr().out == ""


def test_a_missing_config_leaves_the_bridge_up_and_says_so(td_home, capsys):
    assert handler._load_token() == ""

    out = capsys.readouterr().out
    assert "WARNING: no auth token" in out
    assert "cannot read" in out
    assert str(td_home / "config.json") in out


def test_malformed_json_is_reported_not_raised(td_home, capsys):
    _write_config(td_home, "{not json at all")

    assert handler._load_token() == ""

    out = capsys.readouterr().out
    assert "WARNING: no auth token" in out
    assert "malformed JSON" in out


def test_a_config_without_a_token_is_reported(td_home, capsys):
    """`td-atlas install --no-auth` is a deliberate open bridge — still loud."""
    _write_config(td_home, {"port": 9977, "token": ""})

    assert handler._load_token() == ""
    assert "no token in" in capsys.readouterr().out


def test_a_config_that_is_not_an_object_is_reported(td_home, capsys):
    _write_config(td_home, [1, 2, 3])

    assert handler._load_token() == ""
    assert "does not hold a JSON object" in capsys.readouterr().out


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root reads unreadable files",
)
@posix_access_refusal_only
def test_unreadable_config_is_reported_not_raised(td_home, capsys):
    path = _write_config(td_home, {"token": "unreachable"})
    path.chmod(0o000)
    try:
        assert handler._load_token() == ""
    finally:
        path.chmod(0o600)

    out = capsys.readouterr().out
    assert "WARNING: no auth token" in out
    assert "cannot read" in out


def test_home_defaults_to_the_dot_directory(monkeypatch):
    monkeypatch.delenv("TD_ATLAS_HOME", raising=False)
    expected = os.path.join(
        os.path.expanduser("~"), ".td-atlas", "config.json"
    )
    assert handler._config_path() == expected


# -- the token as the request sees it ---------------------------------------

def _request(token=None, method="no-such-method"):
    request = {"data": json.dumps({"method": method})}
    if token is not None:
        request["X-TD-Atlas-Token"] = token
    return request


def _status(response):
    return response["statusCode"], json.loads(response["data"])


def test_a_request_without_the_token_is_refused(td_home):
    _write_config(td_home, {"token": "s3cret-token"})

    status, body = _status(handler.onHTTPRequest(None, _request(), {}))
    assert status == 401
    assert body["error"]["type"] == "Unauthorized"


def test_a_request_with_the_token_gets_past_the_gate(td_home):
    _write_config(td_home, {"token": "s3cret-token"})

    # An unknown method proves only that authentication let the request
    # through; every real method needs TouchDesigner's globals.
    status, body = _status(
        handler.onHTTPRequest(None, _request("s3cret-token"), {})
    )
    assert status == 404
    assert body["error"]["type"] == "UnknownMethod"


def test_the_config_is_read_once_not_once_per_request(td_home, capsys):
    """Requests run on TouchDesigner's main thread; none of them touch disk."""
    path = _write_config(td_home, {"token": "s3cret-token"})

    handler.onHTTPRequest(None, _request("s3cret-token"), {})
    path.unlink()

    status, _ = _status(handler.onHTTPRequest(None, _request("s3cret-token"), {}))
    assert status == 404, "the token was re-read from disk on a later request"
    # ...and a wrong token is still refused from the cached value.
    status, _ = _status(handler.onHTTPRequest(None, _request("wrong"), {}))
    assert status == 401


def test_a_header_that_is_not_a_token_is_refused_not_crashed(td_home):
    """The comparison is `hmac.compare_digest`, which is fussier than `!=`.

    It raises TypeError on a non-ASCII str and on anything that is not a
    str or bytes, where `!=` just returned False. A caller controls that
    header, so a crash there is a 500 handed to anyone who sends `é` — and
    inside TouchDesigner an unhandled handler exception is worse than a
    refusal. Both shapes must come back as an ordinary 401.
    """
    _write_config(td_home, {"token": "s3cret-token"})

    for junk in ("s3cret-tokén", "—", 12345, None, b"s3cret-token", ["x"]):
        request = {"data": json.dumps({"method": "no-such-method"})}
        request["X-TD-Atlas-Token"] = junk
        status, body = _status(handler.onHTTPRequest(None, request, {}))
        assert status == 401, junk
        assert body["error"]["type"] == "Unauthorized"


def test_a_non_ascii_token_still_authenticates(td_home):
    """The encode is not a filter: a config token outside ASCII must work.

    Rejecting it would be a silent lockout — the bridge would start, announce
    itself, and refuse the token the host is reading from the same file.
    """
    _write_config(td_home, {"token": "паро́ль-мостá"})

    status, body = _status(handler.onHTTPRequest(None, _request("паро́ль-мостá"), {}))
    assert status == 404
    assert body["error"]["type"] == "UnknownMethod"


def test_an_unconfigured_handler_warns_on_the_first_request(td_home, capsys):
    """Even if onServerStart never ran, the open bridge is not silent."""
    status, _ = _status(handler.onHTTPRequest(None, _request(), {}))

    assert status == 404, "no token means no gate"
    assert "WARNING: no auth token" in capsys.readouterr().out


# -- the two install paths now write the same text --------------------------

def test_bootstrap_no_longer_substitutes_the_token():
    source = (
        Path(handler.__file__).parent / "bootstrap.py"
    ).read_text()
    assert "AUTH_TOKEN" not in source
    assert ".replace(" not in source
