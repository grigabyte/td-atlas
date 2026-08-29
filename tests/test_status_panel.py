"""The status panel's text, built without TouchDesigner.

The panel is a Text TOP inside the bridge COMP; everything about *what* it
says is a pure function of a stored state mapping, which is what these check.
Drawing it — reading the Table DAT, writing the parameter — needs a running
instance and is not covered here.
"""

from __future__ import annotations

import time

from td_atlas.component import handler as h


def _values(text):
    """The panel's lines as a mapping from label to the rest of the line."""
    lines = text.splitlines()
    out = {}
    for line in lines[1:]:
        label = line[: h._PANEL_LABEL].strip()
        out[label] = line[h._PANEL_LABEL :]
    return out


def test_an_untouched_panel_says_so_rather_than_showing_zeroes():
    text = h.render_panel({"port": "9977"})
    assert text.splitlines()[0] == "td-atlas    port 9977    protocol 3"
    values = _values(text)
    assert values["last call"] == "nothing yet"
    assert values["batch"] == "none yet"
    assert values["health"] == "not checked yet"


def test_the_port_is_the_stored_one_and_an_unknown_one_is_not_invented():
    assert "port 9312" in h.render_panel({"port": "9312"})
    assert "port ?" in h.render_panel({})


def test_the_protocol_falls_back_to_this_module_s_own_version():
    assert "protocol %d" % h.PROTOCOL_VERSION in h.render_panel({})


def test_a_call_shows_the_method_the_caller_and_the_wall_clock_time():
    when = 1735732801.0
    line = _values(h.render_panel({"method": "op_create", "owner": "claude",
                                   "at": repr(when)}))["last call"]
    assert line.startswith("op_create  by claude  at ")
    assert line.endswith(time.strftime("%H:%M:%S", time.localtime(when)))


def test_a_caller_that_named_nobody_is_shown_as_unattributed():
    line = _values(h.render_panel({"method": "exec", "at": "1735732801.0"}))
    assert "unattributed" in line["last call"]


def test_a_failed_call_is_marked_so_and_a_successful_one_is_not():
    failed = h.render_panel({"method": "op_create", "at": "1735732801.0",
                             "outcome": "error"})
    fine = h.render_panel({"method": "op_create", "at": "1735732801.0",
                           "outcome": "ok"})
    assert "FAILED" in _values(failed)["last call"]
    assert "FAILED" not in _values(fine)["last call"]


def test_the_batch_size_is_shown_verbatim():
    assert _values(h.render_panel({"batch": "12 ops applied"}))["batch"] == (
        "12 ops applied"
    )


def test_the_health_verdict_carries_the_time_it_was_reached():
    when = 1735732801.0
    line = _values(h.render_panel({"health": "60/60 fps, 5/6 cooking - "
                                             "nothing wrong found",
                                   "healthAt": repr(when)}))["health"]
    assert line.startswith("60/60 fps, 5/6 cooking - nothing wrong found  at ")
    assert line.endswith(time.strftime("%H:%M:%S", time.localtime(when)))


def test_a_stamp_that_is_not_a_time_leaves_the_clock_off_rather_than_lying():
    assert h._clock("") == ""
    assert h._clock("later") == ""
    assert h._clock("0") == ""
    assert h._clock(None) == ""


def test_the_labels_line_the_values_up_under_each_other():
    text = h.render_panel({"method": "ping", "at": "1735732801.0",
                           "batch": "3 ops applied", "health": "fine"})
    for line in text.splitlines()[1:]:
        assert line[h._PANEL_LABEL - 1] == " ", line
        assert line[h._PANEL_LABEL] != " ", line


def test_the_state_survives_a_trip_through_the_table_s_rows():
    state = {"port": "9977", "method": "op_create", "owner": "claude",
             "at": "1735732801.0", "batch": "2 ops applied", "health": "fine"}
    rows = h._format_status_rows(state)
    assert rows[0] == list(h._STATUS_HEADER)
    assert h._parse_status_rows(rows) == state


def test_the_header_row_is_not_read_back_as_a_value():
    assert h._parse_status_rows([list(h._STATUS_HEADER)]) == {}


def test_a_short_or_junk_row_is_skipped_rather_than_raising():
    assert h._parse_status_rows([["port"], [], ["port", "9977"]]) == {
        "port": "9977"
    }


def test_separators_inside_a_value_cannot_split_it_into_extra_cells():
    rows = h._format_status_rows({"health": "two\tcells\nand a row"})
    assert rows[1] == ["health", "two cells and a row"]
    assert h._parse_status_rows(rows) == {"health": "two cells and a row"}


def test_off_the_host_there_is_no_table_and_nothing_raises():
    """`me` does not exist here, so the panel is simply absent."""
    assert h._status_table() is None
    assert h._read_status() == {}
    assert h._note_status(port=1) is None


def test_status_note_refuses_a_verdict_that_is_not_a_line_of_text():
    for bad in ({}, {"health": ""}, {"health": "   "}, {"health": 7}):
        try:
            h.m_status_note(bad)
        except ValueError as exc:
            assert "status_note needs 'health'" in str(exc)
        else:
            raise AssertionError("accepted %r" % (bad,))
