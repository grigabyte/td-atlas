"""The call journal: what it writes, what it refuses to write, how it is read.

Every test here runs on the host with no TouchDesigner anywhere — the journal
is host-side by design (see `td_atlas/journal.py`), which is exactly what
makes "the token never lands in the log" a claim a test can settle.
"""

from __future__ import annotations

import json
import os
import time

import pytest

from td_atlas import config as cfg
from td_atlas import journal
from td_atlas.bridge.client import BridgeClient, BridgeError, BridgeUnavailable
from td_atlas.component import handler
from td_atlas.mcp import hints
from windows_gaps import posix_access_refusal_only

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


# -- what a change leaves behind ---------------------------------------------
#
# Until 2026-09-24 the policy was the opposite: names, never payloads. Six
# thousand journal lines of an agent's session held the method, the outcome and
# the time, and not one parameter value, so "put it back the way it was
# yesterday" was answered by matching stills from a rendered video for forty
# minutes (ПРОЧТИ-БОЛИ-АГЕНТА-2, point 1). The owner reversed it: a call that
# changes the project records what it changed. A call that only reads still
# records its path and nothing else.

class _Answering(BridgeClient):
    """A client whose transport answers with a chosen result."""

    answer: object = None

    def _call(self, method, timeout=None, **params):
        return self.answer


def test_par_set_records_the_values_and_what_they_read_back_as():
    client = _Answering(port=9977)
    client.answer = {"path": "/project1/geo1",
                     "applied": {"tx": 0.5, "ty": 12.25}}
    client.call("par_set", path="/project1/geo1",
                pars={"tx": 0.5, "ty": {"expr": "absTime.seconds"}}, owner="")
    (call,) = journal.read()
    assert call.path == "/project1/geo1"
    assert call.change["pars"] == {"tx": 0.5, "ty": {"expr": "absTime.seconds"}}
    assert call.change["applied"]["ty"] == 12.25
    text = journal.format_calls([call])
    assert "tx = 0.5" in text
    assert "ty = expr absTime.seconds" in text and "12.25" in text


def test_par_set_does_not_invent_an_old_value_the_bridge_never_sent():
    client = _Answering(port=9977)
    client.answer = {"path": "/project1/geo1", "applied": {"tx": 0.5}}
    client.call("par_set", path="/project1/geo1", pars={"tx": 0.5})
    (call,) = journal.read()
    assert "before" not in call.change
    assert "was" not in journal.format_calls([call])


def test_flags_set_records_the_old_values_the_bridge_already_returns():
    client = _Answering(port=9977)
    client.answer = {"path": "/project1/look", "type": "renderTOP",
                     "before": {"viewer": True}, "applied": {"viewer": False}}
    client.call("flags_set", path="/project1/look", flags={"viewer": False})
    (call,) = journal.read()
    assert call.change["flags"] == {"viewer": False}
    assert call.change["before"] == {"viewer": True}
    assert "viewer = False (was True)" in journal.format_calls([call])


def test_exec_records_its_code_clipped_and_saying_so():
    code = "op('/project1/noise1').par.amp = 3\n" + "# padding\n" * 2000
    journal.record("exec", ok=True, seconds=0.01, params={"code": code})
    (call,) = journal.read()
    stored = call.change["code"]
    assert stored.startswith("op('/project1/noise1').par.amp = 3")
    assert len(stored) == journal.MAX_CODE_CHARS
    assert stored.endswith("[clipped]")
    assert call.change["code_chars"] == len(code)


def test_a_short_exec_is_kept_whole():
    code = "op('/project1/noise1').par.amp = 3"
    journal.record("exec", ok=True, seconds=0.01, params={"code": code})
    (call,) = journal.read()
    assert call.change == {"code": code}
    assert code in journal.format_calls([call])


def test_named_secrets_in_recorded_code_are_hidden():
    """Code an agent ran can carry someone else's key; the journal keeps code."""
    code = (
        'api_key = "live-3f9a7c2e1b"\n'
        "client.login(user='me', password='hunter2')\n"
        "headers = {'X-Auth-Token': 'abc123def456'}\n"
        'SECRET_KEY: "s3cr3t-value"\n'
    )
    journal.record("exec", ok=True, seconds=0.0, params={"code": code})
    raw = journal.journal_path().read_text()
    for value in ("live-3f9a7c2e1b", "hunter2", "abc123def456", "s3cr3t-value"):
        assert value not in raw
    (call,) = journal.read()
    stored = call.change["code"]
    assert 'api_key = "[redacted]"' in stored
    assert "password='[redacted]'" in stored
    assert "'X-Auth-Token': '[redacted]'" in stored
    assert "client.login(user='me', " in stored


