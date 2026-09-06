"""What td_health does when its own reads fail, and how long it is allowed to sleep.

`scriptErrors` was read under a bare `except: pass` at three sites, so a build
where that read raises produced a report saying "Nothing wrong found" about the
one blind spot the tool exists to cover. And `interval` came straight from the
caller into `time.sleep` on the MCP server's own process.

No TouchDesigner needed: the sample's reads are monkeypatched to raise, and the
sleep is recorded rather than taken.
"""

from __future__ import annotations

import pytest

from td_atlas.bridge import health
from td_atlas.component import handler


class Exploding:
    """An operator whose script-error read fails, as a broken build's would."""

    def __init__(self, path, children=None, recursive_raises=True,
                 own_raises=True):
        self.path = path
        self.name = path.rsplit("/", 1)[-1]
        self.OPType = "containerCOMP"
        self.family = "COMP"
        self.valid = True
        self.inputs = []
        self.children = children or []
        self.totalCooks = 1
        self.cookTime = 0.1
        self.bypass = False
        self.par = object()
        self._recursive_raises = recursive_raises
        self._own_raises = own_raises

    def errors(self, recurse=False):
        return ""

    def warnings(self, recurse=False):
        return ""

    def scriptErrors(self, recurse=False):
        if recurse and self._recursive_raises:
            raise RuntimeError("scriptErrors is not available on this build")
        if not recurse and self._own_raises:
            raise RuntimeError("no scriptErrors on this operator")
        return "Error: Traceback (most recent call last): [%s]" % self.path


@pytest.fixture
def td_globals(monkeypatch):
    class Time:
        rate = 60.0
        play = True

    monkeypatch.setattr(handler, "me", type("Me", (), {"time": Time()})(),
                        raising=False)
    monkeypatch.setattr(handler, "absTime", type("T", (), {"frame": 1})(),
                        raising=False)
    monkeypatch.setattr(handler, "root", Exploding("/"), raising=False)
    monkeypatch.setattr(handler, "project",
                        type("P", (), {"realTime": True})(), raising=False)
    monkeypatch.setattr(handler, "app",
                        type("A", (), {"product": "TouchDesigner"})(),
                        raising=False)
    monkeypatch.setattr(handler, "licenses",
                        type("L", (), {"type": "Pro", "commercial": True})(),
                        raising=False)


def test_a_failed_recursive_read_is_named_rather_than_swallowed(
    monkeypatch, td_globals
):
    target = Exploding("/project1", [Exploding("/project1/a")])
    monkeypatch.setattr(handler, "_resolve", lambda path: target)

    sample = handler.m_health_sample({"path": "/project1"})

    assert sample["scriptErrorsUnreadCount"] == 1
    assert "recursive read" in sample["scriptErrorsUnread"][0]
    assert "/project1" in sample["scriptErrorsUnread"][0]
    assert "RuntimeError" in sample["scriptErrorsUnread"][0]


def test_a_failed_per_operator_read_is_named_too(monkeypatch, td_globals):
    child = Exploding("/project1/a")
    target = Exploding("/project1", [child], recursive_raises=False,
                       own_raises=False)
    monkeypatch.setattr(handler, "_resolve", lambda path: target)

    sample = handler.m_health_sample({"path": "/project1"})

    # The recursive read succeeded and was non-empty, so the walk runs; the
    # child's own read is the one that fails.
    assert sample["scriptErrorsUnreadCount"] == 1
    assert sample["scriptErrorsUnread"][0].startswith("/project1/a ")


def test_reads_that_all_succeed_leave_no_unread_marker(monkeypatch, td_globals):
    target = Exploding("/project1", [], recursive_raises=False,
                       own_raises=False)
    monkeypatch.setattr(handler, "_resolve", lambda path: target)

    sample = handler.m_health_sample({"path": "/project1"})

    assert "scriptErrorsUnread" not in sample
    assert "scriptErrorsUnreadCount" not in sample


def _sample(**extra):
    base = {
        "frame": 0,
        "fpsTarget": 60.0,
        "playing": True,
        "nodes": [{
            "path": "/project1/a", "type": "noiseTOP", "family": "TOP",
            "cooks": 1, "cookTime": 0.1, "bypass": False,
            "errors": None, "warnings": None,
        }],
        "license": {"type": "Pro"},
        "product": "TouchDesigner",
        "scriptErrors": {},
        "scriptErrorsRaw": "",
    }
    base.update(extra)
    return base


class SamplingClient:
    def __init__(self, first, second):
        self._replies = [first, second]

    def call(self, method, **params):
        if method == "health_sample":
            return self._replies.pop(0)
        raise AssertionError(method)


def _report(monkeypatch, second, **kwargs):
    slept = []
    monkeypatch.setattr(health.time, "sleep", slept.append)
    first = _sample(frame=0)
    client = SamplingClient(first, second)
    report = health.check(client, publish=False, **kwargs).render()
    return report, slept


def test_the_report_says_the_tracebacks_could_not_be_read(monkeypatch):
    report, _ = _report(
        monkeypatch,
        _sample(frame=60, scriptErrorsUnread=["/project1 (RuntimeError: no)"],
                scriptErrorsUnreadCount=3),
    )

    assert "Nothing wrong found" not in report
    assert "could not be read on 3 operator(s)" in report
    assert "unknown, not clean" in report
    assert "/project1 (RuntimeError: no)" in report


def test_a_clean_sample_still_reads_as_clean(monkeypatch):
    report, _ = _report(monkeypatch, _sample(frame=60))

    assert "could not be read" not in report


def test_a_long_interval_is_clamped_and_the_clamp_is_reported(monkeypatch):
    report, slept = _report(monkeypatch, _sample(frame=60), interval=600.0)

    assert slept == [health._MAX_INTERVAL]
    assert "changed to" in report and "600.0s" in report


def test_a_negative_interval_never_reaches_sleep(monkeypatch):
    report, slept = _report(monkeypatch, _sample(frame=60), interval=-5.0)

    assert slept == [0.0]
    assert "changed to 0.0s" in report


def test_an_ordinary_interval_is_left_alone(monkeypatch):
    report, slept = _report(monkeypatch, _sample(frame=60), interval=1.0)

    assert slept == [1.0]
    assert "changed to" not in report
