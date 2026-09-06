"""The status panel's text, built without TouchDesigner.

The panel is a Text TOP inside the bridge COMP; everything about *what* it
says is a pure function of a stored state mapping, which is what these check.
Drawing it — reading the Table DAT, writing the parameter — needs a running
instance and is not covered here.
"""

from __future__ import annotations

import time

import pytest

from td_atlas.component import handler as h


def _values(text):
    """The panel's lines as a mapping from label to the value.

    A value too long for one line is broken across several with the label
    column left blank (see `handler._wrap_value`), so the continuations are
    joined back on here: what these tests are about is what the panel says,
    not which line it landed on.
    """
    lines = text.splitlines()
    out = {}
    label = None
    for line in lines[1:]:
        head = line[: h._PANEL_LABEL].strip()
        rest = line[h._PANEL_LABEL :]
        if head:
            label = head
            out[label] = rest
        elif label is not None:
            out[label] += " " + rest
    return out


def test_an_untouched_panel_says_so_rather_than_showing_zeroes():
    text = h.render_panel({"port": "9977"})
    # The number is not this test's business — that the line carries the
    # bridge's own protocol version is.
    assert text.splitlines()[0] == (
        "td-atlas    port 9977    protocol %d" % h.PROTOCOL_VERSION
    )
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


# -- the text fits the panel ------------------------------------------------
#
# Measured on the live 2025.32460 panel by rendering strings into it and
# reading the ink back with `numpyArray` (alpha > 0.05):
#
#   Courier New at fontsizex 15 advances 12.0 px per character, the same for
#   'i', '.', '0' and 'W'; lines advance 24 px; the first line's ink runs from
#   y = 11 to y = 22 below the top edge; a line of 62 characters stays on one
#   line and one of 63 wraps, which is (760 - 10) // 12.
#
# Those numbers are the constants in `handler.py`, and everything below is a
# consequence of them, so this file is where the panel's legibility is checked
# rather than looked at. The measurement itself needs TouchDesigner; the claim
# it supports does not.

def _lines(state):
    return h.render_panel(state).splitlines()


def _fits(state):
    lines = _lines(state)
    assert lines, "the panel is never empty"
    assert max(len(line) for line in lines) <= h.PANEL_COLUMNS, (
        "a line runs past the panel: %r" % max(lines, key=len)
    )
    assert len(lines) <= h.PANEL_ROWS, "%d lines in a %d-line panel" % (
        len(lines), h.PANEL_ROWS
    )
    return lines


def test_the_columns_and_rows_are_the_measured_geometry_and_nothing_else():
    assert h.PANEL_COLUMNS == (h.PANEL_WIDTH - h.PANEL_X) // h.PANEL_PX_PER_CHAR
    assert h.PANEL_ROWS == (
        (h.PANEL_HEIGHT - h.PANEL_INK_BOTTOM) // h.PANEL_PX_PER_LINE + 1
    )
    # The last line's ink has to land inside the panel, which is the whole
    # point of the row count.
    last_ink = h.PANEL_INK_TOP + h.PANEL_PX_PER_LINE * (h.PANEL_ROWS - 1)
    assert last_ink + (h.PANEL_INK_BOTTOM - h.PANEL_INK_TOP) <= h.PANEL_HEIGHT
    assert h.PANEL_INK_TOP + h.PANEL_PX_PER_LINE * h.PANEL_ROWS > h.PANEL_HEIGHT


def test_the_parameters_carry_the_font_and_the_size_the_geometry_was_measured_at():
    pars = dict(h.PANEL_PARS)
    assert pars["font"] == h.PANEL_FONT
    assert pars["fontsizex"] == str(h.PANEL_FONT_SIZE)
    assert pars["resolutionw"] == str(h.PANEL_WIDTH)
    assert pars["resolutionh"] == str(h.PANEL_HEIGHT)
    assert pars["positionx"] == str(h.PANEL_X)


def test_a_full_panel_with_a_long_health_verdict_stays_inside_its_borders():
    """The state that was overflowing the live panel, verdict and all."""
    lines = _fits({
        "port": "9977",
        "method": "op_create",
        "owner": "claude",
        "at": "1735732801.0",
        "batch": "4 ops applied",
        "health": "61/60 fps, 7/11 cooking - 1 error, 1 warning, 1 note",
        "healthAt": "1735732801.0",
        "text": "Vessel.network.json  1834 ops",
        "textAt": "1735732801.0",
    })
    # The verdict is on two lines rather than one wrapped by TouchDesigner.
    assert any(line.startswith("health") for line in lines)
    assert "note" in "\n".join(lines)


def test_a_long_operator_name_in_every_field_still_fits():
    long_name = "moviefilein_with_a_very_long_deliberate_name_1234567890"
    lines = _fits({
        "port": "9977",
        "method": "par_set",
        "owner": long_name,
        "at": "1735732801.0",
        "batch": "12 ops applied to /project1/%s" % long_name,
        "health": "58/60 fps, 41/220 cooking - 3 errors on /project1/%s"
                  % long_name,
        "healthAt": "1735732801.0",
        "text": "refused: %s.network.json is not ours" % long_name,
        "textAt": "1735732801.0",
    })
    assert len(lines) == h.PANEL_ROWS


