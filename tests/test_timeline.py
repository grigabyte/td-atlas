"""`td_status` says which frame the *timeline* is on and whether it is playing.

Until 2026-09-24 the status line printed `frame 1284807`, which was
`absTime.frame` — the application's clock — while the timeline stood at 851
and was playing. An agent took "TD is paused" on trust, and a live CHOP
between capture steps cooked frames it did not expect (ПРОЧТИ-БОЛИ-АГЕНТА-2,
point 2). A forgotten `rangeEnd` stopped a recording at frame 600 in silence
(ПРОЧТИ-БОЛИ-АГЕНТА, point 5). Both are answered by one line, and both halves
of it are tested here without TouchDesigner: the bridge's `ping` reply, faked
globals and all, and the host's rendering of it.
"""

from __future__ import annotations

import pytest

from td_atlas.component import handler


class _Time:
    frame = 851.0
    play = True
    start = 1.0
    end = 994.0
    rangeStart = 1.0
    rangeEnd = 994.0
    rate = 60.0


class _Root:
    def __init__(self, time):
        self.time = time


@pytest.fixture
def td_globals(monkeypatch):
    """The injected names `m_ping` reads, faked with plain objects."""
    monkeypatch.setattr(handler, "app", type("A", (), {
        "build": "2025.32460", "version": "099", "product": "TouchDesigner",
    })(), raising=False)
    monkeypatch.setattr(handler, "project", type("P", (), {
        "name": "demo2.toe", "folder": "/tmp/demo",
    })(), raising=False)
    # The bridge's own DAT may sit under a Time COMP of its own, which is why
    # the reply must come from the root's time and not from `me.time`.
    bridge_time = type("BT", (), {"rate": 30.0, "frame": 5.0, "play": False})()
    monkeypatch.setattr(handler, "me", type("Me", (), {"time": bridge_time})(),
                        raising=False)
    monkeypatch.setattr(handler, "absTime", type("T", (), {"frame": 1284807})(),
                        raising=False)
    monkeypatch.setattr(handler, "op", lambda path: _Root(_Time()),
                        raising=False)


def test_ping_reports_the_timeline_of_the_root_not_the_app_clock(td_globals):
    reply = handler.m_ping({})
    line = reply["timeline"]
    assert line["frame"] == 851
    assert line["play"] is True
    assert (line["start"], line["end"]) == (1, 994)
    assert (line["rangeStart"], line["rangeEnd"]) == (1, 994)
    assert line["rate"] == 60
    assert reply["absFrame"] == 1284807


def test_ping_survives_a_timeline_field_that_will_not_read(td_globals, monkeypatch):
    """A broken read must not take `ping` down: it is the protocol check."""

    class Half(_Time):
        @property
        def rangeEnd(self):
            raise RuntimeError("no such member")

    monkeypatch.setattr(handler, "op", lambda path: _Root(Half()), raising=False)
    reply = handler.m_ping({})
    assert reply["protocol"] == handler.PROTOCOL_VERSION
    assert "rangeEnd" not in reply["timeline"]
    assert reply["timeline"]["frame"] == 851


def _describe(info):
    # Imported here so that, before the module existed, each test failed on
    # its own line rather than the whole file failing to collect.
    from td_atlas.bridge import timeline_status

    return timeline_status.describe(info)


def _info(**overrides):
    line = {"frame": 851, "play": True, "start": 1, "end": 994,
            "rangeStart": 1, "rangeEnd": 994, "rate": 60}
    line.update(overrides)
    return {"timeline": line, "absFrame": 1284807, "frame": 1284807}


def test_the_line_names_the_timeline_frame_and_the_app_clock_apart():
    assert _describe(_info()) == (
        "timeline: frame 851 (playing) | start 1 end 994 | range 1–994 | "
        "rate 60 | abs 1284807"
    )


def test_a_paused_timeline_says_so():
    assert "frame 851 (paused)" in _describe(_info(play=False))


def test_a_forgotten_range_end_is_flagged():
    text = _describe(_info(end=8731, rangeEnd=600))
    assert "range 1–600 (!)" in text
    assert "600" in text.split("(!)", 1)[1], "the reason names where it stops"


def test_a_range_start_after_start_is_flagged_too():
    assert "(!)" in _describe(_info(rangeStart=100))


def test_an_older_bridge_that_sends_no_timeline_is_named_not_guessed():
    text = _describe({"frame": 1284807})
    assert text.startswith("timeline: not reported")
    assert "851" not in text


def test_a_current_bridge_that_cannot_read_the_time_is_not_told_to_reload(
    td_globals, monkeypatch
):
    def broken(path):
        raise RuntimeError("no root")

    monkeypatch.setattr(handler, "op", broken, raising=False)
    reply = handler.m_ping({})
    assert reply["timeline"] == {}
    text = _describe(reply)
    assert "could not read" in text
    assert "reload" not in text


def test_td_status_shows_the_line(monkeypatch):
    from td_atlas.mcp import server

    class Fake:
        ambiguity_warning = None
        version_warning = None

        def ping(self):
            return {"product": "TouchDesigner", "build": "2025.32460",
                    "project": "demo2.toe", "projectFolder": "/tmp/demo",
                    "fps": 60.0, **_info(end=8731, rangeEnd=600)}

    monkeypatch.setattr(server, "bridge", lambda: Fake())
    text = server.td_status()
    assert "timeline: frame 851 (playing)" in text
    assert "(!)" in text
    assert "frame 1284807" not in text, "the app clock is not called 'frame'"


def test_cli_status_shows_the_line(monkeypatch, capsys, tmp_path):
    from td_atlas import cli

    monkeypatch.setenv("TD_ATLAS_HOME", str(tmp_path))

    class Fake:
        port = 9977
        version_warning = None
        ambiguity_warning = None

        def ping(self):
            return {"product": "TouchDesigner", "build": "2025.32460",
                    "project": "demo2.toe", "projectFolder": "/tmp/demo",
                    "fps": 60.0, **_info(play=False)}

    monkeypatch.setattr(cli, "_client", lambda args, timeout=None: Fake())
    cli.main(["status"])
    out = capsys.readouterr().out
    assert "timeline      : frame 851 (paused)" in out
