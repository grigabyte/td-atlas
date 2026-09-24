"""One line saying what the project timeline is doing, from a `ping` reply.

Shared by `td_status` and `td-atlas status` so the two cannot word it
differently. The line exists for two failures that nothing else surfaced:

- The status line printed `frame 1284807`, which was `absTime.frame`, the
  application's clock, while the timeline stood at 851. Next to the word
  "frame" it read as the project's frame, and whether the timeline was playing
  was not shown at all — an agent trusted a note that said "paused", and a
  live CHOP cooked frames between capture steps that it did not expect.
- A `rangeEnd` left at 600 by a test stopped a recording at frame 600 with
  `play` on and no error. `end` had been put back; `rangeEnd` is what playback
  obeys.
"""

from __future__ import annotations

from typing import Any


def _num(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _range_warning(line: dict) -> str:
    """Why the playback range is narrower than the timeline, or "".

    Flagged every time rather than guessed at: a range narrower than the
    timeline can be deliberate, but the one case seen was a leftover, and
    what it costs when it is one — a recording that stops without a word —
    is worth a mark the reader can dismiss.
    """
    try:
        start, end = float(line["start"]), float(line["end"])
        low, high = float(line["rangeStart"]), float(line["rangeEnd"])
    except (KeyError, TypeError, ValueError):
        return ""
    reasons = []
    if high < end:
        reasons.append(
            f"playback stops at rangeEnd {_num(line['rangeEnd'])}, not end "
            f"{_num(line['end'])}"
        )
    if low > start:
        reasons.append(
            f"playback starts at rangeStart {_num(line['rangeStart'])}, not "
            f"start {_num(line['start'])}"
        )
    if not reasons:
        return ""
    return "; ".join(reasons) + " — a forgotten range? a recording stops there silently"


def describe(info: dict) -> str:
    """`timeline: frame 851 (playing) | start 1 end 994 | range 1–994 | ...`.

    A bridge older than this line sends no `timeline`, and then the line says
    so instead of printing the application clock under the timeline's name.
    """
    line = info.get("timeline")
    if not isinstance(line, dict) or "frame" not in line:
        return (
            "timeline: not reported by this bridge (run 'td-atlas reload' to "
            "update it)"
        )
    parts = []
    head = f"frame {_num(line['frame'])}"
    if "play" in line:
        head += " (playing)" if line["play"] else " (paused)"
    parts.append(head)
    if "start" in line and "end" in line:
        parts.append(f"start {_num(line['start'])} end {_num(line['end'])}")
    warning = _range_warning(line)
    if "rangeStart" in line and "rangeEnd" in line:
        parts.append(
            f"range {_num(line['rangeStart'])}–{_num(line['rangeEnd'])}"
            + (" (!)" if warning else "")
        )
    if "rate" in line:
        parts.append(f"rate {_num(line['rate'])}")
    absolute = info.get("absFrame", info.get("frame"))
    if absolute is not None:
        parts.append(f"abs {_num(absolute)}")
    text = "timeline: " + " | ".join(parts)
    if warning:
        text += f"\n  (!) {warning}"
    return text
