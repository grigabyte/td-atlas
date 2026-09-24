"""Walking the timeline frame by frame, as a job, without holding the thread.

Two agents in a row wrote the same chain by hand — set a frame, let it cook,
save the TOP, next frame — through `run(delayFrames=...)`, because one request
runs inside one cook and td_exec gives up after 30 s. Along the way they found
the traps the job now closes itself (agent report 2, point 3; report 1,
points 1 and 4):

- with the timeline paused, `run(delayFrames=N)` counts timeline frames and
  never fires unless `delayRef=op.TDResources`;
- a delayed call that did not fire wakes when the frame changes and runs as a
  second chain, so every step carries a generation and a stale one exits;
- live audio analysis repeats only when frames are walked from the first one
  consecutively, with at least two application frames between steps and the
  timeline paused;
- a Feedback TOP advances only on a real timeline frame, so quarters above the
  licence's 1280 cap are four whole passes, one per crop, never one pass with
  four crops per frame;
- the crop goes back to 0..1 on every way out, a failure included;
- nothing of the job's is left in a project's storage.

The TouchDesigner half is an `env` object the handler builds from its globals;
here it is a fake with a queue of scheduled steps that the test pumps, one
application frame's worth at a time. What only TouchDesigner can answer — that
the live env's `run()` fires under pause, that a paused frame change advances a
Feedback TOP — is listed in the report, not asserted here.
"""

from __future__ import annotations

import json

import pytest

from td_atlas.bridge import timeline as host_timeline
from td_atlas.bridge.client import BridgeError
from td_atlas.component import handler
from td_atlas.mcp import server

CROP = ("cropleft", "cropright", "cropbottom", "croptop")
FULL = (0.0, 1.0, 0.0, 1.0)


# -- the fake TouchDesigner ------------------------------------------------

class FakeTime:
    def __init__(self, frame=500, play=True, start=1, end=1000, clamp_to=None):
        self._frame = frame
        self.play = play
        self.start = start
        self.end = end
        self.rangeStart = start
        self.rangeEnd = end
        self.path = "/local/time"
        # Where TouchDesigner would stop a frame that is set past it.
        self.clamp_to = clamp_to
        self.history = []

    @property
    def frame(self):
        return self._frame

    @frame.setter
    def frame(self, value):
        if self.clamp_to is not None:
            value = min(value, self.clamp_to)
        self._frame = value
        self.history.append(value)


class Par:
    def __init__(self, value):
        self.value = value

    def eval(self):
        return self.value


class Pars:
    def __init__(self, **values):
        object.__setattr__(self, "_pars", {k: Par(v) for k, v in values.items()})

    def __getattr__(self, name):
        try:
            return self._pars[name]
        except KeyError:
            raise AttributeError(name) from None

    def __setattr__(self, name, value):
        if name not in self._pars:
            raise AttributeError(name)
        self._pars[name].value = value


class FakeTOP:
    family = "TOP"

    def __init__(self, path, env, op_type="nullTOP", fail_on_save=None):
        self.path = path
        self.OPType = op_type
        self.env = env
        self.saves = []
        self.fail_on_save = fail_on_save
        self.par = Pars(
            cropleft=0.0, cropright=1.0, cropbottom=0.0, croptop=1.0,
            cropleftunit="fraction", croprightunit="fraction",
            cropbottomunit="fraction", croptopunit="fraction",
        ) if op_type == "renderTOP" else Pars()

    def save(self, path, createFolders=False):
        if self.fail_on_save is not None and len(self.saves) == self.fail_on_save:
            raise OSError("disk full")
        render = self.env.render
        crop = tuple(getattr(render.par, n).eval() for n in CROP) if render else None
        self.saves.append({
            "path": path,
            "frame": self.env.time.frame,
            "play": self.env.time.play,
            "crop": crop,
        })


class FakeEnv:
    """The seam m_timeline_* and the scheduled step talk to."""

    def __init__(self, time=None, render=True, fail_on_save=None):
        self.time = time or FakeTime()
        self.clock = 1000.0
        self.queue = []
        self.killed = 0
        self.delays = []
        self.state = {}
        self.render = FakeTOP("/project1/render1", self, "renderTOP") if render else None
        self.top = FakeTOP("/project1/out", self, fail_on_save=fail_on_save)
        self.ops = {self.top.path: self.top}
        if self.render:
            self.ops[self.render.path] = self.render

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

    # the test's own hand on the clock
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