def test_keys_that_announce_themselves_are_hidden_wherever_they_stand():
    keys = ["sk-" + "a1B2c3D4e5F6g7H8i9J0", "ghp_" + "A" * 36,
            "xoxb-" + "123456789012-abcdef", "AKIA" + "ABCDEFGHIJKLMNOP"]
    code = "\n".join("connect(%r)" % key for key in keys)
    journal.record("exec", ok=True, seconds=0.0, params={"code": code})
    raw = journal.journal_path().read_text()
    for key in keys:
        assert key not in raw
    assert raw.count("[redacted]") == len(keys)


def test_a_refusal_that_quotes_the_line_hides_the_secret_in_it_too():
    """A SyntaxError from `exec` quotes the source line it stopped on."""
    journal.record("exec", ok=False, seconds=0.0, params={"code": "x"},
                   error_type="SyntaxError",
                   error_message="invalid syntax: password = 'hunter2' +")
    raw = journal.journal_path().read_text()
    assert "hunter2" not in raw
    assert "password = '[redacted]'" in raw


def test_a_parameter_named_as_a_secret_is_written_hidden():
    journal.record("par_set", ok=True, seconds=0.0,
                   params={"path": "/project1/web1",
                           "pars": {"password": "hunter2", "url": "https://x"}})
    (call,) = journal.read()
    assert call.change["pars"] == {"password": "[redacted]", "url": "https://x"}


def test_ordinary_code_that_mentions_tokens_is_kept_as_written():
    """Names without a string value, comparisons and look-alikes stay intact."""
    code = (
        "token_count = 3\n"
        "token = get_token()\n"
        'if token == "abc":\n'
        "    author = 'Bob'\n"
        "password_hash = 'x1'\n"
        "tokens = split(text)\n"
    )
    journal.record("exec", ok=True, seconds=0.0, params={"code": code})
    (call,) = journal.read()
    assert call.change["code"] == code


def test_the_token_is_scrubbed_out_of_recorded_code():
    """The bridge's own token, even where no name says it is one.

    Under a name like `X-TD-Atlas-Token` the secret patterns hide it first
    (the case above); passed bare, only `_scrub` knows it.
    """
    _write_config()
    journal.record("exec", ok=True, seconds=0.0,
                   params={"code": "send(%r)" % TOKEN})
    raw = journal.journal_path().read_text()
    assert TOKEN not in raw
    assert "<token redacted>" in raw


def test_the_token_under_a_header_name_is_hidden_either_way():
    _write_config()
    journal.record("exec", ok=True, seconds=0.0,
                   params={"code": "headers = {'X-TD-Atlas-Token': '%s'}" % TOKEN})
    assert TOKEN not in journal.journal_path().read_text()


def test_batch_records_every_step_and_what_each_changed():
    client = _Answering(port=9977)
    client.answer = {"applied": 3, "results": [
        {"path": "/project1/noise1", "type": "noiseTOP"},
        {"path": "/project1/noise1", "applied": {"amp": 3.0}},
        {"path": "/project1/look", "before": {"bypass": False},
         "applied": {"bypass": True}},
    ]}
    client.batch([
        {"method": "op_create", "params": {"parent": "/project1",
                                           "type": "noiseTOP", "name": "noise1"}},
        {"method": "par_set", "params": {"path": "/project1/noise1",
                                         "pars": {"amp": 3.0}}},
        {"method": "flags_set", "params": {"path": "/project1/look",
                                           "flags": {"bypass": True}}},
    ], undo_name="demo")
    (call,) = journal.read()
    assert call.steps == 3
    steps = call.change["steps"]
    assert [s["method"] for s in steps] == ["op_create", "par_set", "flags_set"]
    assert steps[0]["type"] == "noiseTOP"
    assert steps[0]["created"] == "/project1/noise1"
    assert steps[1]["pars"] == {"amp": 3.0}
    assert steps[2]["before"] == {"bypass": False}
    text = journal.format_calls([call])
    assert "amp = 3.0" in text and "bypass = True (was False)" in text


def test_a_failed_change_still_records_what_was_attempted():
    journal.record("par_set", ok=False, seconds=0.0,
                   params={"path": "/project1/n", "pars": {"nosuchpar": 1}},
                   error_type="AttributeError", error_message="has no parameter")
    (call,) = journal.read()
    assert call.change["pars"] == {"nosuchpar": 1}


