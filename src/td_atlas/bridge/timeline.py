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


PROFILE_ROWS = 40


def _ms(value: float | None) -> str:
    return "     —" if value is None else f"{value:6.2f}"


def describe_profile(profile: dict, walked: int, rows: int = PROFILE_ROWS) -> list[str]:
    """The profile's table, costliest first, each row saying what it is worth.

    A row is a forced cook timed with the GPU waited for. What makes a number
    misleading is said on its own row, not in a legend: an operator that did
    not cook on its own on any frame walked is not part of the frame's cost
    (report 3's `44 ms` from a cook 374,144 frames old), and one cooked inside
    an earlier operator's measurement has its cost in that operator's too.
    """
    lines = [
        f"  measured {profile.get('measured', 0)} of {walked} frames, "
        f"{profile.get('nodes', 0)} operators"
    ]
    table = profile.get("rows") or []
    if not table:
        return lines
    # A pulled operator's cost is already inside the operator that pulled it,
    # so adding its own row would count it twice.
    counted = [
        row for row in table
        if row.get("mean") is not None and row.get("cooked")
    ]
    pulled = [row for row in counted if row.get("pulled")]
    frame_sum = sum(row["mean"] for row in counted if not row.get("pulled"))
    total = f"  sum of means of operators that cooked on their own: {frame_sum:.2f} ms"
    if pulled:
        total += (
            f" ({len(pulled)} pulled operator(s) left out: their cost is inside "
            f"the operator that pulled them)"
        )
    lines.append(total)
    lines.append("    mean     max  cooked  operator (ms per frame, GPU waited for)")
    for row in table[:rows]:
        frames = row.get("frames") or 0
        text = (
            f"  {_ms(row.get('mean'))}  {_ms(row.get('max'))}  "
            f"{row.get('cooked', 0):>3}/{frames:<3} {row['path']} ({row.get('type')})"
        )
        marks = []
        if frames and not row.get("cooked"):
            marks.append("did not cook on its own on any frame walked — not in the frame's cost")
        if row.get("pulled"):
            marks.append(
                f"cooked inside an earlier operator's measurement on {row['pulled']} "
                f"frame(s), so its cost is counted there too"
            )
        if row.get("refused"):
            marks.append(f"the forced cook did not happen on {row['refused']} frame(s)")
        if row.get("errors"):
            marks.append(f"{row['errors']} failed: {row.get('error')}")
        if marks:
            text += " — " + "; ".join(marks)
        lines.append(text)
    if len(table) > rows:
        lines.append(f"  … {len(table) - rows} cheaper operator(s) not shown")
    if profile.get("truncated"):
        lines.append(
            f"  at least {profile['truncated']} more operator(s) were not walked; "
            f"the note above says which ceiling stopped the walk"
        )
    return lines


def describe(job: dict) -> list[str]:
    """A job's progress as lines an agent reads at a glance."""
    if not job.get("job"):
        return ["no timeline job has run in this TouchDesigner session"]
    first, last = job["walk"]
    what = {
        "capture": "saving frames of",
        "profile": "profiling everything under",
    }.get(job.get("mode"), "advancing")
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
    if isinstance(job.get("profile"), dict):
        lines.extend(describe_profile(job["profile"], last - first + 1))
    return lines
