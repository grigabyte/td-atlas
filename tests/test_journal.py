"""The call journal: what it writes, what it refuses to write, how it is read.

Every test here runs on the host with no TouchDesigner anywhere — the journal
is host-side by design (see `td_atlas/journal.py`), which is exactly what
makes "the token never lands in the log" a claim a test can settle.
"""

from __future__ import annotations

import json
import time

import pytest

from td_atlas import config as cfg
from td_atlas import journal
from td_atlas.bridge.client import BridgeClient, BridgeError, BridgeUnavailable
from td_atlas.component import handler
from td_atlas.mcp import hints

TOKEN = "s3cret-token-abcdefghijklmnop"


@pytest.fixture(autouse=True)
def _fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("TD_ATLAS_HOME", str(tmp_path))
    journal.forget_secrets()
    yield
    journal.forget_secrets()


def _write_config(token=TOKEN):
    cfg.ensure_home()
    cfg.save_config({"port": 9977, "token": token})


# -- what gets written ------------------------------------------------------

def test_a_successful_call_is_recorded_with_its_duration():
    journal.record("op_create", ok=True, seconds=0.0125,
                   params={"path": "/project1/blur1"}, port=9977)
    (call,) = journal.read()
    assert call.method == "op_create"
    assert call.ok
    assert call.ms == pytest.approx(12.5)
    assert call.path == "/project1/blur1"
    assert call.port == 9977


def test_a_refusal_keeps_its_whole_text():
    message = "no operator at path '/project1/nope' " + "x" * 500
    journal.record("op_info", ok=False, seconds=0.001,
                   error_type="LookupError", error_message=message)
    (call,) = journal.read()
    assert not call.ok
    assert call.error == "LookupError"
    assert call.message == message


def test_a_runaway_refusal_is_clipped_not_dropped():
    journal.record("exec", ok=False, seconds=0.0,
                   error_type="RuntimeError", error_message="y" * 50_000)
    (call,) = journal.read()
    assert len(call.message) == journal.MAX_ERROR_CHARS
    assert call.message.endswith("[clipped]")


def test_batch_records_its_size_and_not_its_steps():
    ops = [{"method": "op_create", "params": {"type": "noiseTOP"}} for _ in range(7)]
    journal.record("batch", ok=True, seconds=0.2, params={"ops": ops})
    (call,) = journal.read()
    assert call.steps == 7
    assert "noiseTOP" not in journal.journal_path().read_text()


def test_payloads_never_reach_the_file():
    """The BOUNDARY: a DAT's text and a whole network stay out of the log."""
    journal.record(
        "exec",
        ok=True,
        seconds=0.01,
        params={
            "code": "op('/project1/text1').par.text = 'the artist's whole shader'",
            "network": {"children": [{"name": "n%d" % i} for i in range(200)]},
            "path": "/project1/text1",
        },
    )
    raw = journal.journal_path().read_text()
    assert "shader" not in raw
    assert "children" not in raw
    assert "/project1/text1" in raw  # the path is kept on purpose
    assert len(raw) < 300


# -- the token --------------------------------------------------------------

def test_the_token_never_lands_in_the_journal_through_a_refusal():
    """The load-bearing one. A refusal that quotes the token is scrubbed.

    Deliberately not "make an ordinary failure and grep": the token does not
    travel in parameters today, so that test would pass with the scrub deleted
    and prove nothing. Here the token is *inside* the text being logged, so
    only the scrub can keep it out.
    """
    _write_config()
    journal.record(
        "exec",
        ok=False,
        seconds=0.0,
        error_type="RuntimeError",
        error_message="request failed with X-TD-Atlas-Token: %s" % TOKEN,
    )
    raw = journal.journal_path().read_text()
    assert TOKEN not in raw
    assert "<token redacted>" in raw
    assert cfg.load_config()["token"] == TOKEN  # the config itself is untouched