def test_a_timeline_job_records_the_walk_it_was_sent():
    """`timeline_run` pauses the timeline, crops Render TOPs and writes files.

    It is a change to the project for as long as it runs, and the files stay,
    so its line keeps what it was asked to do: which TOP, which frames, which
    of them saved and where, how many tiles over which Render TOPs.
    """
    client = _Answering(port=9977)
    client.answer = {"job": "j1", "state": "running"}
    client.call("timeline_run", path="/project1/out1", start=1, end=994,
                output="/renders/f{frame:04d}_{tile}.png", tiles=2,
                from_start=True, settle=2, save=[[92, 217], [459, 541]],
                render=["/project1/render1"])
    (call,) = journal.read()
    assert call.path == "/project1/out1"
    assert call.change == {
        "start": 1, "end": 994, "output": "/renders/f{frame:04d}_{tile}.png",
        "tiles": 2, "from_start": True, "settle": 2,
        "save": [[92, 217], [459, 541]], "render": ["/project1/render1"],
    }
    text = journal.format_calls([call])
    assert "output: " in text and "/renders/f{frame:04d}_{tile}.png" in text
    assert "render: " in text and "/project1/render1" in text


def test_cancelling_a_timeline_job_records_which_job():
    """A cancel puts the crop and the play mode back: a change of its own."""
    client = _Answering(port=9977)
    client.answer = {"job": "j1", "state": "cancelled"}
    client.call("timeline_cancel", job="j1")
    (call,) = journal.read()
    assert call.change == {"job": "j1"}


def test_a_profile_records_the_walk_it_was_sent():
    """`timeline_profile` pauses the timeline and moves its frame: a change."""
    client = _Answering(port=9977)
    client.answer = {"job": "t4", "state": "running", "mode": "profile"}
    client.call("timeline_profile", path="/project1/cement", start=3000,
                end=3009, limit=50, settle=2)
    (call,) = journal.read()
    assert call.path == "/project1/cement"
    assert call.change == {"start": 3000, "end": 3009, "limit": 50, "settle": 2}


def test_a_call_that_only_reads_records_its_path_and_nothing_else():
    """What stays out: a whole network, a render's bytes, a read's arguments."""
    journal.record(
        "network",
        ok=True,
        seconds=0.01,
        params={
            "network": {"children": [{"name": "n%d" % i} for i in range(200)]},
            "path": "/project1/text1",
            "depth": 3,
        },
    )
    raw = journal.journal_path().read_text()
    assert "children" not in raw
    assert "/project1/text1" in raw  # the path is kept on purpose
    assert len(raw) < 300
    (call,) = journal.read()
    assert call.change is None


def test_one_line_stays_bounded_however_large_the_change():
    ops = [{"method": "op_create",
            "params": {"parent": "/project1", "type": "textDAT",
                       "text": "x" * 20_000}} for _ in range(500)]
    journal.record("batch", ok=True, seconds=0.2, params={"ops": ops})
    raw = journal.journal_path().read_text()
    assert len(raw.encode("utf-8")) <= journal.MAX_LINE_BYTES
    (call,) = journal.read()
    assert call.steps == 500
    assert call.change, "something of the change is kept, not all dropped"


def test_the_journal_holds_a_day_of_changes_not_an_hour():
    """The cap is sized for the question it has to answer: yesterday.

    An exec a line at the code bound is the worst case; the cap must hold a
    working session of those, not a few hours of pings.
    """
    assert journal.MAX_BYTES // journal.MAX_CODE_CHARS >= 3000


def test_both_reading_surfaces_show_the_change(capsys):
    from td_atlas import cli
    from td_atlas.mcp import server

    journal.record("par_set", ok=True, seconds=0.01,
                   params={"path": "/project1/geo1", "pars": {"rx": 45}})
    assert cli.main(["log", "--method", "par_set"]) == 0
    assert "rx = 45" in capsys.readouterr().out
    assert "rx = 45" in server.td_log(method="par_set")


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
    for reason in ("bridge_timeout", "bridge_asleep", "bridge_unreachable",
                   "bridge_protocol"):
        live = hints.classify(BridgeUnavailable("x", reason=reason))
        assert hints.from_record("BridgeUnavailable", reason) is live
    live = hints.classify(BridgeError({"type": "ScopeHeld", "message": ""}, "m"))
    assert hints.from_record("ScopeHeld", "") is live


def test_an_unmapped_stored_failure_gets_the_honest_gap():
    recovery = hints.from_record("SomethingNobodyMapped", "")
    assert "SomethingNobodyMapped" in recovery.cause + recovery.action