def _use(monkeypatch, fake):
    monkeypatch.setattr(handler, "_timeline_env", lambda: fake)
    return fake


def _run(**params):
    base = {"path": "/project1/out", "start": 1, "end": 10}
    base.update(params)
    return handler.m_timeline_run(base)


def _crop(fake):
    return tuple(getattr(fake.render.par, n).eval() for n in CROP)


# -- what gets saved, and when ----------------------------------------------

def test_saves_exactly_the_requested_frames_walking_every_frame_between(env):
    reply = _run(save=[[4, 5], [8, 8]], output="/tmp/seq/f{frame:04d}.png")
    assert reply["state"] == "running"
    assert reply["toSave"] == 3

    env.pump()

    # Walked from the first frame, every frame, and no further than the last
    # frame anything was wanted from.
    assert env.time.history == list(range(1, 9))
    assert [s["path"] for s in env.top.saves] == [
        "/tmp/seq/f0004.png", "/tmp/seq/f0005.png", "/tmp/seq/f0008.png",
    ]
    # Each file is the frame it is named after, taken after it had cooked.
    assert [s["frame"] for s in env.top.saves] == [4, 5, 8]
    # Every step waits the settle: two application frames by default.
    assert set(env.delays) == {2}

    status = handler.m_timeline_status({})
    assert status["state"] == "done"
    assert status["saved"] == 3 and status["toSave"] == 3


def test_without_from_start_the_walk_begins_at_the_first_saved_frame(env):
    _run(save=[[4, 6]], output="/tmp/f{frame}.png", from_start=False)
    env.pump()

    assert env.time.history == [4, 5, 6]


def test_the_settle_is_the_step_between_frames(env):
    _run(end=4, output="/tmp/f{frame}.png", settle=3)
    env.pump()

    assert set(env.delays) == {3}
    assert len(env.top.saves) == 4


def test_without_an_output_the_job_only_advances_the_timeline(env):
    """The warm-up of report 1: bring history up to a frame, save nothing."""
    reply = _run(start=1, end=40)
    assert reply["mode"] == "advance"
    env.pump()

    assert env.time.history == list(range(1, 41))
    assert env.top.saves == []
    assert handler.m_timeline_status({})["state"] == "done"


# -- the timeline's mode ------------------------------------------------------

def test_the_timeline_is_paused_for_the_walk_and_its_mode_given_back(env):
    assert env.time.play is True
    _run(end=5, output="/tmp/f{frame}.png")

    assert env.time.play is False
    env.pump()

    assert all(s["play"] is False for s in env.top.saves)
    assert env.time.play is True
    assert handler.m_timeline_status({})["restored"]["play"] is True


def test_an_advance_holds_the_timeline_at_its_last_frame(env):
    """Resuming play after a warm-up would move past the frame just reached."""
    _run(start=1, end=30)
    env.pump()

    assert env.time.play is False
    assert env.time.frame == 30


def test_a_timeline_started_by_hand_mid_walk_fails_the_job(env):
    """A playing timeline moves the audio between steps: not repeatable."""
    _run(end=10, output="/tmp/f{frame}.png")
    env.pump(steps=3)
    env.time.play = True
    env.pump()

    status = handler.m_timeline_status({})
    assert status["state"] == "failed"
    assert "play" in status["error"]


def test_a_frame_the_timeline_would_not_take_fails_the_job(monkeypatch):
    """TouchDesigner clamps a frame past the range; saving it would lie."""
    fake = _use(monkeypatch, FakeEnv(time=FakeTime(clamp_to=5)))
    _run(end=8, output="/tmp/f{frame}.png")
    fake.pump()

    status = handler.m_timeline_status({})
    assert status["state"] == "failed"
    assert "6" in status["error"]
    assert [s["frame"] for s in fake.top.saves] == [1, 2, 3, 4, 5]


# -- quarters ---------------------------------------------------------------

