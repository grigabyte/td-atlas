"""Where a TOP chain's signal went: the verdict on a `trace` reply.

The bridge walks up a TOP's inputs and reads each image's minimum, mean and
maximum (component/handler.py, `m_trace`). This module decides which node to
point at, and it lives on the host so that decision can be tested without
TouchDesigner. It was asked for by an agent that did the walk by hand twice,
and both times it found the cause: a Level TOP in 16-bit float with no
clamp went to -0.21, and the Add below it took that away from what it added
to (report of 2026-09-20). The report blamed black level 0.42; measured
since, black level alone stops at 0, and contrast above 1 or a Range In Low
above 0 is what goes below it (handler.py, `_negative_float_risk`).

Four things are marked, each where it starts rather than everywhere it
shows: the node where the image goes black while an input still carried
something, an Add fed a negative input (where the negative subtracts), the
node where the alpha goes to 0 under colour that is still there, and the
first node with negative values or with NaN or Inf. A node whose input
could not be read is marked with a question mark, since the loss may have
come from the input nobody looked at.
"""

from __future__ import annotations

from typing import Any

# Half an 8-bit step. A value below it shows as 0 on an 8-bit output, so a
# node whose R, G and B all stay under it is black to anyone looking; a
# negative value past it is one a clamp at 0 would visibly change. Derived
# from the 8-bit quantum, not measured on a network.
_EPS = 0.5 / 255

_ERROR_EXCERPT = 80


def _numbers(values: Any, count: int = 3) -> list[float]:
    return [v for v in (values or [])[:count] if isinstance(v, (int, float))]


def state(entry: dict) -> str:
    """`unsampled`, `nan`, `negative`, `black`, `transparent` or `ok`."""
    stats = entry.get("stats")
    if not stats:
        return "unsampled"
    if stats.get("nan") or stats.get("inf"):
        return "nan"
    lows = _numbers(stats.get("min"))
    highs = _numbers(stats.get("max"))
    if lows and min(lows) < -_EPS:
        return "negative"
    if highs and max(highs) <= _EPS:
        return "black"
    # Colour with no alpha: invisible once composited over anything, while
    # every colour number above looks fine. What opacity 0 on a Level TOP
    # does to R, G and B has not been measured, so the alpha is judged on
    # its own rather than assumed to follow.
    alpha = (stats.get("max") or [])[3:4]
    if alpha and isinstance(alpha[0], (int, float)) and alpha[0] <= _EPS:
        return "transparent"
    return "ok"


def _adds(entry: dict) -> bool:
    """An operator where a negative input subtracts instead of adding."""
    kind = entry.get("type")
    operand = (entry.get("pars") or {}).get("operand")
    return kind == "addTOP" or (kind == "compositeTOP" and operand == "add")


def facts(entry: dict) -> list[str]:
    """What the node's own flags and parameters say, worded for a reader."""
    found = []
    if entry.get("bypass"):
        found.append("bypass")
    if entry.get("lock"):
        found.append("locked: holds a frozen image")
    wired = len(entry.get("inputs") or [])
    needed = entry.get("minInputs")
    if isinstance(needed, int) and wired < needed:
        found.append(f"input empty: {wired} of {needed} wired")
    opacity = (entry.get("pars") or {}).get("opacity")
    if isinstance(opacity, (int, float)) and not isinstance(opacity, bool) \
            and opacity <= 0:
        found.append("opacity 0")
    # Black level takes what is under it to 0, not below (2025.32460,
    # 2026-09-24: a Constant at 0.2 through a Level in rgba16float, clamp
    # off, black level 0.5 -> 0.0), so it is a reason for black, and never
    # one for negative values.
    black = (entry.get("pars") or {}).get("blacklevel")
    if isinstance(black, (int, float)) and not isinstance(black, bool) \
            and black > 0:
        found.append(f"blacklevel {black}, which cuts dark values to 0")
    risk = entry.get("negativeFloat")
    if risk:
        # The settings that take a Level below zero, measured on the same
        # build and day: contrast 3 -> -0.4 and inlow 0.5 -> -0.6 from 0.2;
        # outlow below 0 is the Range page's floor (handler.py,
        # `_negative_float_risk`).
        settings = risk.get("settings") or {}
        found.append(", ".join(
            [str(risk.get("format"))]
            + [f"{name} {value}" for name, value in settings.items()]
            + ["no clamp at 0"]
        ))
    if entry.get("errors"):
        line = str(entry["errors"]).strip().splitlines()[0]
        if len(line) > _ERROR_EXCERPT:
            line = line[:_ERROR_EXCERPT] + "..."
        found.append(f"error: {line}")
    return found


def _short(reason: str) -> str:
    return str(reason).split(":", 1)[0]


