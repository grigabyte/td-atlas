"""`td_trace`: where a TOP chain's signal went. No TouchDesigner required.

Two halves, tested apart. The walk runs inside TouchDesigner and is driven
here with stand-in operators and an injected sampler, so the order, the
ceilings and every "not sampled" reason are checked without numpy or a GPU.
The verdict — which node the signal was lost at, and why — is read on the
host from a reply, and is checked on replies written out by hand.
"""

from __future__ import annotations

import pytest

from td_atlas.bridge import trace
from td_atlas.component import handler
from td_atlas.mcp import server

# -- the walk, inside TouchDesigner ---------------------------------------------


class Par:
    def __init__(self, value):
        self.value = value

    def eval(self):
        return self.value


class Pars:
    def __init__(self, **values):
        for name, value in values.items():
            setattr(self, name, Par(value))


class Node:
    def __init__(self, path, inputs=(), family="TOP", op_type="levelTOP",
                 cooks=3, min_inputs=0, width=64, height=64,
                 fmt="rgba8fixed", bypass=False, errors="", **pars):
        self.path = path
        self.name = path.rsplit("/", 1)[-1]
        self.inputs = list(inputs)
        self.outputs = []
        for source in self.inputs:
            source.outputs.append(self)
        self.family = family
        self.OPType = op_type
        self.totalCooks = cooks
        self.minInputs = min_inputs
        self.width = width
        self.height = height
        self.pixelFormatName = fmt
        self.bypass = bypass
        self.lock = False
        self._errors = errors
        self.par = Pars(**pars)

    def errors(self, recurse=False):
        return self._errors

    def warnings(self, recurse=False):
        return ""


GOOD = {"min": [0.1, 0.1, 0.1, 1.0], "mean": [0.3, 0.3, 0.3, 1.0],
        "max": [0.6, 0.6, 0.6, 1.0], "nan": 0, "inf": 0, "stride": 1,
        "pixels": 4096, "channels": 4}


def sampler(record=None):
    def sample(node):
        if record is not None:
            record.append(node.path)
        return dict(GOOD)
    return sample


def walk(target, depth=12, sample=None, clock=None):
    kwargs = {} if clock is None else {"clock": clock}
    return handler._trace_walk(target, depth, sample or sampler(), **kwargs)


def test_trace_is_a_bridge_method():
    assert handler.METHODS["trace"] is handler.m_trace


def test_the_walk_goes_up_the_inputs_breadth_first_and_once_per_node():
    src = Node("/p/src", op_type="noiseTOP")
    left = Node("/p/left", [src])
    right = Node("/p/right", [src])
    out = Node("/p/out", [left, right], op_type="compositeTOP")

    result = walk(out)

    paths = [e["path"] for e in result["nodes"]]
    assert paths == ["/p/out", "/p/left", "/p/right", "/p/src"]
    by = {e["path"]: e for e in result["nodes"]}
    assert by["/p/src"]["depth"] == 2
    assert by["/p/src"]["below"] == "/p/left"
    assert by["/p/out"]["inputs"] == ["/p/left", "/p/right"]
    assert all("stats" in e for e in result["nodes"])
    assert result["unvisited"] == 0


def test_the_walk_stops_at_the_depth_it_was_given_and_says_so():
    chain = Node("/p/n0", op_type="noiseTOP")
    for i in range(1, 6):
        chain = Node(f"/p/n{i}", [chain])

    result = walk(chain, depth=2)

    paths = [e["path"] for e in result["nodes"]]
    assert paths == ["/p/n5", "/p/n4", "/p/n3"]
    assert result["nodes"][-1]["deeper"] is True


def test_the_walk_stops_at_its_node_ceiling_and_counts_what_it_left():
    sources = [Node(f"/p/s{i}", op_type="noiseTOP") for i in range(45)]
    out = Node("/p/out", sources, op_type="compositeTOP")

    result = walk(out)

    assert len(result["nodes"]) == handler._TRACE_MAX_NODES == 40
    assert result["unvisited"] == 46 - 40


def test_a_non_top_input_is_named_but_neither_sampled_nor_followed():
    chop_source = Node("/p/lfo", family="CHOP", op_type="lfoCHOP")
    chop = Node("/p/math", [chop_source], family="CHOP", op_type="mathCHOP")
    out = Node("/p/out", [chop], op_type="choptoTOP")
    seen = []

    result = walk(out, sample=sampler(seen))

    by = {e["path"]: e for e in result["nodes"]}
    assert "/p/lfo" not in by
    assert "not a TOP" in by["/p/math"]["unsampled"]
    assert seen == ["/p/out"]


