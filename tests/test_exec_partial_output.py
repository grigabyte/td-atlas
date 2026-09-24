"""A script that raises still hands back what it printed before it raised.

Measured in live work (agent report 1, point 2): four `print` calls of a
measurement ran, the fifth line named a parameter that does not exist, and the
reply was the exception alone. The output existed inside TouchDesigner and was
thrown away, so the whole script had to be run again without its last line —
about five times in one session.

The reply stays a failure (`ok: false`, the exception's own type), because the
call journal and the panel count failures by that flag; what changes is that
the error carries the stdout, stderr and `result` the script had produced.
None of this needs TouchDesigner: `exec` of plain Python runs anywhere.
"""

from __future__ import annotations

import argparse
import json

import pytest

from td_atlas import cli
from td_atlas.bridge.client import BridgeError
from td_atlas.component import handler
from td_atlas.mcp import server

SCRIPT = (
    "print('antialias = aa4')\n"
    "print('cam winx/winy/winsize:', 0.0, 0.0, 1.0)\n"
    "result = {'measured': 2}\n"
    "raise AttributeError(\"'td.ParCollection' object has no attribute 'aa'\")\n"
)


@pytest.fixture
def open_bridge(monkeypatch):
    """The Web Server DAT's view of the handler, with its disk writes off."""
    monkeypatch.setattr(handler, "AUTH_TOKEN", "")
    monkeypatch.setattr(handler, "_TOKEN_LOADED", True)
    monkeypatch.setattr(handler, "_refresh_instance", lambda dat: None)
    monkeypatch.setattr(handler, "_note_request", lambda *args: None)


def _call(method, **params):
    request = {"data": json.dumps({"method": method, "params": params})}
    response = handler.onHTTPRequest(None, request, {})
    return response["statusCode"], json.loads(response["data"])


def test_the_error_reply_carries_what_the_script_printed(open_bridge):
    status, body = _call("exec", code=SCRIPT)

    assert status == 500
    assert body["ok"] is False
    error = body["error"]
    assert error["type"] == "AttributeError"
    assert "no attribute 'aa'" in error["message"]
    assert error["stdout"] == (
        "antialias = aa4\ncam winx/winy/winsize: 0.0 0.0 1.0\n"
    )
    assert error["result"] == {"measured": 2}


def test_an_expression_that_raises_keeps_its_failure_shape(open_bridge):
    """The eval path fails the same way, with nothing printed to carry."""
    status, body = _call("exec", code="1/0")

    assert status == 500
    assert body["error"]["type"] == "ZeroDivisionError"
    assert body["error"].get("stdout", "") == ""


def test_a_script_that_succeeds_is_unchanged(open_bridge):
    status, body = _call("exec", code="print('hi')\nresult = 3")

    assert status == 200
    assert body["result"] == {"stdout": "hi\n", "stderr": "", "result": 3}


# -- the host ----------------------------------------------------------------

def _raised():
    return BridgeError(
        {
            "type": "tdAttributeError",
            "message": "'td.ParCollection' object has no attribute 'aa'",
            "traceback": "Traceback (most recent call last):\n  ...\n",
            "stdout": "antialias = aa4\ncam winx/winy/winsize: 0.0 0.0 1.0\n",
            "result": {"measured": 2},
        },
        "exec",
    )


class RaisingClient:
    ambiguity_warning = None
    version_warning = None

    def exec(self, code, timeout=None):
        raise _raised()


def test_the_client_keeps_the_partial_output():
    exc = _raised()
    assert exc.stdout.startswith("antialias = aa4")
    assert exc.result == {"measured": 2}


def test_td_exec_shows_the_output_before_the_error(monkeypatch):
    monkeypatch.setattr(server, "bridge", lambda: RaisingClient())

    text = server.td_exec("...")

    assert "stdout (before the error):\n  antialias = aa4\n" in text
    assert "  cam winx/winy/winsize: 0.0 0.0 1.0" in text
    # The output reads first and the exception after it, as in a terminal.
    assert text.index("antialias") < text.index("tdAttributeError:")
    assert '"measured": 2' in text
    # The recovery hint the failure always carried is still there.
    assert "fix:" in text


def test_the_cli_prints_the_output_before_the_error(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_client", lambda args: RaisingClient())

    code = cli.cmd_exec(argparse.Namespace(code="..."))

    captured = capsys.readouterr()
    assert code == 1
    assert "antialias = aa4" in captured.out
    assert "no attribute 'aa'" in captured.err