def verdicts(nodes: list[dict]) -> dict[str, tuple[str, list[str]]]:
    """Path -> (marker, reasons) for the nodes worth pointing at."""
    by_path = {n["path"]: n for n in nodes}
    states = {path: state(n) for path, n in by_path.items()}
    names = {path: n.get("name") or path for path, n in by_path.items()}
    marks: dict[str, tuple[str, list[str]]] = {}

    for entry in nodes:
        path = entry["path"]
        mine = states[path]
        inputs = entry.get("inputs") or []
        known = [p for p in inputs if states.get(p, "unsampled") != "unsampled"]
        unknown = [p for p in inputs if p not in known]
        seen = [states[p] for p in known]

        def hedge(reasons: list[str]) -> list[str]:
            for p in unknown:
                why = by_path[p].get("unsampled") if p in by_path else None
                why = _short(why) if why else "beyond the walk"
                reasons.append(
                    f"input {names.get(p, p)} was not sampled ({why}), so it "
                    f"may have come from there"
                )
            return reasons

        if mine == "nan" and "nan" not in seen:
            stats = entry["stats"]
            reasons = [f"{stats.get('nan', 0)} NaN and {stats.get('inf', 0)} "
                       f"Inf among the values read"]
            marker = "NaN/Inf start here" + ("?" if unknown else "")
            marks[path] = (marker, hedge(reasons + facts(entry)))
            continue
        if mine == "negative" and not {"nan", "negative"} & set(seen):
            marker = "negative values start here" + ("?" if unknown else "")
            marks[path] = (marker, hedge(facts(entry)))
            continue

        negative_inputs = [p for p in known if states[p] == "negative"]
        if _adds(entry) and negative_inputs and mine != "nan":
            listed = ", ".join(names[p] for p in negative_inputs)
            reasons = [f"adds {listed}, which holds negative values, so it "
                       f"subtracts"]
            marks[path] = ("dropped here", reasons + facts(entry))
            continue
        if mine == "transparent":
            if "ok" in seen or not inputs:
                marks[path] = ("alpha goes to 0 here", facts(entry))
            elif not seen:
                marks[path] = ("alpha goes to 0 here?", hedge(facts(entry)))
            continue
        if mine != "black":
            continue
        if "ok" in seen or not inputs:
            marks[path] = ("dropped here", facts(entry))
        elif not seen:
            marks[path] = ("dropped here?", hedge(facts(entry)))
    return marks


def _values(entry: dict) -> str:
    stats = entry.get("stats") or {}
    lows = _numbers(stats.get("min"))
    means = _numbers(stats.get("mean"))
    highs = _numbers(stats.get("max"))
    if not (lows and means and highs):
        return "no finite values"
    mean = sum(means) / len(means)
    alpha = (stats.get("mean") or [None] * 4)[3:4]
    alpha_text = (
        f"a {alpha[0]:5.2f}" if alpha and isinstance(alpha[0], (int, float))
        else "a   -  "
    )
    return f"{min(lows):7.3f} {mean:7.3f} {max(highs):7.3f}  {alpha_text}"


def render(reply: dict) -> str:
    """The trace as a table, marked where the signal was lost."""
    nodes = reply.get("nodes") or []
    root = reply.get("root") or (nodes[0]["path"] if nodes else "?")
    depth = reply.get("depth")
    marks = verdicts(nodes)

    counts: dict[str, int] = {}
    for entry in nodes:
        name = entry.get("name") or entry["path"]
        counts[name] = counts.get(name, 0) + 1
    labels = {}
    for entry in nodes:
        name = entry.get("name") or entry["path"]
        label = name if counts[name] == 1 else entry["path"]
        labels[entry["path"]] = "  " * int(entry.get("depth") or 0) + label
    width = max((len(label) for label in labels.values()), default=4) + 2

    sampled = sum(1 for n in nodes if n.get("stats"))
    strides = sorted({n["stats"].get("stride") for n in nodes
                      if n.get("stats") and n["stats"].get("stride")})
    stride_text = (
        "every pixel" if strides == [1]
        else f"one pixel in {strides[0]} along each side" if len(strides) == 1
        else f"one pixel in {strides[0]} to {strides[-1]} along each side"
        if strides else "no pixels"
    )
    lines = [
        f"Traced {root} up its inputs: {len(nodes)} node(s), {sampled} read, "
        f"depth up to {depth}, at absolute frame {reply.get('frame')}.",
        f"Columns: min / mean / max over R, G and B, then the alpha mean; "
        f"read at {stride_text}.",
    ]

    first = nodes[0] if nodes else {}
    if first.get("cooks") == 0:
        lines.append(
            f"{root} has never cooked: nothing has asked TouchDesigner for "
            f"this image (no open viewer, display, render or output reads it), "
            f"so there is no picture to be black. Make something read it."
        )
    elif marks:
        lines.append(
            "Look at: " + ", ".join(
                f"{path} ({marker})" for path, (marker, _) in marks.items()
            ) + "."
        )
    else:
        lines.append(
            "No node in this chain is black, transparent, negative or NaN, so "
            "the signal is not lost anywhere these values can show. Compare "
            "the look with td_render, or trace from a node further down."
        )
    lines.append("")

    for entry in nodes:
        label = labels[entry["path"]].ljust(width)
        if entry.get("stats"):
            body = _values(entry)
        else:
            body = f"not sampled: {entry.get('unsampled') or 'no reading'}"
        tags = [f for f in facts(entry)
                if entry["path"] not in marks and f in ("bypass",)]
        if tags:
            body += "  [" + ", ".join(tags) + "]"
        mark = marks.get(entry["path"])
        if mark:
            marker, reasons = mark
            body += f"  ← {marker}"
            if reasons:
                body += ": " + "; ".join(reasons)
        lines.append(f"  {label}{body}".rstrip())

    notes = []
    unvisited = reply.get("unvisited") or 0
    if unvisited:
        notes.append(
            f"{unvisited} input(s) past the {reply.get('maxNodes')}-node "
            f"ceiling were not visited; trace again from a node near the "
            f"bottom of this table."
        )
    deeper = [labels[n["path"]].strip() for n in nodes if n.get("deeper")]
    if deeper:
        notes.append(
            f"The walk stopped at depth {depth}; inputs continue above "
            f"{', '.join(deeper)}. Trace again from there, or with a larger "
            f"depth."
        )
    budget = reply.get("budget") or {}
    if budget.get("stopped"):
        notes.append(
            "A read budget ran out, so the nodes after it were walked but not "
            "read; each says which budget. Trace again from one of them."
        )
    notes.append(
        "Wires only: an image read through a parameter (a Select TOP's top, "
        "a Render TOP's camera and geometry) is not followed."
    )
    lines.append("")
    lines.extend(notes)
    return "\n".join(lines)