def test_a_value_with_no_spaces_is_cut_up_rather_than_clipped_by_the_panel():
    path = "/project1/" + "a" * 200
    lines = _fits({"port": "9977", "text": path, "textAt": "0"})
    rejoined = "".join(line.strip() for line in lines[1:])
    assert path[:60] in rejoined


def test_a_continuation_sits_under_the_value_column_not_under_the_label():
    lines = h._wrap_value("health", "word " * 30)
    assert lines[0].startswith("health")
    for line in lines[1:]:
        assert line.startswith(" " * h._PANEL_LABEL)
        assert line[h._PANEL_LABEL] != " "


def test_more_text_than_the_panel_holds_says_it_was_cut(monkeypatch):
    lines = h._fit(["line %d" % i for i in range(h.PANEL_ROWS + 3)])
    assert len(lines) == h.PANEL_ROWS
    assert lines[-1].endswith("...")


def test_nothing_is_cut_while_it_still_fits():
    exact = ["line %d" % i for i in range(h.PANEL_ROWS)]
    assert h._fit(exact) == exact


def test_the_first_line_is_not_trusted_to_be_short_either():
    lines = _fits({"port": "9" * 300})


def test_an_empty_panel_fits_too():
    _fits({})
    _fits({"port": "9977"})


# -- the measurement the constants above stand on ---------------------------
#
# These need a running TouchDesigner and skip without one. They are what makes
# the pure tests above more than internally consistent: the geometry constants
# are a claim about a Text TOP, and this is where that claim is checked
# against one. They write on the live panel, which the bridge repaints at the
# end of the next request it serves.

import live_network as ln  # noqa: E402  (test helper, not a package import)


@pytest.mark.live
def test_the_advance_per_character_is_the_measured_one():
    client = ln.bridge_or_skip()
    ink = ln.panel_ink(client, "W" * 40)
    assert ink["font"] == h.PANEL_FONT
    assert ink["fontsize"] == float(h.PANEL_FONT_SIZE)
    span = ink["ink_right"] - h.PANEL_X + 1
    assert abs(span / 40 - h.PANEL_PX_PER_CHAR) < 0.5, span
    # Fixed pitch, which is the property the column count depends on: the
    # narrowest and the widest glyph must advance the same.
    narrow = ln.panel_ink(client, "i" * 40)["ink_right"]
    assert abs(narrow - ink["ink_right"]) <= h.PANEL_PX_PER_CHAR


@pytest.mark.live
def test_the_advance_per_line_is_the_measured_one():
    client = ln.bridge_or_skip()
    ink = ln.panel_ink(client, "\n".join("W" * 3 for _ in range(5)))
    starts = ink["line_starts"]
    assert len(starts) == 5
    steps = [b - a for a, b in zip(starts, starts[1:])]
    assert all(abs(step - h.PANEL_PX_PER_LINE) <= 1 for step in steps), steps
    assert abs(starts[0] - h.PANEL_INK_TOP) <= 1


@pytest.mark.live
def test_a_full_column_fits_and_one_more_character_does_not():
    """`PANEL_COLUMNS` is a wrap threshold, not an estimate."""
    client = ln.bridge_or_skip()
    fits = ln.panel_ink(client, "x " * (h.PANEL_COLUMNS // 2))
    assert len(fits["line_starts"]) == 1
    over = ln.panel_ink(client, "x " * (h.PANEL_COLUMNS // 2) + "xxx")
    assert len(over["line_starts"]) == 2


@pytest.mark.live
def test_the_worst_case_the_renderer_can_produce_lands_inside_the_panel():
    client = ln.bridge_or_skip()
    long_name = "moviefilein_with_a_very_long_deliberate_name_1234567890"
    text = h.render_panel({
        "port": "9977", "method": "par_set", "owner": long_name,
        "at": "1735732801.0",
        "batch": "12 ops applied to /project1/%s" % long_name,
        "health": "58/60 fps, 41/220 cooking - 3 errors on /project1/%s"
                  % long_name,
        "healthAt": "1735732801.0",
        "text": "refused: %s.network.json is not ours" % long_name,
        "textAt": "1735732801.0",
    })
    ink = ln.panel_ink(client, text)
    assert ink["width"] == h.PANEL_WIDTH and ink["height"] == h.PANEL_HEIGHT
    # Every line the renderer wrote has ink in the 24 px band the geometry
    # gives it. Not an exact top: where a line's ink starts depends on its
    # tallest glyph, and measured here that moves it by up to 2 px. And not a
    # band count either — a line whose glyphs leave one empty pixel row across
    # the whole width (a row of dots) reads as two bands.
    starts = ink["line_starts"]
    for index in range(len(text.splitlines())):
        top = h.PANEL_INK_TOP + h.PANEL_PX_PER_LINE * index
        assert any(
            top - 3 <= start < top + h.PANEL_PX_PER_LINE - 3 for start in starts
        ), (top, starts)
    # And nothing landed outside the panel, which is the whole claim.
    assert ink["ink_bottom"] < h.PANEL_HEIGHT
    assert ink["ink_right"] < h.PANEL_WIDTH
