"""Profiling a frame: what each operator costs, measured on real timeline frames.

An agent wrote this by hand in live work (agent report 3, point 3): set the
frame, `cook(force=True)`, time it. Its first pass said the whole chain cost
0.7 ms where it cost 81, because a TOP's cook only queues work on the GPU and
returns; the time is paid when something reads the texture back, and it took
`sample()` after each cook to see it. `health` showed the other half of the
trap: a `cookTime` of 44 ms on an operator that had not cooked for 374,144
frames.

So a profile job walks the timeline the way a timeline job does (paused, a
step per scheduled call, generations, nothing in a project's storage), and at
each frame it forces every TOP/CHOP/SOP/POP under the path, upstream first,
synchronising a TOP with `sample()` and a POP with `numPoints()`, and marks the
operators that did not cook on their own on the frames walked.

The fake below makes the trap concrete: a TOP's `cook` costs only its CPU part
on the clock, and the GPU part lands on the clock only when something waits for
it. A profiler that forgets to wait reads the GPU cost as zero.
"""

from __future__ import annotations

import json

import pytest

from td_atlas.bridge import timeline as host_timeline
from td_atlas.component import handler
from td_atlas.mcp import server


class FakeTime:
    def __init__(self, env, frame=500, play=True, start=1, end=1000):
        self.env = env
        self._frame = frame
        self.play = play
        self.start = start
        self.end = end
        self.rangeStart = start
        self.rangeEnd = end
        self.path = "/local/time"
        self.history = []

    @property
    def frame(self):
        return self._frame

    @frame.setter
    def frame(self, value):
        self._frame = value
        self.history.append(value)
        # The application frames of the settle: whatever is time-dependent and
        # pulled by something cooks on its own, before the step runs.
        self.env.natural_cook()


class FakeNode:
    def __init__(self, env, path, family, cpu=0.0, gpu=0.0, live=True,
                 inputs=(), op_type=None, pulls=(), fail_sync=False):
        self.env = env
        self.path = path
        self.name = path.rsplit("/", 1)[-1]
        self.family = family
        self.OPType = op_type or ("null" + family)
        self.cpu = cpu
        self.gpu = gpu
        self.live = live
        self.inputs = list(inputs)
        self.pulls = list(pulls)
        self.fail_sync = fail_sync
        self.children = []
        self.totalCooks = 0
        self.cpuCookTime = 0.0
        self.gpuCookTime = 0.0
        self.time = env.time

    def cost(self, part):
        value = getattr(self, part)
        return value(self.env.time.frame) if callable(value) else value

    def cook(self, force=False, recurse=False, includeUtility=False):
        for other in self.pulls:
            other.cook(force=True)
        cpu, gpu = self.cost("cpu"), self.cost("gpu")
        self.env.ms += cpu
        self.env.gpu_queue += gpu
        self.totalCooks += 1
        self.cpuCookTime = cpu
        self.gpuCookTime = gpu
        self.env.forced.append(self.path)

    def _wait_for_gpu(self):
        if self.fail_sync:
            raise RuntimeError("TOP has no resolution")
        self.env.ms += self.env.gpu_queue
        self.env.gpu_queue = 0.0
        self.env.synced.append(self.path)

    # TOP
    def sample(self, x=None, y=None, z=None, u=None, v=None, w=None):
        self._wait_for_gpu()
        return (0.0, 0.0, 0.0, 1.0)

    # POP
    def numPoints(self, delayed=False, max=False):
        self._wait_for_gpu()
        return 1


class FakeEnv:
    def __init__(self):
        self.time = FakeTime(self)
        self.clock = 1000.0
        self.ms = 0.0
        self.gpu_queue = 0.0
        self.queue = []
        self.killed = 0
        self.delays = []
        self.state = {}
        self.forced = []
        self.synced = []
        self.ops = {}
        self.root = self.add("/project1", "COMP")

    def add(self, path, family, parent=None, **kwargs):
        node = FakeNode(self, path, family, **kwargs)
        self.ops[path] = node
        if parent is not None:
            parent.children.append(node)
        return node

    def natural_cook(self):
        for node in self.ops.values():
            if node.live and node.family != "COMP":
                node.totalCooks += 1

    # the interface
    def op(self, path):
        return self.ops.get(path)

    def time_of(self, target):
        return self.time

    def schedule(self, fn, gen, seq, delay):
        self.queue.append((fn, gen, seq))
        self.delays.append(delay)

    def kill_pending(self):
        self.killed += len(self.queue)
        self.queue.clear()

    def registry(self):
        return self.state

    def now(self):
        return self.clock

    def perf(self):
        return self.ms

    def pump(self, steps=10_000):
        ran = 0
        while self.queue and ran < steps:
            fn, gen, seq = self.queue.pop(0)
            self.clock += 1 / 30
            fn(gen, seq)
            ran += 1
        return ran