def test_every_reason_the_journal_can_store_has_a_hint():
    """The five `BridgeUnavailable.reason` values all resolve to real advice."""
    for reason in ("bridge_unreachable", "bridge_http", "bridge_timeout",
                   "bridge_asleep", "bridge_protocol"):
        assert reason in hints.HINTS
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


# -- when the journal itself is broken --------------------------------------
#
# The audit's finding, in one sentence: `record` swallowed every write error,
# no caller checked its return value, and `td_log` then printed "No calls
# recorded yet" — the same words it prints when the session really did nothing.
# The reader was told the reassuring half of an ambiguity.

@pytest.fixture(autouse=True)
def _forget_write_failure():
    journal._write_failure = ""
    journal._read_failure = ""
    yield
    journal._write_failure = ""
    journal._read_failure = ""


def test_a_failed_write_is_named_in_the_report_not_swallowed(monkeypatch):
    def explode(_line):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(journal, "_append", explode)
    assert journal.record("op_create", ok=True, seconds=0.01) is None

    text = journal.format_calls(journal.read())
    assert "not being written" in text
    assert "No space left on device" in text


def test_the_summary_says_it_too(monkeypatch):
    monkeypatch.setattr(journal, "_append", lambda _l: (_ for _ in ()).throw(
        PermissionError("read-only file system")
    ))
    journal.record("op_create", ok=True, seconds=0.01)
    text = journal.format_summary(journal.summarise(journal.read()))
    assert "not being written" in text and "read-only" in text


def test_a_write_that_works_again_clears_the_complaint(monkeypatch):
    monkeypatch.setattr(journal, "_append", lambda _l: (_ for _ in ()).throw(
        OSError("disk went away")
    ))
    journal.record("op_create", ok=True, seconds=0.01)
    assert journal.not_being_kept()

    monkeypatch.undo()
    journal.record("op_create", ok=True, seconds=0.01)
    assert journal.not_being_kept() == ""


def test_a_journal_that_cannot_be_read_is_not_an_empty_one(monkeypatch):
    journal.record("op_create", ok=True, seconds=0.01)

    real = journal.Path.read_text

    def refuse(self, *args, **kwargs):
        if self.name == "calls.jsonl":
            raise PermissionError("Operation not permitted")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(journal.Path, "read_text", refuse)
    assert journal.read() == []
    assert "cannot be read" in journal.format_calls(journal.read())


def test_an_empty_journal_that_is_simply_empty_says_nothing_extra():
    assert journal.not_being_kept() == ""
    assert "not being written" not in journal.format_calls(journal.read())


# -- the token cache ---------------------------------------------------------

def test_a_rotated_token_is_scrubbed_not_the_old_one():
    """`td-atlas install` rewrites config.json in place. The cache was keyed on
    the path alone, so a long-lived MCP server went on removing the *previous*
    token and wrote the live one into the log in clear."""
    _write_config("old-token-aaaaaaaaaaaaaaaa")
    journal.record("op_create", ok=True, seconds=0.01,
                   params={"owner": "old-token-aaaaaaaaaaaaaaaa"})

    new = "new-token-bbbbbbbbbbbbbbbb"
    _write_config(new)
    # A rotation inside one timestamp tick is the residual gap the cache
    # cannot close, so the test moves the mtime the way a real second would.
    stamp = cfg.config_path().stat().st_mtime + 2
    os.utime(cfg.config_path(), (stamp, stamp))

    journal.record("op_create", ok=True, seconds=0.01, params={"owner": new})

    written = journal.journal_path().read_text()
    assert new not in written
    assert written.count("<token redacted>") == 2


def test_a_home_that_does_not_exist_yet_is_not_a_complaint(tmp_path, monkeypatch):
    """`~/.td-atlas` is created by the first install, build or bridge call. Until
    then it is absent, and `os.access` says False for an absent directory —
    which would have made a fresh checkout's first `td-atlas log` announce that
    the journal is broken."""
    monkeypatch.setenv("TD_ATLAS_HOME", str(tmp_path / "never-made"))
    assert journal.not_being_kept() == ""
    assert "not being written" not in journal.format_calls(journal.read())


@posix_access_refusal_only
def test_a_home_that_exists_and_refuses_writes_is(tmp_path, monkeypatch):
    home = tmp_path / "read-only"
    home.mkdir()
    home.chmod(0o500)
    monkeypatch.setenv("TD_ATLAS_HOME", str(home))
    try:
        assert "not being written" in journal.not_being_kept()
    finally:
        home.chmod(0o700)
