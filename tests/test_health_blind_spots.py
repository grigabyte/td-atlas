"""What td_health says about the things a running network does not say itself.

Each case here was paid for in a live session (the agent reports kept beside
the repository, 2026-09-20 and 2026-09-24):

- a cook time read hundreds of thousands of frames after the cook it timed,
  reported as a culprit for the frame rate of now — five calls on a false
  trail;
- a Feedback TOP that `cook(force=True)` does not advance — an hour;
- a bypassed Level TOP taken for a switched-off layer;
- a Level TOP with a black level in a float format handing negative values to
  an Add, which darkened what it was added to;
- a camera driven by `absTime.seconds`, the application's clock, so no two
  recordings moved alike.

None of this needs TouchDesigner: the host half reads prepared samples, and
the bridge half walks plain objects carrying the attributes it reads.
"""

from __future__ import annotations

import pytest

from td_atlas.bridge import health
from td_atlas.component import handler


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(health.time, "sleep", lambda _s: None)


class Client:
    def __init__(self, first, second):
        self._replies = [first, second]

    def call(self, method, **_params):
        if method == "status_note":
            return {"stored": True}
        assert method == "health_sample"
        return self._replies.pop(0)


def node(path, cooks, **over):
    base = {
        "path": path,
        "type": over.pop("type", "noiseTOP"),
        "family": over.pop("family", "TOP"),
        "cooks": cooks,
        "cookTime": 0.1,
        "bypass": False,
        "errors": None,
        "warnings": None,
    }
    base.update(over)
    return base


def sample(frame, nodes, **over):
    base = {
        "frame": frame,
        "fpsTarget": 60.0,
        "playing": True,
        "realTime": True,
        "license": {"type": "TouchDesigner Commercial"},
        "product": "TouchDesigner",
        "nodes": nodes,
    }
    base.update(over)
    return base


def check(first, second):
    return health.check(Client(first, second), publish=False)


def finding(result, kind):
    found = [f for f in result.findings if f.kind == kind]
    return found[0] if found else None


# -- B1: a cook time is only news if the cook was recent ---------------------

def test_an_operator_that_last_cooked_long_ago_is_not_blamed_for_this_frame():
    """The false trail from the first report, in its own numbers.

    `moviefilein1` last cooked at absolute frame 353 and the clock reads
    374497, so its 44 ms is a measurement from long ago. `ray_blur` is cooking
    every frame. Only the second belongs in the list of what costs the frame.
    """
    first = sample(374437, [
        node("/project1/moviefilein1", 5, cookTime=44.0,
             cookAbsFrame=353.0, cookFrame=353.0),
        node("/project1/cement/ray_blur", 1000, cookTime=74.0,
             cookAbsFrame=374436.0, cookFrame=3000.0),
    ])
    second = sample(374497, [
        node("/project1/moviefilein1", 5, cookTime=44.0,
             cookAbsFrame=353.0, cookFrame=353.0),
        node("/project1/cement/ray_blur", 1060, cookTime=74.0,
             cookAbsFrame=374496.0, cookFrame=3060.0),
    ])
    result = check(first, second)

    expensive = finding(result, "expensive")
    assert expensive is not None
    assert len(expensive.paths) == 1
    assert expensive.paths[0].startswith("/project1/cement/ray_blur (74 ms")
    assert "374496" in expensive.paths[0]
    assert "1 frame(s) ago, during this check" in expensive.paths[0]

    stale = finding(result, "expensive-stale")
    assert stale is not None and stale.severity == "note"
    assert len(stale.paths) == 1
    line = stale.paths[0]
    assert line.startswith("/project1/moviefilein1 (44 ms")
    assert "374144 frames ago" in line
    assert "353" in line
    assert "not part of the current frame" in stale.message


def test_a_node_that_cooked_inside_the_window_is_current():
    # Sampled before it cooked in the frame the first sample was taken in —
    # one frame of allowance, so a node cooking every frame is never stale.
    first = sample(100, [node("/p/a", 10, cookTime=12.0, cookAbsFrame=99.0)])
    second = sample(160, [node("/p/a", 70, cookTime=12.0, cookAbsFrame=159.0)])
    result = check(first, second)
    assert finding(result, "expensive") is not None
    assert finding(result, "expensive-stale") is None


def test_a_bridge_without_cook_frames_keeps_the_old_report():
    first = sample(0, [node("/p/slow", 100, cookTime=430.0)])
    second = sample(60, [node("/p/slow", 160, cookTime=430.0)])
    result = check(first, second)
    assert finding(result, "expensive").paths == ["/p/slow (430 ms)"]
    assert finding(result, "expensive-stale") is None