def test_a_node_that_never_cooked_is_not_read_and_says_why():
    idle = Node("/p/idle", op_type="noiseTOP", cooks=0)
    out = Node("/p/out", [idle])
    seen = []

    result = walk(out, sample=sampler(seen))

    by = {e["path"]: e for e in result["nodes"]}
    assert "never cooked" in by["/p/idle"]["unsampled"]
    assert "stats" not in by["/p/idle"]
    assert seen == ["/p/out"]


def test_a_read_that_fails_or_returns_nothing_is_reported_as_unread():
    def sample(node):
        if node.path == "/p/a":
            raise RuntimeError("download failed")
        return None

    a = Node("/p/a", op_type="noiseTOP")
    out = Node("/p/out", [a])
    by = {e["path"]: e for e in walk(out, sample=sample)["nodes"]}

    assert "RuntimeError" in by["/p/a"]["unsampled"]
    assert "no image" in by["/p/out"]["unsampled"]


def test_the_time_budget_stops_the_reads_but_not_the_walk():
    ticks = iter(range(100))
    chain = Node("/p/n0", op_type="noiseTOP")
    for i in range(1, 4):
        chain = Node(f"/p/n{i}", [chain])

    # One tick per clock read; the deadline is two ticks past the start.
    result = handler._trace_walk(
        chain, 12, sampler(), clock=lambda: next(ticks), budget_s=2,
    )

    unread = [e["path"] for e in result["nodes"] if "unsampled" in e]
    assert len(result["nodes"]) == 4
    assert unread and "time budget" in result["nodes"][-1]["unsampled"]
    assert result["budget"]["stopped"] is True


def test_the_byte_budget_skips_an_image_too_large_to_download():
    big = Node("/p/big", op_type="noiseTOP", width=8192, height=8192)
    out = Node("/p/out", [big], width=8, height=8)

    result = handler._trace_walk(out, 12, sampler(), budget_bytes=1024 * 1024)

    by = {e["path"]: e for e in result["nodes"]}
    assert "stats" in by["/p/out"]
    assert "byte budget" in by["/p/big"]["unsampled"]


def test_a_level_that_can_go_negative_carries_the_parameters_that_say_so():
    src = Node("/p/src", op_type="noiseTOP")
    lev = Node("/p/lev", [src], fmt="rgba16float", blacklevel=0.42,
               contrast=1.0, inlow=0.5, outlow=0.0,
               clamp=False, clamplow2=0.0, opacity=1.0)
    add = Node("/p/add", [src, lev], op_type="addTOP")

    by = {e["path"]: e for e in walk(add)["nodes"]}

    risk = by["/p/lev"]["negativeFloat"]
    assert risk["format"] == "rgba16float"
    assert risk["settings"] == {"inlow": 0.5}
    assert risk["adds"] == ["/p/add"]
    assert by["/p/lev"]["pars"]["blacklevel"] == 0.42
    assert by["/p/lev"]["pars"]["opacity"] == 1.0


def test_m_trace_refuses_a_non_top(monkeypatch):
    comp = Node("/p/geo", family="COMP", op_type="geometryCOMP")
    monkeypatch.setattr(handler, "op", lambda path: comp, raising=False)
    with pytest.raises(TypeError, match="TOP"):
        handler.m_trace({"path": "/p/geo"})


# -- the verdict, on the host ---------------------------------------------------


def row(path, inputs=(), op_type="levelTOP", depth=0, stats=None, **extra):
    entry = {
        "path": path, "name": path.rsplit("/", 1)[-1], "type": op_type,
        "family": "TOP", "depth": depth, "inputs": list(inputs),
        "minInputs": 0, "bypass": False, "lock": False, "cooks": 5,
        "errors": None, "pars": {},
    }
    if stats is not None:
        entry["stats"] = stats
    entry.update(extra)
    return entry


def rgb(lo, mid, hi, alpha=1.0, **extra):
    stats = {"min": [lo] * 3 + [alpha], "mean": [mid] * 3 + [alpha],
             "max": [hi] * 3 + [alpha], "nan": 0, "inf": 0, "stride": 4,
             "pixels": 4096, "channels": 4}
    stats.update(extra)
    return stats


def reply(nodes, **extra):
    base = {"root": nodes[0]["path"], "frame": 2013719, "depth": 12,
            "nodes": nodes, "unvisited": 0, "maxNodes": 40,
            "budget": {"seconds": 0.5, "stopped": False}}
    base.update(extra)
    return base


def marked(text):
    return [line for line in text.splitlines() if "←" in line]