def test_the_token_is_scrubbed_out_of_every_field_not_just_the_message():
    _write_config()
    journal.record("claim_scope", ok=True, seconds=0.0,
                   params={"owner": TOKEN, "path": "/project1/" + TOKEN})
    raw = journal.journal_path().read_text()
    assert TOKEN not in raw


def test_the_client_scrubs_its_own_token_even_when_the_config_moved_on():
    """A client holding a token the config no longer names still scrubs it."""
    _write_config(token="a-completely-different-token-value")
    journal.record("ping", ok=False, seconds=0.0, error_type="RuntimeError",
                   error_message="bad token %s" % TOKEN, token=TOKEN)
    assert TOKEN not in journal.journal_path().read_text()


def test_a_short_secret_is_not_used_as_a_scrub_pattern():
    """An empty or one-character token must not blank the whole line."""
    _write_config(token="x")
    journal.record("ping", ok=True, seconds=0.0, params={"path": "/xxx"})
    (call,) = journal.read()
    assert call.path == "/xxx"


# -- growth -----------------------------------------------------------------

def test_the_journal_stops_growing_and_keeps_the_newest(monkeypatch):
    monkeypatch.setattr(journal, "MAX_BYTES", 4000)
    monkeypatch.setattr(journal, "TRIM_TO_BYTES", 3000)
    for i in range(400):
        journal.record("ping", ok=True, seconds=0.001, params={"path": "/p%04d" % i})
    size = journal.journal_path().stat().st_size
    assert size <= journal.MAX_BYTES
    calls = journal.read()
    assert calls, "trimming must not empty the file"
    assert calls[-1].path == "/p0399", "the newest call survives"
    assert calls[0].path != "/p0000", "the oldest was actually dropped"
    # And every surviving line is whole, not cut mid-record.
    for line in journal.journal_path().read_text().splitlines():
        json.loads(line)


def test_a_corrupt_line_does_not_hide_the_rest():
    journal.record("ping", ok=True, seconds=0.0)
    with journal.journal_path().open("a") as handle:
        handle.write('{"method": "half\n')
    journal.record("exec", ok=True, seconds=0.0)
    assert [c.method for c in journal.read()] == ["ping", "exec"]


def test_writing_never_raises_even_when_the_home_is_unusable(monkeypatch):
    monkeypatch.setattr(journal, "journal_path", lambda: (_ for _ in ()).throw(OSError))
    assert journal.record("ping", ok=True, seconds=0.0) is None


# -- reading and filtering --------------------------------------------------

def _three_calls():
    journal.record("ping", ok=True, seconds=0.001)
    journal.record("op_create", ok=False, seconds=0.05,
                   error_type="ScopeHeld", error_message="claimed by 'agent-b'")
    journal.record("exec", ok=True, seconds=0.5)


def test_failures_only_shows_the_refusals():
    _three_calls()
    failures = journal.read(failures_only=True)
    assert [c.method for c in failures] == ["op_create"]


def test_limit_keeps_the_most_recent():
    _three_calls()
    assert [c.method for c in journal.read(limit=2)] == ["op_create", "exec"]


def test_filtering_by_method():
    _three_calls()
    assert [c.method for c in journal.read(method="exec")] == ["exec"]


def test_reading_an_absent_journal_is_empty_not_an_error():
    assert journal.read() == []
    assert "No calls recorded yet" in journal.format_calls([])
    assert "No calls recorded yet" in journal.format_summary(journal.summarise([]))


# -- the summary ------------------------------------------------------------

def test_the_summary_counts_and_ranks_by_failures():
    for _ in range(400):
        journal.record("ping", ok=True, seconds=0.001)
    for i in range(3):
        journal.record("op_create", ok=False, seconds=0.01,
                       error_type="ScopeHeld", error_message="held %d" % i)
    journal.record("op_create", ok=True, seconds=0.01)
    journal.record("render", ok=True, seconds=2.0)

    summary = journal.summarise(journal.read())
    assert summary.total == 405
    assert summary.failures == 3
    # The method that refuses leads, even though ping outnumbers it 100 to 1.
    assert summary.by_method[0][0] == "op_create"
    assert summary.by_method[0][1:3] == (4, 3)
    assert summary.reasons == [("ScopeHeld", 3)]
    assert summary.slowest[0].method == "render"

    text = journal.format_summary(summary)
    assert "405 calls, 3 failed" in text
    assert "op_create" in text.split("where it fails")[1]