# -- B2: feedback loops that a forced cook does not advance -----------------

def test_feedback_loops_are_named_with_their_targets():
    nodes = [
        node("/project1/cement/pfb", 10, type="feedbackTOP",
             feedback={"target": "/project1/cement/post_out", "reset": False}),
        node("/project1/cement/wf_fb", 10, type="feedbackTOP",
             feedback={"target": "/project1/cement/wf_out", "reset": False}),
    ]
    after = [dict(n, cooks=70) for n in nodes]
    result = check(sample(0, nodes), sample(60, after))

    loop = finding(result, "feedback-loops")
    assert loop is not None and loop.severity == "note"
    assert loop.paths == [
        "/project1/cement/pfb (target /project1/cement/post_out)",
        "/project1/cement/wf_fb (target /project1/cement/wf_out)",
    ]
    assert "cook(force=True)" in loop.message
    assert "play the timeline" in loop.message


def test_a_feedback_operator_held_in_reset_is_not_a_loop():
    nodes = [node("/p/fb", 10, type="feedbackTOP",
                  feedback={"target": "/p/out", "reset": True})]
    result = check(sample(0, nodes), sample(60, [dict(nodes[0], cooks=70)]))
    assert finding(result, "feedback-loops") is None


# -- B3: bypass on a gain operator passes the signal through ----------------

def test_a_bypassed_level_is_said_to_pass_its_input_through():
    nodes = [node("/p/glow_lev", 10, type="levelTOP", bypass=True),
             node("/p/blur1", 10, type="blurTOP", bypass=True)]
    after = [dict(n, cooks=70) for n in nodes]
    result = check(sample(0, nodes), sample(60, after))

    through = finding(result, "bypass-passes-through")
    assert through is not None and through.severity == "note"
    assert through.paths == ["/p/glow_lev (levelTOP)"]
    assert "does not switch" in through.message
    # Each bypassed operator is named once: a gain operator under the note
    # that says what its bypass does, every other one under the warning.
    assert finding(result, "bypassed").paths == ["/p/blur1"]


def test_gain_operators_alone_leave_no_empty_bypass_warning():
    nodes = [node("/p/glow_lev", 10, type="levelTOP", bypass=True)]
    result = check(sample(0, nodes), sample(60, [dict(nodes[0], cooks=70)]))
    assert finding(result, "bypassed") is None
    assert finding(result, "bypass-passes-through").paths == [
        "/p/glow_lev (levelTOP)"]


def test_the_bypass_warning_counts_the_gain_operators_it_leaves_to_the_note():
    nodes = [node("/p/glow_lev", 10, type="levelTOP", bypass=True),
             node("/p/blur1", 10, type="blurTOP", bypass=True)]
    result = check(sample(0, nodes), sample(60, [dict(n, cooks=70) for n in nodes]))
    assert "bypass-passes-through" in finding(result, "bypassed").message


# -- B4: negative values out of a Level TOP in a float format ---------------

def test_a_level_handing_negative_values_to_an_add_is_a_warning():
    risk = {"format": "rgba16float", "blacklevel": 0.42,
            "adds": ["/project1/cement/ray_add"], "depth": 4}
    nodes = [node("/project1/cement/ray_lev", 10, type="levelTOP",
                  negativeFloat=risk)]
    result = check(sample(0, nodes), sample(60, [dict(nodes[0], cooks=70)]))

    negative = finding(result, "negative-float")
    assert negative is not None and negative.severity == "warning"
    assert negative.paths == [
        "/project1/cement/ray_lev (rgba16float, black level 0.42, "
        "added in /project1/cement/ray_add)"
    ]
    assert "clamp" in negative.message


def test_a_level_with_no_add_downstream_is_only_a_note():
    risk = {"format": "rgba32float", "blacklevel": 0.1, "adds": [], "depth": 4}
    nodes = [node("/p/lev", 10, type="levelTOP", negativeFloat=risk)]
    result = check(sample(0, nodes), sample(60, [dict(nodes[0], cooks=70)]))
    negative = finding(result, "negative-float")
    assert negative is not None and negative.severity == "note"
    assert "within 4 operators downstream" in negative.message


# -- B5: a frame that is not the same frame on the next run -----------------