def test_quarters_are_four_whole_passes_each_with_its_own_crop(env):
    _run(end=3, output="/tmp/f{frame}_t{tile}.png", tiles=2,
         render="/project1/render1")
    env.pump()

    saves = env.top.saves
    assert [s["path"] for s in saves] == [
        f"/tmp/f{f}_t{t}.png" for t in range(4) for f in (1, 2, 3)
    ]
    # Each pass walks from its first frame again, so a Feedback TOP builds its
    # history under the crop it will be saved with.
    assert env.time.history == [1, 2, 3] * 4
    crops = [s["crop"] for s in saves[::3]]
    assert crops == [
        (0.0, 0.5, 0.5, 1.0),  # 0: top left
        (0.5, 1.0, 0.5, 1.0),  # 1: top right
        (0.0, 0.5, 0.0, 0.5),  # 2: bottom left
        (0.5, 1.0, 0.0, 0.5),  # 3: bottom right
    ]
    assert _crop(env) == FULL


def test_the_crop_comes_back_even_when_a_save_fails_mid_pass(monkeypatch):
    fake = _use(monkeypatch, FakeEnv(fail_on_save=4))
    _run(end=3, output="/tmp/f{frame}_t{tile}.png", tiles=2,
         render="/project1/render1")
    fake.pump()

    status = handler.m_timeline_status({})
    assert status["state"] == "failed"
    assert "disk full" in status["error"]
    assert status["saved"] == 4
    assert _crop(fake) == FULL
    assert fake.time.play is True
    assert fake.queue == []


def test_a_render_top_given_as_the_path_is_its_own_crop_target(env):
    env.ops["/project1/render1"] = env.render
    handler.m_timeline_run({
        "path": "/project1/render1", "start": 1, "end": 1,
        "output": "/tmp/f{frame}_t{tile}.png", "tiles": 2,
    })
    env.pump()

    assert len(env.render.saves) == 4
    assert _crop(env) == FULL


def test_quarters_without_a_render_top_are_refused_before_anything_moves(env):
    with pytest.raises(ValueError, match="render"):
        _run(end=3, output="/tmp/f{frame}_t{tile}.png", tiles=2)
    assert env.time.play is True
    assert env.time.history == []


def test_quarters_that_would_overwrite_each_other_are_refused(env):
    with pytest.raises(ValueError, match="tile"):
        _run(end=3, output="/tmp/f{frame}.png", tiles=2, render="/project1/render1")
    assert env.time.play is True


def test_frames_that_would_overwrite_each_other_are_refused(env):
    with pytest.raises(ValueError, match="frame"):
        _run(end=3, output="/tmp/still.png")


# -- generations ------------------------------------------------------------

def test_a_step_from_an_older_generation_does_nothing(env):
    _run(end=10, output="/tmp/a{frame}.png")
    env.pump(steps=2)
    stale = env.queue[0]

    handler.m_timeline_cancel({})
    _run(end=10, output="/tmp/b{frame}.png")
    frames_before = list(env.time.history)
    queued = list(env.queue)

    fn, gen, seq = stale
    fn(gen, seq)

    assert env.time.history == frames_before
    assert env.queue == queued
    assert not any(s["path"].startswith("/tmp/a") for s in env.top.saves[2:])
    env.pump()
    assert handler.m_timeline_status({})["state"] == "done"


def test_a_duplicate_step_within_one_job_does_nothing(env):
    """The second chain of report 2: a delayed call that wakes up late."""
    _run(end=10, output="/tmp/f{frame}.png")
    env.pump(steps=3)
    fn, gen, seq = env.queue[0]

    fn(gen, seq - 1)

    assert len(env.queue) == 1
    env.pump()
    assert [s["frame"] for s in env.top.saves] == list(range(1, 11))


def test_a_second_job_is_refused_while_one_runs(env):
    first = _run(end=10)
    with pytest.raises(RuntimeError, match=first["job"]):
        _run(end=10)


# -- cancel, and what is left behind --------------------------------------

def test_cancel_stops_the_walk_and_gives_everything_back(env):
    _run(end=10, output="/tmp/f{frame}_t{tile}.png", tiles=2,
         render="/project1/render1")
    env.pump(steps=4)

    reply = handler.m_timeline_cancel({})

    assert reply["state"] == "cancelled"
    assert env.queue == [] and env.killed == 1
    assert _crop(env) == FULL
    assert env.time.play is True
    saved = len(env.top.saves)
    env.pump()
    assert len(env.top.saves) == saved
    assert handler.m_timeline_status({})["state"] == "cancelled"