@pytest.fixture
def env(monkeypatch):
    fake = FakeEnv()
    monkeypatch.setattr(handler, "_timeline_env", lambda: fake)
    return fake


def _chain(env):
    """noise (CHOP) and a GPU-heavy blur fed by a render, under /project1."""
    root = env.root
    lfo = env.add("/project1/lfo1", "CHOP", root, cpu=0.2)
    render = env.add("/project1/render1", "TOP", root, cpu=0.5, gpu=6.0)
    blur = env.add("/project1/blur1", "TOP", root, cpu=0.1, gpu=74.0,
                   inputs=[render])
    return lfo, render, blur


def _profile(**params):
    base = {"path": "/project1", "start": 1, "end": 5}
    base.update(params)
    return handler.m_timeline_profile(base)


def _rows(reply=None):
    reply = reply or handler.m_timeline_status({})
    return {row["path"]: row for row in reply["profile"]["rows"]}


# -- the walk ------------------------------------------------------------------

def test_the_profile_walks_every_frame_and_measures_each_one(env):
    _chain(env)
    reply = _profile(start=10, end=14)
    assert reply["mode"] == "profile"
    assert reply["state"] == "running"

    env.pump()

    assert env.time.history == [10, 11, 12, 13, 14]
    status = handler.m_timeline_status({})
    assert status["state"] == "done"
    assert status["profile"]["measured"] == 5
    assert all(row["frames"] == 5 for row in status["profile"]["rows"])
    assert set(env.delays) == {2}


def test_a_top_is_waited_for_on_the_gpu_and_a_chop_is_not(env):
    """The trap of report 3: without the wait a TOP costs its CPU part only."""
    lfo, render, blur = _chain(env)
    _profile()
    env.pump()

    rows = _rows()
    assert rows["/project1/blur1"]["mean"] == pytest.approx(74.1)
    assert rows["/project1/render1"]["mean"] == pytest.approx(6.5)
    assert rows["/project1/lfo1"]["mean"] == pytest.approx(0.2)
    assert rows["/project1/blur1"]["sync"] == "sample"
    assert rows["/project1/lfo1"]["sync"] is None
    assert env.synced.count("/project1/blur1") == 5
    assert "/project1/lfo1" not in env.synced


def test_a_pop_is_waited_for_through_its_point_count(env):
    env.add("/project1/pop1", "POP", env.root, cpu=0.3, gpu=9.0)
    _profile(end=2)
    env.pump()

    row = _rows()["/project1/pop1"]
    assert row["sync"] == "numPoints"
    assert row["mean"] == pytest.approx(9.3)


def test_mean_and_max_are_per_frame_and_the_table_is_sorted_by_cost(env):
    root = env.root
    env.add("/project1/spiky", "CHOP", root, cpu=lambda f: 10.0 if f == 3 else 1.0)
    env.add("/project1/steady", "CHOP", root, cpu=4.0)
    env.add("/project1/cheap", "SOP", root, cpu=0.5)
    _profile(start=1, end=5)
    env.pump()

    rows = handler.m_timeline_status({})["profile"]["rows"]
    assert [row["path"] for row in rows] == [
        "/project1/steady", "/project1/spiky", "/project1/cheap",
    ]
    spiky = rows[1]
    assert spiky["mean"] == pytest.approx(2.8)
    assert spiky["max"] == pytest.approx(10.0)
    assert rows[0]["max"] == pytest.approx(4.0)
    # TouchDesigner's own figures for the forced cook ride along.
    assert rows[0]["cpu"] == pytest.approx(4.0)


def test_an_operator_that_did_not_cook_on_its_own_is_marked(env):
    """The 44 ms of report 3: a cost from a cook that is not in the frame."""
    root = env.root
    env.add("/project1/moviefilein1", "TOP", root, cpu=44.0, live=False)
    env.add("/project1/noise1", "TOP", root, cpu=1.0)
    _profile(end=4)
    env.pump()

    rows = _rows()
    assert rows["/project1/moviefilein1"]["cooked"] == 0
    assert rows["/project1/noise1"]["cooked"] == 4
    # It is still measured — what it would cost — but the mark says it is not
    # part of the frame.
    assert rows["/project1/moviefilein1"]["mean"] == pytest.approx(44.0)