# The case from the report that asked for this tool: a Level in 16-bit float
# with no clamp went to -0.21, and the Add below it took that away from what
# it added to. The report put it down to black level 0.42, but black level
# alone takes values to 0, not below (2025.32460, 2026-09-24: 0.2 in, 0.0 out
# at black level 0.5), so that Level had contrast or a Range setting as well;
# which one was not recorded, and contrast stands in for it here.
NEGATIVE_LEVEL = reply([
    row("/p/outT", ["/p/grade"], "nullTOP", 0, rgb(0.008, 0.028, 0.071)),
    row("/p/grade", ["/p/ray_add"], "levelTOP", 1, rgb(0.008, 0.028, 0.071)),
    row("/p/ray_add", ["/p/post_out", "/p/ray_lev"], "addTOP", 2,
        rgb(0.0, 0.009, 0.020)),
    row("/p/post_out", [], "noiseTOP", 3, rgb(0.032, 0.037, 0.046)),
    row("/p/ray_lev", ["/p/rays"], "levelTOP", 3, rgb(-0.21, -0.21, -0.21),
        negativeFloat={"format": "rgba16float",
                       "settings": {"contrast": 3.0},
                       "adds": ["/p/ray_add"], "depth": 4}),
    row("/p/rays", [], "noiseTOP", 4, rgb(0.0, 0.2, 0.5)),
])


def test_the_negative_level_is_where_the_values_went_wrong_and_the_add_where_it_dropped():
    text = trace.render(NEGATIVE_LEVEL)

    lines = marked(text)
    drops = [line for line in lines if "dropped here" in line]
    assert len(drops) == 1 and "ray_add" in drops[0]
    assert "ray_lev" in drops[0]  # the reason names the input that subtracts

    negatives = [line for line in lines if "negative values start here" in line]
    assert len(negatives) == 1 and "ray_lev" in negatives[0]
    assert "rgba16float" in negatives[0]
    assert "contrast 3.0" in negatives[0]
    assert "blacklevel" not in negatives[0]
    assert "no clamp" in negatives[0]

    assert len(lines) == 2
    for quiet in ("outT", "grade", "post_out", "rays"):
        assert not any(quiet in line.split("←")[0] for line in lines), quiet


def test_the_same_chain_driven_to_black_still_marks_the_add_once():
    nodes = [dict(n) for n in NEGATIVE_LEVEL["nodes"]]
    for entry in nodes[:3]:
        entry["stats"] = rgb(0.0, 0.0, 0.0)
    text = trace.render(reply(nodes))

    drops = [line for line in marked(text) if "dropped here" in line]
    assert len(drops) == 1 and "ray_add" in drops[0]


def test_a_bypassed_source_is_where_the_image_went_black():
    text = trace.render(reply([
        row("/p/out", ["/p/lev"], "nullTOP", 0, rgb(0, 0, 0)),
        row("/p/lev", ["/p/noise"], "levelTOP", 1, rgb(0, 0, 0)),
        row("/p/noise", [], "noiseTOP", 2, rgb(0, 0, 0), bypass=True),
    ]))

    lines = marked(text)
    assert len(lines) == 1
    assert "noise" in lines[0] and "dropped here" in lines[0]
    assert "bypass" in lines[0]


def test_a_bypassed_node_that_passes_a_good_signal_is_shown_but_not_blamed():
    text = trace.render(reply([
        row("/p/out", ["/p/lev"], "nullTOP", 0, rgb(0.1, 0.3, 0.6)),
        row("/p/lev", ["/p/noise"], "levelTOP", 1, rgb(0.1, 0.3, 0.6),
            bypass=True),
        row("/p/noise", [], "noiseTOP", 2, rgb(0.1, 0.3, 0.6)),
    ]))

    assert marked(text) == []
    lev = next(line for line in text.splitlines() if " lev " in line + " ")
    assert "bypass" in lev
    assert "No node in this chain" in text


