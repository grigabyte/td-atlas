"""td_record: a take with its sound, as a timeline job.

The TouchDesigner half is faked the way test_timeline_job fakes it: an env
with a queue of scheduled steps the test pumps. What only TouchDesigner could
answer was measured live on 2025.32460 (2026-09-29) and is in the handler's
comments: a record set in the request that built the recorder wrote no file,
Info CHOP's active_records falls to 0 at the length limit, and 120 of 120
frames with 88200 samples came out with realTime off.
"""

from __future__ import annotations

import pytest

from td_atlas.bridge import timeline as host_timeline
from td_atlas.component import handler


class Par:
    def __init__(self, value=None):
        self.value = value
        self.pulses = 0

    def eval(self):
        return self.value

    def pulse(self):
        self.pulses += 1


class Pars:
    def __init__(self, names):
        object.__setattr__(self, "_p", {n: Par() for n in names})

    def __getattr__(self, name):
        try:
            return self._p[name]
        except KeyError:
            raise AttributeError(name) from None

    def __setattr__(self, name, value):
        self._p.setdefault(name, Par()).value = value


class Conn:
    def connect(self, other):
        pass


class Chan:
    def __init__(self, env, name):
        self.env, self.name = env, name

    def eval(self):
        return self.env.info[self.name]


class Node:
    def __init__(self, env, path, family="TOP", optype="nullTOP", pars=()):
        self.env, self.path, self.family, self.OPType = env, path, family, optype
        self.par = Pars(pars)
        self.inputConnectors = [Conn()]
        self.children = []

    def __getitem__(self, name):
        return Chan(self.env, name)

    def findChildren(self):
        return self.children

    def destroy(self):
        self.env.ops.pop(self.path, None)
        self.env.destroyed.append(self.path)


class Time:
    def __init__(self):
        self.frame, self.play, self.start, self.end = 1, True, 1, 600
        self.rangeEnd, self.rate, self.path = 600, 60.0, "/local/time"


class Env:
    def __init__(self, licence="TouchDesigner Commercial"):
        self.time = Time()
        self.queue, self.state, self.destroyed = [], {}, []
        self.rt = True
        self.lic = licence
        self.files = set()
        self.info = {"total_frames_written": 0, "total_audio_samples_written": 0,
                     "total_frames_dropped": 0, "active_records": 0}
        self.ops = {}
        parent = Node(self, "/p", "COMP", "baseCOMP")
        self.add(parent)
        self.add(Node(self, "/p/out"))
        self.add(Node(self, "/p/osc", "CHOP", "audiooscillatorCHOP"))
        trig = Node(self, "/p/trig", "CHOP", "triggerCHOP", pars=("resetpulse",))
        self.add(trig)
        parent.children = [self.ops["/p/out"], self.ops["/p/osc"], trig]

    def add(self, node):
        self.ops[node.path] = node

    # the interface
    def op(self, path):
        return self.ops.get(path)

    def time_of(self, target):
        return self.time

    def schedule(self, fn, gen, seq, delay):
        self.queue.append((fn, gen, seq))

    def kill_pending(self):
        self.queue.clear()

    def registry(self):
        return self.state

    def now(self):
        return 0.0

    def realtime(self, value=None):
        if value is not None:
            self.rt = value
        return self.rt

    def licence(self):
        return self.lic

    def create(self, parent, optype, name):
        node = Node(self, f"{parent}/{name}", optype=optype)
        self.add(node)
        return node

    def exists(self, path):
        return path in self.files

    def pump(self):
        fn, gen, seq = self.queue.pop(0)
        fn(gen, seq)


@pytest.fixture
def env(monkeypatch):
    fake = Env()
    monkeypatch.setattr(handler, "_timeline_env", lambda: fake)
    monkeypatch.setattr(handler, "_resync_files", lambda: [])
    return fake


def _start(env, **over):
    params = {"path": "/p/out", "file": "/tmp/take.mov", "frames": 120,
              "audio": "/p/osc"}
    params.update(over)
    return handler.m_record(params)


def test_a_take_sets_up_resets_then_records_a_step_later(env):
    view = _start(env)
    rec = env.ops["/p/tdatlas_rec"]
    assert view["phase"] == "arm" and view["reset"] == ["/p/trig"]
    assert env.ops["/p/trig"].par.resetpulse.pulses == 1
    assert env.rt is False and env.time.play is False and env.time.frame == 1
    # Not in the request that built it (report 3).
    assert rec.par._p.get("record") is None
    env.pump()
    assert rec.par.record.value is True and env.time.play is True


def test_it_ends_when_the_recorder_goes_idle_and_puts_everything_back(env):
    _start(env)
    env.pump()
    env.info.update(total_frames_written=60, active_records=1,
                    total_audio_samples_written=44100)
    env.pump()
    assert env.state["job"]["state"] == "running"
    env.info.update(total_frames_written=120, active_records=0,
                    total_audio_samples_written=88200)
    env.pump()
    job = env.state["job"]
    assert job["state"] == "done" and job["written"] == 120
    assert job["audioSamples"] == 88200
    assert env.rt is True and env.time.play is True
    assert {"/p/tdatlas_rec", "/p/tdatlas_rec_keep", "/p/tdatlas_rec_info"} <= set(env.destroyed)
    text = "\n".join(host_timeline.describe(handler._timeline_view(job, env)))
    assert "120 of 120 frame(s) written" in text and "88200 samples" in text


def test_a_pause_mid_take_fails_it_with_the_count(env):
    _start(env)
    env.pump()
    env.info.update(total_frames_written=30, active_records=1)
    env.time.play = False
    env.pump()
    job = env.state["job"]
    assert job["state"] == "failed" and "30 of 120" in job["error"]


@pytest.mark.parametrize("over,words", [
    ({"file": "/tmp/take.mp4"}, ".mov"),
    ({"frames": 0}, "frames or seconds"),
    ({"frames": 700}, "playable range"),
    ({"audio": "/p/out"}, "must be a CHOP"),
    ({"codec": "vp9"}, "codec is one of"),
])
def test_refusals_touch_nothing(env, over, words):
    with pytest.raises(Exception) as caught:
        _start(env, **over)
    assert words in str(caught.value)
    assert "/p/tdatlas_rec" not in env.ops and env.rt is True


def test_h264_is_refused_on_non_commercial(env):
    env.lic = "TouchDesigner Non-Commercial"
    with pytest.raises(ValueError, match="Non-Commercial"):
        _start(env, codec="h264")


def test_an_existing_file_needs_overwrite(env):
    env.files.add("/tmp/take.mov")
    with pytest.raises(ValueError, match="already exists"):
        _start(env)
    assert _start(env, overwrite=True)["state"] == "running"


def test_seconds_become_frames_at_the_timeline_rate(env):
    assert _start(env, frames=0, seconds=2)["frames"] == 120