def test_reads_of_the_application_clock_are_named():
    reads = [
        {"path": "/project1/cement/cam_cb", "where": "text",
         "call": "absTime.seconds"},
        {"path": "/project1/cement/filmlook", "where": "vec0valuex",
         "call": "absTime.seconds"},
        {"path": "/p/jitter", "where": "tx", "call": "random.random()"},
    ]
    nodes = [node("/p/a", 10)]
    result = check(sample(0, nodes),
                   sample(60, [node("/p/a", 70)], clockReads=reads,
                          clockReadsCount=3,
                          clockScan={"scanned": 1, "of": 1}))

    nondet = finding(result, "nondeterministic")
    assert nondet is not None and nondet.severity == "warning"
    assert nondet.paths == [
        "/project1/cement/cam_cb uses absTime.seconds (script text)",
        "/project1/cement/filmlook vec0valuex = absTime.seconds",
        "/p/jitter tx = random.random()",
    ]
    assert "me.time.seconds" in nondet.message
    assert "not reproduce" in nondet.message


def test_a_scan_that_ran_out_of_time_says_how_much_it_covered():
    nodes = [node("/p/a", 10)]
    result = check(sample(0, nodes),
                   sample(60, [node("/p/a", 70)], clockReads=[],
                          clockReadsCount=0,
                          clockScan={"scanned": 120, "of": 900}))
    assert finding(result, "nondeterministic") is None
    partial = finding(result, "nondeterminism-unscanned")
    assert partial is not None
    assert "120 of 900" in partial.message


# == the bridge half ==========================================================

class Par:
    def __init__(self, name, value=None, expr="", mode="ParMode.CONSTANT"):
        self.name = name
        self._value = value
        self.expr = expr
        self.mode = mode

    def eval(self):
        return self._value


class Pars:
    def __init__(self, pars):
        for p in pars:
            setattr(self, p.name, p)


class Op:
    def __init__(self, path, op_type="noiseTOP", family="TOP", pars=(),
                 children=None, **attrs):
        self.path = path
        self.name = path.rsplit("/", 1)[-1]
        self.OPType = op_type
        self.family = family
        self.valid = True
        self.inputs = []
        self.outputs = []
        self.children = children or []
        self.totalCooks = 1
        self.cookTime = 0.1
        self.bypass = False
        self._pars = list(pars)
        self.par = Pars(self._pars)
        for key, value in attrs.items():
            setattr(self, key, value)

    def pars(self, pattern="*"):
        return list(self._pars)

    def errors(self, recurse=False):
        return ""

    def warnings(self, recurse=False):
        return ""

    def scriptErrors(self, recurse=False):
        return ""


@pytest.fixture
def td_globals(monkeypatch):
    class Time:
        rate = 60.0
        play = True

    monkeypatch.setattr(handler, "me", type("Me", (), {"time": Time()})(),
                        raising=False)
    monkeypatch.setattr(handler, "absTime", type("T", (), {"frame": 500})(),
                        raising=False)
    monkeypatch.setattr(handler, "root", Op("/", "containerCOMP", "COMP"),
                        raising=False)
    monkeypatch.setattr(handler, "project",
                        type("P", (), {"realTime": True})(), raising=False)
    monkeypatch.setattr(handler, "app",
                        type("A", (), {"product": "TouchDesigner"})(),
                        raising=False)
    monkeypatch.setattr(handler, "licenses",
                        type("L", (), {"type": "Pro", "commercial": True})(),
                        raising=False)


def sampled(monkeypatch, *children):
    target = Op("/project1", "containerCOMP", "COMP", children=list(children))
    monkeypatch.setattr(handler, "_resolve", lambda path: target)
    return handler.m_health_sample({"path": "/project1"})


def entry(reply, path):
    return next(n for n in reply["nodes"] if n["path"] == path)


def test_the_bridge_sends_when_each_operator_last_cooked(monkeypatch, td_globals):
    reply = sampled(monkeypatch,
                    Op("/project1/mf", cookAbsFrame=353.0, cookFrame=353.0))
    assert entry(reply, "/project1/mf")["cookAbsFrame"] == 353.0
    assert entry(reply, "/project1/mf")["cookFrame"] == 353.0


def test_an_operator_without_the_new_attributes_is_still_sampled(
    monkeypatch, td_globals
):
    """Every new read has its own guard: a failure must not drop the node."""
    bare = Op("/project1/bare", "levelTOP")
    bare.par = object()
    reply = sampled(monkeypatch, bare)
    assert entry(reply, "/project1/bare")["cooks"] == 1


def test_the_bridge_sends_a_feedback_target(monkeypatch, td_globals):
    out = Op("/project1/out")
    fb = Op("/project1/fb", "feedbackTOP",
            pars=[Par("top", out), Par("reset", False)])
    reply = sampled(monkeypatch, fb, out)
    assert entry(reply, "/project1/fb")["feedback"] == {
        "target": "/project1/out", "reset": False}


def _level(path, fmt="rgba16float", black=0.42, clamp=False, low=0.0):
    return Op(path, "levelTOP", pixelFormatName=fmt,
              pars=[Par("blacklevel", black), Par("clamp", clamp),
                    Par("clamplow2", low)])