def test_the_summary_says_so_when_nothing_failed():
    journal.record("ping", ok=True, seconds=0.001)
    assert "no refusals recorded" in journal.format_summary(
        journal.summarise(journal.read())
    )


def test_the_rendered_list_shows_the_refusal_text_under_the_call():
    _three_calls()
    text = journal.format_calls(journal.read())
    assert "FAIL" in text
    assert "ScopeHeld: claimed by 'agent-b'" in text
    assert text.index("op_create") < text.index("exec"), "newest last"


# -- the client chokepoint --------------------------------------------------

class _Recorded(BridgeClient):
    """A client whose transport is replaced, so no TouchDesigner is needed."""

    outcome: object = None

    def _call(self, method, timeout=None, **params):
        time.sleep(0.002)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return {"ok": True}


def test_every_successful_bridge_call_lands_in_the_journal():
    client = _Recorded(port=9977, token=TOKEN)
    client.call("op_info", path="/project1/blur1")
    (call,) = journal.read()
    assert call.method == "op_info"
    assert call.ok
    assert call.path == "/project1/blur1"
    assert call.port == 9977
    assert call.ms >= 2.0, "the duration is measured, not invented"


def test_a_handler_refusal_lands_with_its_type_and_text():
    client = _Recorded(port=9977)
    client.outcome = BridgeError(
        {"type": "LookupError", "message": "no operator at path '/project1/nope'"},
        "op_info",
    )
    with pytest.raises(BridgeError):
        client.call("op_info", path="/project1/nope")
    (call,) = journal.read()
    assert call.error == "LookupError"
    assert "no operator" in call.message


def test_an_unreachable_bridge_lands_with_the_reason_hints_are_keyed_on():
    client = _Recorded(port=9977)
    client.outcome = BridgeUnavailable("nothing there", reason="bridge_timeout")
    with pytest.raises(BridgeUnavailable):
        client.call("ping")
    (call,) = journal.read()
    assert call.error == "BridgeUnavailable"
    assert call.reason == "bridge_timeout"


# -- the repair advice ------------------------------------------------------

def test_a_stored_failure_maps_to_the_same_hint_a_live_one_would():
    for reason in ("bridge_timeout", "bridge_unreachable", "bridge_protocol"):
        live = hints.classify(BridgeUnavailable("x", reason=reason))
        assert hints.from_record("BridgeUnavailable", reason) is live
    live = hints.classify(BridgeError({"type": "ScopeHeld", "message": ""}, "m"))
    assert hints.from_record("ScopeHeld", "") is live


def test_an_unmapped_stored_failure_gets_the_honest_gap():
    recovery = hints.from_record("SomethingNobodyMapped", "")
    assert "SomethingNobodyMapped" in recovery.cause + recovery.action


def test_every_reason_the_journal_can_store_has_a_hint():
    """The four `BridgeUnavailable.reason` values all resolve to real advice."""
    for reason in ("bridge_unreachable", "bridge_http", "bridge_timeout",
                   "bridge_protocol"):
        assert hints.from_record("BridgeUnavailable", reason).action


# -- the two reading surfaces, CLI and MCP ----------------------------------

def test_the_cli_shows_the_trail_the_failures_and_the_summary(capsys):
    from td_atlas import cli

    _three_calls()

    assert cli.main(["log"]) == 0
    assert "op_create" in capsys.readouterr().out

    assert cli.main(["log", "--failures"]) == 0
    out = capsys.readouterr().out
    assert "ping" not in out and "ScopeHeld" in out
    # The repair for the last refusal, from the shared hints table.
    assert "fix:" in out

    assert cli.main(["log", "--summary"]) == 0
    assert "3 calls, 1 failed" in capsys.readouterr().out

    assert cli.main(["log", "--method", "exec"]) == 0
    assert "op_create" not in capsys.readouterr().out


