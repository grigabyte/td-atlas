"""The host half of a timeline job: reading frame specs, describing progress.

The walk itself runs inside TouchDesigner (`m_timeline_run` and the notes
above it in `component/handler.py`); this side only turns what an agent types
into ranges and what the bridge reports into lines.
"""

from __future__ import annotations

import re

_RANGE = re.compile(r"^\s*(-?\d+)\s*(?:(?:\.\.|-)\s*(-?\d+))?\s*$")


def parse_frames(spec: str) -> list[list[int]]:
    """`"92..217,459..541"` as `[[92, 217], [459, 541]]`, inclusive.

    A single number is a range of one frame, and `a-b` reads as `a..b`. Refuses
    rather than guesses: a range that runs backwards or a part that is not a
    number is an error, since a misread spec costs a whole walk.
    """
    parts = [part for part in (spec or "").split(",") if part.strip()]
    if not parts:
        raise ValueError("no frames given; write them as '1..994' or '92..217,459..541'")
    ranges = []
    for part in parts:
        match = _RANGE.match(part)
        if not match:
            raise ValueError(
                f"{part.strip()!r} is not a frame or a range; write '12' or '1..994'"
            )
        first = int(match.group(1))
        last = int(match.group(2)) if match.group(2) is not None else first
        if last < first:
            raise ValueError(f"the range {first}..{last} runs backwards")
        ranges.append([first, last])
    return ranges


def describe(job: dict) -> list[str]:
    """A job's progress as lines an agent reads at a glance."""
    if not job.get("job"):
        return ["no timeline job has run in this TouchDesigner session"]
    first, last = job["walk"]
    what = "saving frames of" if job.get("mode") == "capture" else "advancing"
    lines = [f"job {job['job']}: {job['state']} — {what} {job['path']}"]
    where = f"frame {job['frame']} of walk {first}..{last}"
    if job.get("tiles", 1) > 1 and job.get("tile") is not None:
        where = f"pass {job['tile'] + 1} of {job['tiles']} (tile {job['tile']}), " + where
    where += f", timeline {job.get('timeline') or '?'}"
    lines.append("  " + where)
    if job.get("mode") == "capture":
        saved = f"  saved {job['saved']} of {job['toSave']}"
        if job.get("lastFile"):
            saved += f", last {job['lastFile']}"
        lines.append(saved)
    lines.append(
        f"  {job['elapsed']:.1f} s elapsed, a step every {job['settle']} "
        f"application frame(s)"
    )
    if job.get("stalled"):
        lines.append(
            f"  stalled: no step for {job['sinceStep']:.1f} s. TouchDesigner may "
            f"be busy, asleep, or holding a dialog; td_timeline_cancel puts the "
            f"timeline and crop back"
        )
    if job.get("error"):
        lines.append(f"  error: {job['error']}")
    for error in job.get("errors") or []:
        lines.append(f"  not put back: {error}")
    for note in job.get("notes") or []:
        lines.append(f"  note: {note}")
    restored = job.get("restored")
    if restored:
        play = restored.get("play")
        if play is not None:
            mode = "playing" if play else "paused"
            lines.append(f"  timeline left {mode}")
        if restored.get("crop"):
            lines.append("  crop back to 0..1 on " + ", ".join(restored["crop"]))
    return lines