def test_opacity_zero_and_an_empty_input_are_named_as_reasons():
    text = trace.render(reply([
        row("/p/out", ["/p/lev"], "nullTOP", 0, rgb(0, 0, 0, alpha=0.0)),
        row("/p/lev", ["/p/noise"], "levelTOP", 1, rgb(0, 0, 0, alpha=0.0),
            pars={"opacity": 0.0}),
        row("/p/noise", [], "noiseTOP", 2, rgb(0.1, 0.3, 0.6)),
    ]))
    lines = marked(text)
    assert len(lines) == 1 and "lev" in lines[0] and "opacity 0" in lines[0]

    # Colour left standing under an alpha of 0: what opacity 0 does to R, G
    # and B is not assumed, so the alpha alone has to find it.
    text = trace.render(reply([
        row("/p/out", ["/p/lev"], "nullTOP", 0, rgb(0.1, 0.3, 0.6, alpha=0.0)),
        row("/p/lev", ["/p/noise"], "levelTOP", 1,
            rgb(0.1, 0.3, 0.6, alpha=0.0), pars={"opacity": 0.0}),
        row("/p/noise", [], "noiseTOP", 2, rgb(0.1, 0.3, 0.6)),
    ]))
    lines = marked(text)
    assert len(lines) == 1 and "lev" in lines[0]
    assert "alpha goes to 0" in lines[0] and "opacity 0" in lines[0]

    text = trace.render(reply([
        row("/p/out", ["/p/blur"], "nullTOP", 0, rgb(0, 0, 0)),
        row("/p/blur", [], "blurTOP", 1, rgb(0, 0, 0), minInputs=1),
    ]))
    lines = marked(text)
    assert len(lines) == 1 and "blur" in lines[0]
    assert "0 of 1" in lines[0]


def test_a_black_level_is_named_where_the_image_went_to_zero():
    # Black level cuts to 0 rather than below it: 2025.32460, 2026-09-24, a
    # Constant at 0.2 through a Level in rgba16float with black level 0.5
    # came out at 0.0.
    text = trace.render(reply([
        row("/p/out", ["/p/lev"], "nullTOP", 0, rgb(0, 0, 0)),
        row("/p/lev", ["/p/src"], "levelTOP", 1, rgb(0, 0, 0),
            pars={"blacklevel": 0.5}),
        row("/p/src", [], "constantTOP", 2, rgb(0.2, 0.2, 0.2)),
    ]))
    lines = marked(text)
    assert len(lines) == 1 and "lev" in lines[0]
    assert "dropped here" in lines[0]
    assert "blacklevel 0.5" in lines[0]


def test_nan_is_marked_where_it_first_appears():
    text = trace.render(reply([
        row("/p/out", ["/p/glsl"], "nullTOP", 0, rgb(0, 0, 0, nan=4096)),
        row("/p/glsl", ["/p/noise"], "glslTOP", 1, rgb(0, 0, 0, nan=4096)),
        row("/p/noise", [], "noiseTOP", 2, rgb(0.1, 0.3, 0.6)),
    ]))
    lines = marked(text)
    assert len(lines) == 1 and "glsl" in lines[0] and "NaN" in lines[0]


def test_a_node_that_never_cooked_is_said_so_and_the_mark_below_it_is_hedged():
    text = trace.render(reply([
        row("/p/out", ["/p/idle"], "nullTOP", 0, rgb(0, 0, 0)),
        row("/p/idle", [], "noiseTOP", 1, cooks=0,
            unsampled="never cooked: nothing has asked for this image"),
    ]))

    idle = next(line for line in text.splitlines() if "idle" in line)
    assert "not sampled" in idle and "never cooked" in idle
    lines = marked(text)
    assert len(lines) == 1 and "out" in lines[0]
    assert "?" in lines[0] and "idle" in lines[0]


def test_an_output_that_never_cooked_is_the_whole_answer():
    text = trace.render(reply([
        row("/p/out", ["/p/src"], "nullTOP", 0, cooks=0,
            unsampled="never cooked: nothing has asked for this image"),
        row("/p/src", [], "noiseTOP", 1, cooks=0,
            unsampled="never cooked: nothing has asked for this image"),
    ]))
    assert "/p/out has never cooked" in text


def test_cuts_are_named():
    text = trace.render(reply(
        [row("/p/out", ["/p/a"], "nullTOP", 0, rgb(0.1, 0.3, 0.6),
             deeper=True)],
        unvisited=3,
        budget={"seconds": 0.5, "stopped": True},
    ))
    assert "3 input(s)" in text
    assert "deeper" in text or "depth" in text
    assert "budget" in text


# -- the tool ---------------------------------------------------------------------


class TraceClient:
    ambiguity_warning = None
    version_warning = None

    def __init__(self, result):
        self.result = result
        self.calls = []

    def call(self, method, **params):
        self.calls.append((method, params))
        return self.result


def test_td_trace_asks_the_bridge_and_renders_the_verdict(monkeypatch):
    client = TraceClient(NEGATIVE_LEVEL)
    monkeypatch.setattr(server, "bridge", lambda: client)

    text = server.td_trace("/p/outT", depth=6)

    assert client.calls == [("trace", {"path": "/p/outT", "depth": 6})]
    assert "dropped here" in text