def test_the_cli_log_refuses_a_bridge_selector(capsys):
    """`log` reads a file and dials nothing, so --port would be a lie."""
    from td_atlas import cli

    assert cli.main(["--port", "9977", "log"]) == 2
    assert "does not talk to one" in capsys.readouterr().err


def test_the_mcp_tool_returns_the_same_text():
    from td_atlas.mcp import server

    _three_calls()
    assert "ScopeHeld" in server.td_log(failures=True)
    assert "3 calls, 1 failed" in server.td_log(summary=True)
    assert server.td_log.__td_atlas_guarded__ is True


# -- the panel counter (pure, no TouchDesigner) -----------------------------

def test_the_panel_counts_failures_for_this_session_only():
    state = {}
    assert handler._tally_failures(state, "ok", 100) == 0
    assert handler._tally_failures(state, "error", 100) == 1
    assert handler._tally_failures(state, "error", 100) == 2
    assert handler._tally_failures(state, "ok", 100) == 2
    # A table restored from a saved .toe carries another run's count; it is
    # reset rather than continued.
    assert handler._tally_failures(state, "error", 999) == 1


def test_a_nonsense_count_in_the_table_does_not_break_the_panel():
    state = {"fails": "not a number", "failsPid": "100"}
    assert handler._tally_failures(state, "error", 100) == 1


def test_the_failure_count_shows_on_the_panel_without_costing_a_row():
    state = {"port": "9977", "protocol": "4", "method": "op_create",
             "owner": "agent-a", "at": repr(time.time()), "outcome": "error",
             "batch": "3 steps", "health": "green", "text": "written",
             "fails": "12", "failsPid": "100"}
    text = handler.render_panel(state)
    lines = text.split("\n")
    assert "12 failed" in lines[0]
    assert len(lines) <= handler.PANEL_ROWS
    assert max(len(line) for line in lines) <= handler.PANEL_COLUMNS


class _FakeTable:
    """Enough Table DAT for `_note_status` to do its read-merge-write.

    The real one needs TouchDesigner. What is worth checking here is not the
    DAT but the wiring: that `_note_request` actually reaches the counter, and
    that it still does it inside the one read and one write the panel already
    paid for.
    """

    def __init__(self):
        self.rows_ = [list(handler._STATUS_HEADER)]
        self.reads = 0
        self.writes = 0

    def rows(self):
        self.reads += 1
        return [list(row) for row in self.rows_]

    def clear(self):
        self.writes += 1
        self.rows_ = []

    def appendRow(self, row):  # noqa: N802 — TouchDesigner's spelling
        self.rows_.append(list(row))

    def state(self):
        return handler._parse_status_rows(self.rows_)


class _FakeDat:
    class par:
        class port:
            @staticmethod
            def eval():
                return 9977


def test_noting_a_request_carries_the_failure_count_into_the_table(monkeypatch):
    table = _FakeTable()
    monkeypatch.setattr(handler, "_status_table", lambda create=False: table)
    monkeypatch.setattr(handler, "_paint_panel", lambda state: None)

    handler._note_request(_FakeDat, "op_create", {"owner": "agent-a"}, "error")
    assert table.state()["fails"] == "1"
    handler._note_request(_FakeDat, "op_create", {"owner": "agent-a"}, "error")
    assert table.state()["fails"] == "2"
    handler._note_request(_FakeDat, "ping", {}, "ok")
    assert table.state()["fails"] == "2"
    assert table.state()["method"] == "ping"
    # One read and one write per request — the counter added neither.
    assert table.reads == 3 and table.writes == 3


def test_a_clean_session_shows_no_counter():
    text = handler.render_panel({"port": "9977", "protocol": "4", "fails": "0"})
    assert "failed" not in text.split("\n")[0]