def test_upstream_is_measured_before_downstream_whatever_the_listing_order(env):
    """Forcing a node with a dirty input cooks the input inside its time."""
    root = env.root
    a = FakeNode(env, "/project1/a", "TOP", cpu=1.0)
    b = FakeNode(env, "/project1/b", "TOP", cpu=1.0, inputs=[a])
    c = FakeNode(env, "/project1/c", "CHOP", cpu=1.0)
    for node in (b, a, c):
        env.ops[node.path] = node
        root.children.append(node)
    _profile(end=1)
    env.pump()

    # Wires first; with no wire between them, CHOPs before TOPs, since a TOP
    # more often reads a CHOP through a parameter than the other way round.
    assert env.forced == ["/project1/c", "/project1/a", "/project1/b"]


def test_an_operator_cooked_inside_an_earlier_measurement_is_marked(env):
    """A parameter reference the order cannot see: its cost is counted twice."""
    root = env.root
    sop = FakeNode(env, "/project1/geo_sop", "TOP", cpu=5.0)
    render = FakeNode(env, "/project1/render", "TOP", cpu=1.0, pulls=[sop])
    for node in (render, sop):
        env.ops[node.path] = node
        root.children.append(node)
    _profile(end=3)
    env.pump()

    rows = _rows()
    assert rows["/project1/geo_sop"]["pulled"] == 3
    assert rows["/project1/render"]["pulled"] == 0


def test_only_tops_chops_sops_and_pops_are_measured_below_nested_comps(env):
    root = env.root
    geo = env.add("/project1/geo1", "COMP", root)
    env.add("/project1/geo1/box1", "SOP", geo, cpu=0.4)
    env.add("/project1/text1", "DAT", root, cpu=3.0)
    env.add("/project1/mat1", "MAT", root, cpu=3.0)
    _profile(end=2)
    env.pump()

    assert set(_rows()) == {"/project1/geo1/box1"}


def test_the_node_ceiling_is_kept_and_said(env):
    for index in range(6):
        env.add(f"/project1/n{index}", "CHOP", env.root, cpu=1.0)
    reply = _profile(end=2, limit=4)

    assert len(reply["profile"]["rows"]) == 4
    assert reply["profile"]["limit"] == 4
    assert reply["profile"]["truncated"] >= 2
    assert any("4" in note and "limit" in note for note in reply["notes"])


def test_a_failed_wait_counts_as_an_error_not_a_zero(env):
    env.add("/project1/empty", "TOP", env.root, cpu=0.1, gpu=3.0, fail_sync=True)
    env.add("/project1/ok", "CHOP", env.root, cpu=1.0)
    _profile(end=3)
    env.pump()

    status = handler.m_timeline_status({})
    assert status["state"] == "done"
    rows = _rows(status)
    assert rows["/project1/empty"]["errors"] == 3
    assert rows["/project1/empty"]["mean"] is None
    assert "resolution" in rows["/project1/empty"]["error"]
    assert rows["/project1/ok"]["frames"] == 3
    # Unmeasured rows go last, after every measured one.
    assert status["profile"]["rows"][-1]["path"] == "/project1/empty"


# -- the job's rules ------------------------------------------------------------

def test_the_play_mode_is_given_back_when_the_profile_ends(env):
    _chain(env)
    assert env.time.play is True
    _profile(end=3)
    assert env.time.play is False
    env.pump()

    assert env.time.play is True
    assert handler.m_timeline_status({})["restored"]["play"] is True


def test_cancel_stops_the_profile_and_keeps_what_was_measured(env):
    _chain(env)
    _profile(end=10)
    env.pump(steps=3)

    reply = handler.m_timeline_cancel({})

    assert reply["state"] == "cancelled"
    assert env.queue == []
    assert env.time.play is True
    assert reply["profile"]["measured"] == 3
    forced = len(env.forced)
    env.pump()
    assert len(env.forced) == forced


def test_a_stale_step_does_nothing(env):
    _chain(env)
    _profile(end=10)
    env.pump(steps=2)
    fn, gen, seq = env.queue[0]
    forced = len(env.forced)

    fn(gen, seq - 1)

    assert len(env.forced) == forced
    assert len(env.queue) == 1


def test_a_profile_and_a_walk_share_one_slot(env):
    """Both move one timeline, so one refuses the other."""
    _chain(env)
    env.ops["/project1/out"] = env.ops["/project1/blur1"]
    handler.m_timeline_run({"path": "/project1/out", "start": 1, "end": 10})
    with pytest.raises(RuntimeError, match="t1"):
        _profile()
    handler.m_timeline_cancel({})
    reply = _profile()
    assert reply["job"] == "t2"