def test_the_bridge_names_an_add_below_a_level_in_float(monkeypatch, td_globals):
    lev = _level("/project1/ray_lev")
    null = Op("/project1/null1", "nullTOP")
    comp = Op("/project1/ray_add", "compositeTOP",
              pars=[Par("operand", "add")])
    lev.outputs = [null]
    null.outputs = [comp]
    reply = sampled(monkeypatch, lev, null, comp)
    risk = entry(reply, "/project1/ray_lev")["negativeFloat"]
    assert risk["format"] == "rgba16float"
    assert risk["blacklevel"] == 0.42
    assert risk["adds"] == ["/project1/ray_add"]


@pytest.mark.parametrize("level", [
    _level("/project1/l", fmt="rgba8fixed"),
    _level("/project1/l", fmt="rgba11float"),   # positive values only
    _level("/project1/l", black=0.0),
    _level("/project1/l", clamp=True, low=0.0),
])
def test_a_level_that_cannot_go_negative_is_not_flagged(
    monkeypatch, td_globals, level
):
    reply = sampled(monkeypatch, level)
    assert "negativeFloat" not in entry(reply, "/project1/l")


def test_the_bridge_finds_clock_reads_in_expressions_and_scripts(
    monkeypatch, td_globals
):
    expr = "ParMode.EXPRESSION"
    film = Op("/project1/filmlook", "glslTOP", pars=[
        Par("vec0valuex", 0.0, "absTime.seconds * 0.1", expr),
        Par("vec1valuex", 0.0, "me.time.seconds", expr),
        # A leftover expression in constant mode is not evaluated.
        Par("vec2valuex", 0.0, "absTime.frame", "ParMode.CONSTANT"),
    ])
    cam = Op("/project1/cam_cb", "executeDAT", "DAT",
             text="def onFrameStart(frame):\n    t = absTime.seconds\n")
    seeded = Op("/project1/seeded", "executeDAT", "DAT",
                text="import random\nrandom.seed(7)\nx = random.random()\n")
    unseeded = Op("/project1/jit", "chopexecuteDAT", "DAT",
                  text="import random, time\nx = random.uniform(0, 1)\n"
                       "y = time.time()\n")
    cb = Op("/project1/cb", "textDAT", "DAT", text="v = absTime.frame\n")
    script = Op("/project1/script1", "scriptCHOP", "CHOP",
                pars=[Par("callbacks", cb)])
    reply = sampled(monkeypatch, film, cam, seeded, unseeded, script, cb)

    found = {(r["path"], r["where"], r["call"]) for r in reply["clockReads"]}
    assert found == {
        ("/project1/filmlook", "vec0valuex", "absTime.seconds"),
        ("/project1/cam_cb", "text", "absTime.seconds"),
        ("/project1/jit", "text", "random.uniform()"),
        ("/project1/jit", "text", "time.time()"),
        ("/project1/cb", "callbacks of /project1/script1", "absTime.frame"),
    }
    assert reply["clockScan"]["scanned"] == reply["clockScan"]["of"]


def test_the_clock_scan_stops_at_its_budget_and_says_so(monkeypatch, td_globals):
    monkeypatch.setattr(handler, "_CLOCK_SCAN_BUDGET_S", 0.0)
    film = Op("/project1/filmlook", pars=[
        Par("tx", 0.0, "absTime.seconds", "ParMode.EXPRESSION")])
    reply = sampled(monkeypatch, film, Op("/project1/b"), Op("/project1/c"))
    assert reply["clockScan"]["scanned"] < reply["clockScan"]["of"]


def test_a_paused_timeline_does_not_split_cook_times_into_now_and_long_ago():
    """With the root timeline paused, absTime.frame stands still.

    Measured on 2025.32460: 2013719 -> 2013719 across one second with
    `root.time.play` False. Nothing cooks and the clock does not move, so a
    cook at the frozen frame is not "during this check" and one before it is
    not "long ago" by any count this check made. The time is listed with the
    frame it was measured on, and the split is not drawn.
    """
    nodes = [
        node("/project1/recent", 10, cookTime=30.0, cookAbsFrame=2013719.0),
        node("/project1/older", 10, cookTime=40.0, cookAbsFrame=2013000.0),
    ]
    result = check(sample(2013719, nodes, playing=False),
                   sample(2013719, nodes, playing=False))
    assert finding(result, "expensive-stale") is None
    expensive = finding(result, "expensive")
    assert expensive is not None and len(expensive.paths) == 2
    for line in expensive.paths:
        assert "during this check" not in line
    assert any("2013000" in line for line in expensive.paths)
    assert "clock did not advance" in expensive.message