def test_the_job_keeps_nothing_that_cannot_be_written_down(env):
    """Its state is data: no function, no operator, nothing a .toe would choke on.

    It lives outside every operator's storage, so a project saved mid-walk
    carries none of it; what is held is plain enough to print.
    """
    _run(end=5, output="/tmp/f{frame}_t{tile}.png", tiles=2,
         render="/project1/render1")
    env.pump(steps=3)
    json.dumps(env.state)

    handler.m_timeline_cancel({})
    json.dumps(env.state)


def test_cancel_with_nothing_running_says_so(env):
    reply = handler.m_timeline_cancel({})
    assert reply["state"] == "none"


# -- the host ----------------------------------------------------------------

@pytest.mark.parametrize(
    "spec, expected",
    [
        ("1..994", [[1, 994]]),
        ("92..217,459..541", [[92, 217], [459, 541]]),
        (" 5 ", [[5, 5]]),
        ("10-12, 3", [[10, 12], [3, 3]]),
    ],
)
def test_frame_specs_read_as_ranges(spec, expected):
    assert host_timeline.parse_frames(spec) == expected


@pytest.mark.parametrize("spec", ["", "a..b", "9..3", "1..", "..4"])
def test_a_frame_spec_that_is_not_one_is_refused(spec):
    with pytest.raises(ValueError):
        host_timeline.parse_frames(spec)


class JobClient:
    ambiguity_warning = None
    version_warning = None

    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def call(self, method, **params):
        self.calls.append((method, params))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


RUNNING = {
    "job": "t3", "state": "running", "mode": "capture",
    "path": "/project1/out", "timeline": "/local/time",
    "walk": [1, 217], "frame": 57, "tile": 1, "tiles": 4,
    "saved": 130, "toSave": 504, "lastFile": "/tmp/f0056_t1.png",
    "settle": 2, "elapsed": 12.5, "sinceStep": 0.03, "stalled": False,
    "error": None, "errors": [], "notes": [], "restored": None,
}


def test_td_timeline_run_sends_the_ranges_and_names_the_job(monkeypatch):
    client = JobClient(dict(RUNNING, frame=1, tile=0, saved=0))
    monkeypatch.setattr(server, "bridge", lambda: client)

    text = server.td_timeline_run(
        "/project1/out", "1..994", output="/tmp/f{frame:04d}_t{tile}.png",
        save="92..217,459..541", tiles=2, render="/project1/render1",
    )

    method, params = client.calls[0]
    assert method == "timeline_run"
    assert params["start"] == 1 and params["end"] == 994
    assert params["save"] == [[92, 217], [459, 541]]
    assert params["tiles"] == 2
    assert "t3" in text
    assert "td_timeline_status" in text


def test_td_timeline_run_refuses_a_bad_spec_without_dialling(monkeypatch):
    def explode():
        raise AssertionError("dialled the bridge for a malformed spec")

    monkeypatch.setattr(server, "bridge", explode)
    text = server.td_timeline_run("/project1/out", "abc")
    assert text.startswith("error:")


def test_td_timeline_status_reports_progress(monkeypatch):
    monkeypatch.setattr(server, "bridge", lambda: JobClient(RUNNING))

    text = server.td_timeline_status()

    assert "t3" in text and "running" in text
    assert "saved 130 of 504" in text
    assert "frame 57" in text
    assert "pass 2 of 4" in text


def test_td_timeline_status_says_when_the_walk_has_stalled(monkeypatch):
    stalled = dict(RUNNING, stalled=True, sinceStep=14.0)
    monkeypatch.setattr(server, "bridge", lambda: JobClient(stalled))

    assert "stalled" in server.td_timeline_status()


def test_td_timeline_cancel_reports_what_was_given_back(monkeypatch):
    reply = dict(RUNNING, state="cancelled",
                 restored={"play": True, "crop": ["/project1/render1"]})
    monkeypatch.setattr(server, "bridge", lambda: JobClient(reply))

    text = server.td_timeline_cancel()

    assert "cancelled" in text
    assert "/project1/render1" in text


def test_a_refused_start_carries_a_hint(monkeypatch):
    refusal = BridgeError(
        {"type": "RuntimeError", "message": "job t2 is still running"},
        "timeline_run",
    )
    monkeypatch.setattr(server, "bridge", lambda: JobClient(refusal))

    text = server.td_timeline_run("/project1/out", "1..10")
    assert "t2" in text and "fix:" in text