def test_the_profile_keeps_nothing_that_cannot_be_written_down(env):
    _chain(env)
    _profile(end=5)
    env.pump(steps=2)
    json.dumps(env.state)
    handler.m_timeline_cancel({})
    json.dumps(env.state)


@pytest.mark.parametrize("params, match", [
    ({"path": "/nowhere"}, "no operator"),
    ({"path": "/"}, "project"),
    ({"start": 9, "end": 3}, "backwards"),
    ({"limit": 0}, "limit"),
    ({"start": 1, "end": 2000}, "range"),
])
def test_refusals_touch_nothing(env, params, match):
    _chain(env)
    env.ops["/"] = FakeNode(env, "/", "COMP")
    with pytest.raises((LookupError, ValueError), match=match):
        _profile(**params)
    assert env.time.play is True
    assert env.time.history == []
    assert env.state.get("job") is None


# -- the host --------------------------------------------------------------------

class JobClient:
    ambiguity_warning = None
    version_warning = None

    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def call(self, method, **params):
        self.calls.append((method, params))
        return self.reply


def _row(path, mean, peak, cooked=5, frames=5, **extra):
    row = {
        "path": path, "type": "blurTOP", "family": "TOP", "frames": frames,
        "mean": mean, "max": peak, "cooked": cooked, "pulled": 0, "refused": 0,
        "errors": 0, "error": None, "cpu": None, "gpu": None, "sync": "sample",
    }
    row.update(extra)
    return row


DONE = {
    "job": "t4", "state": "done", "mode": "profile",
    "path": "/project1/cement", "timeline": "/local/time",
    "walk": [3000, 3009], "frame": 3009, "tile": None, "tiles": 1,
    "saved": 0, "toSave": 0, "lastFile": None, "output": "",
    "settle": 2, "elapsed": 4.2, "sinceStep": 0.03, "stalled": False,
    "error": None, "errors": [], "notes": [],
    "restored": {"play": True, "crop": []},
    "profile": {
        "measured": 10, "limit": 100, "truncated": 0, "nodes": 3,
        "rows": [
            _row("/project1/cement/ray_blur", 74.012, 80.5),
            _row("/project1/moviefilein1", 44.0, 44.0, cooked=0),
            _row("/project1/cement/grade", 0.4, 0.9, sync=None, family="CHOP"),
        ],
    },
}


def test_td_timeline_profile_sends_the_walk_and_names_the_job(monkeypatch):
    client = JobClient(dict(DONE, state="running", frame=3000))
    monkeypatch.setattr(server, "bridge", lambda: client)

    text = server.td_timeline_profile("/project1/cement", "3000..3009", limit=50)

    method, params = client.calls[0]
    assert method == "timeline_profile"
    assert params == {
        "path": "/project1/cement", "start": 3000, "end": 3009,
        "limit": 50, "settle": 2,
    }
    assert "t4" in text and "td_timeline_status" in text


def test_td_timeline_profile_refuses_a_bad_spec_without_dialling(monkeypatch):
    def explode():
        raise AssertionError("dialled the bridge for a malformed spec")

    monkeypatch.setattr(server, "bridge", explode)
    assert server.td_timeline_profile("/project1", "x").startswith("error:")
    assert server.td_timeline_profile("/project1", "1..3,5..6").startswith("error:")


def test_the_table_reads_costliest_first_and_marks_the_idle(monkeypatch):
    monkeypatch.setattr(server, "bridge", lambda: JobClient(DONE))

    text = server.td_timeline_status()
    lines = text.splitlines()

    blur = next(i for i, line in enumerate(lines) if "ray_blur" in line)
    movie = next(i for i, line in enumerate(lines) if "moviefilein1" in line)
    assert blur < movie
    assert "74.01" in lines[blur] and "80.50" in lines[blur]
    assert "did not cook" in lines[movie]
    assert "did not cook" not in lines[blur]
    assert "measured 10 of 10 frames" in text
    # The sum counts only what cooked on its own: 74.012 + 0.4.
    assert "74.41" in text


def test_describe_of_a_walk_is_unchanged_by_the_profile_branch():
    lines = host_timeline.describe({
        "job": "t1", "state": "running", "mode": "advance",
        "path": "/project1/out", "timeline": "/local/time", "walk": [1, 9],
        "frame": 3, "tiles": 1, "tile": None, "settle": 2, "elapsed": 0.2,
        "stalled": False, "notes": [], "errors": [], "restored": None,
    })
    assert lines[0] == "job t1: running — advancing /project1/out"
