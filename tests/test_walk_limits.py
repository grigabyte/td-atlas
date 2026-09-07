"""Bounded walks on TouchDesigner's main thread, and the marker when one stops.

`m_errors` and `m_health_sample` used to hand `findChildren(depth=None)` the
whole project: the cost is paid before any cap could apply, and the reply looks
identical whether it covered everything or a corner. `m_capture` kept frames in
component storage with nothing to stop it. Each now has a ceiling and says when
it hit one — silence there means "nothing wrong under here", which is the one
thing a partial sweep cannot claim.

None of this needs TouchDesigner: the walk touches attributes a plain object
can carry, and the `td` globals the sample reads are monkeypatched in.
"""

from __future__ import annotations

import pytest

from td_atlas.bridge import filmstrip, health
from td_atlas.component import handler
from td_atlas.mcp import server


class FakeOp:
    def __init__(self, path, children=None, op_type="nullCHOP", family="CHOP",
                 errors="", warnings=""):
        self.path = path
        self.name = path.rsplit("/", 1)[-1]
        self.OPType = op_type
        self.family = family
        self.valid = True
        self.inputs = []
        self.children = children or []
        self.totalCooks = 1
        self.cookTime = 0.1
        self.bypass = False
        self.par = object()
        self._errors = errors
        self._warnings = warnings

    def errors(self, recurse=False):
        return self._errors

    def warnings(self, recurse=False):
        return self._warnings

    def scriptErrors(self, recurse=False):
        return ""


def _tree(count, parent="/project1"):
    """One parent with `count` leaf children."""
    return FakeOp(parent, [FakeOp(f"{parent}/n{i}") for i in range(count)],
                  "containerCOMP", "COMP")


# -- the walk itself --------------------------------------------------------

def test_the_walk_stops_at_its_limit_and_says_what_it_left(monkeypatch):
    found, unvisited = handler._bounded_descendants(_tree(50), 10)

    assert len(found) == 10
    assert unvisited == 40


def test_a_subtree_under_the_limit_comes_back_whole():
    found, unvisited = handler._bounded_descendants(_tree(9), 10)

    assert len(found) == 9
    assert unvisited == 0


def test_the_walk_descends_rather_than_only_listing_direct_children():
    grandchild = FakeOp("/project1/a/b")
    child = FakeOp("/project1/a", [grandchild], "containerCOMP", "COMP")
    found, _ = handler._bounded_descendants(
        FakeOp("/project1", [child], "containerCOMP", "COMP"), 10
    )

    assert [op.path for op in found] == ["/project1/a", "/project1/a/b"]


def test_something_with_no_children_at_all_is_not_an_error():
    assert handler._bounded_descendants(object(), 10) == ([], 0)


# -- m_errors ---------------------------------------------------------------

@pytest.fixture
def asked(monkeypatch):
    """Record the path `m_errors` resolves, and answer with a given subtree."""
    seen: list[str] = []

    def install(target):
        def resolve(path):
            seen.append(path)
            return target

        monkeypatch.setattr(handler, "_resolve", resolve)
        return seen

    return install


def test_errors_marks_a_sweep_that_did_not_reach_the_whole_project(
    monkeypatch, asked
):
    monkeypatch.setattr(handler, "_MAX_WALK_NODES", 4)
    asked(_tree(10))

    reply = handler.m_errors({})

    # Four descendants plus the component the walk was pointed at.
    assert reply["scanned"] == 5
    assert reply["truncated"] is True
    assert reply["notScanned"] == 6
    assert reply["limit"] == 4


def test_errors_over_a_small_project_carries_no_truncation_marker(
    monkeypatch, asked
):
    asked(_tree(3))

    reply = handler.m_errors({})

    assert reply["scanned"] == 4
    assert "truncated" not in reply


def test_errors_walks_the_project_by_default_and_not_the_whole_session(asked):
    """The defect this default repairs, measured live 2026-09-07.

    Breadth-first from `/` on a 32,063-operator session spent all 5000 nodes
    on TouchDesigner's own /ui and /sys — root lists them before /project1 —
    and a warning planted eight levels inside the project came back as
    "nothing is reporting errors".
    """
    seen = asked(_tree(3))

    handler.m_errors({})

    assert seen == ["/project1"]


def test_errors_walks_the_component_it_was_given(asked):
    seen = asked(_tree(3, parent="/project1/geo1"))

    reply = handler.m_errors({"path": "/project1/geo1"})

    assert seen == ["/project1/geo1"]
    assert reply["root"] == "/project1/geo1"


def test_the_component_asked_about_is_checked_and_not_only_its_children(asked):
    """From `/` it was covered as a child of root; from a named path it is not.

    A reply that says nothing about the very component it was pointed at is
    the partial-answer-read-as-whole failure this tool exists to prevent.
    """
    target = _tree(2)
    target._warnings = "Warning: File not found"
    asked(target)

    reply = handler.m_errors({})

    assert [node["path"] for node in reply["nodes"]] == ["/project1"]


class FakeClient:
    version_warning = None
    ambiguity_warning = None

    def __init__(self, reply):
        self.reply = reply
        self.asked: list[str] = []

    def errors(self, path="/project1"):
        self.asked.append(path)
        return self.reply


def _errors_text(monkeypatch, reply, path="/project1"):
    monkeypatch.setattr(server, "bridge", lambda: FakeClient(reply))
    return server.td_errors(path)


def test_a_clean_but_partial_sweep_does_not_read_as_a_clean_project(monkeypatch):
    text = _errors_text(
        monkeypatch,
        {"count": 0, "nodes": [], "root": "/", "scanned": 5000,
         "truncated": True, "notScanned": 120, "limit": 5000},
    )

    assert "No operators are reporting errors" in text
    assert "TRUNCATED" in text
    assert "5000" in text and "120" in text


def test_a_complete_sweep_says_nothing_about_limits(monkeypatch):
    text = _errors_text(
        monkeypatch, {"count": 0, "nodes": [], "root": "/project1", "scanned": 32}
    )

    assert "TRUNCATED" not in text


def test_a_clean_reply_names_the_subtree_it_covers(monkeypatch):
    """"Nothing is wrong" is a claim about one subtree, so it names it."""
    text = _errors_text(
        monkeypatch,
        {"count": 0, "nodes": [], "root": "/project1/geo1", "scanned": 7},
        path="/project1/geo1",
    )

    assert "/project1/geo1" in text
    assert "7" in text


def test_findings_name_the_subtree_too(monkeypatch):
    text = _errors_text(
        monkeypatch,
        {
            "count": 1,
            "root": "/project1",
            "scanned": 12,
            "nodes": [
                {"path": "/project1/movie", "type": "moviefileinTOP",
                 "errors": "Error: File not found", "warnings": None}
            ],
        },
    )

    assert "at and under /project1" in text
    assert "/project1/movie" in text


# A bridge that ignores `path` and walks from `/` used to be caught here, by
# the absent `root` key in its reply. It is now caught at connect by the
# version check — `path` is part of protocol 7 — and that refusal is held in
# `tests/test_bridge_client.py::test_a_bridge_one_version_below_the_minimum_is_refused`.


# -- m_health_sample --------------------------------------------------------

@pytest.fixture
def td_globals(monkeypatch):
    """The handful of `td` globals a health sample reads."""

    class Time:
        rate = 60.0
        play = True

    class Me:
        time = Time()

    monkeypatch.setattr(handler, "me", Me(), raising=False)
    monkeypatch.setattr(handler, "absTime", type("T", (), {"frame": 10})(),
                        raising=False)
    monkeypatch.setattr(handler, "root", FakeOp("/"), raising=False)
    monkeypatch.setattr(handler, "project",
                        type("P", (), {"realTime": True})(), raising=False)
    monkeypatch.setattr(handler, "app",
                        type("A", (), {"product": "TouchDesigner"})(),
                        raising=False)
    monkeypatch.setattr(handler, "licenses",
                        type("L", (), {"type": "Pro", "commercial": True})(),
                        raising=False)


def test_a_health_sample_that_stopped_early_says_so(monkeypatch, td_globals):
    monkeypatch.setattr(handler, "_MAX_WALK_NODES", 4)
    monkeypatch.setattr(handler, "_resolve", lambda path: _tree(10))

    sample = handler.m_health_sample({"path": "/project1"})

    assert len(sample["nodes"]) == 4
    assert sample["scanned"] == 4
    assert sample["truncated"] is True
    assert sample["notScanned"] == 6


def test_a_health_sample_of_a_small_project_is_not_marked(monkeypatch, td_globals):
    monkeypatch.setattr(handler, "_resolve", lambda path: _tree(3))

    sample = handler.m_health_sample({"path": "/project1"})

    assert sample["scanned"] == 3
    assert "truncated" not in sample


def test_a_health_sample_of_a_leaf_operator_is_refused_not_answered_empty(
    monkeypatch, td_globals
):
    """`children` is empty on a non-COMP, so a walk would call a TOP healthy."""
    monkeypatch.setattr(handler, "_resolve",
                        lambda path: FakeOp("/project1/noise1"))

    with pytest.raises(TypeError) as raised:
        handler.m_health_sample({"path": "/project1/noise1"})

    assert "/project1/noise1" in str(raised.value)
    assert "component" in str(raised.value)


def _sample(nodes, **extra):
    base = {
        "frame": 0,
        "fpsTarget": 60.0,
        "playing": True,
        "nodes": nodes,
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


def _node(path, cooks):
    return {
        "path": path, "type": "noiseTOP", "family": "TOP", "cooks": cooks,
        "cookTime": 0.1, "bypass": False, "errors": None, "warnings": None,
    }


def test_the_health_report_names_the_part_of_the_network_it_did_not_see(
    monkeypatch,
):
    monkeypatch.setattr(health.time, "sleep", lambda seconds: None)
    first = _sample([_node("/project1/a", 1)], frame=0)
    second = _sample(
        [_node("/project1/a", 61)], frame=60, truncated=True,
        notScanned=900, scanned=5000, limit=5000,
    )

    report = health.check(SamplingClient(first, second), publish=False).render()

    assert "walk-truncated" not in report  # the kind is internal
    assert "5000 operator(s)" in report
    assert "900 more were not looked at" in report


def test_an_untruncated_sample_produces_no_such_warning(monkeypatch):
    monkeypatch.setattr(health.time, "sleep", lambda seconds: None)
    first = _sample([_node("/project1/a", 1)], frame=0)
    second = _sample([_node("/project1/a", 61)], frame=60)

    report = health.check(SamplingClient(first, second), publish=False).render()

    assert "not looked at" not in report


# -- m_capture --------------------------------------------------------------

class FakeFrame:
    def __init__(self, nbytes):
        self.nbytes = nbytes


class Holder:
    def __init__(self):
        self.storage = {}

    def fetch(self, key, default=None):
        return self.storage.get(key, default)

    def store(self, key, value):
        self.storage[key] = value


class FakeTOP:
    family = "TOP"
    path = "/project1/out1"

    def __init__(self, size=1024):
        self.size = size
        self.cooks = 0

    def cook(self, force=False):
        self.cooks += 1

    def numpyArray(self):
        return FakeFrame(self.size)


@pytest.fixture
def capture_bench(monkeypatch):
    holder = Holder()
    top = FakeTOP()
    monkeypatch.setattr(handler, "_resolve", lambda path: top)
    monkeypatch.setattr(handler, "me",
                        type("Me", (), {"parent": lambda self: holder})(),
                        raising=False)
    monkeypatch.setattr(handler, "absTime", type("T", (), {"frame": 3})(),
                        raising=False)
    return holder, top


def test_the_frame_buffer_stops_at_its_byte_budget(monkeypatch, capture_bench):
    holder, top = capture_bench
    monkeypatch.setattr(handler, "_MAX_CAPTURE_BYTES", 3 * 1024)

    replies = [handler.m_capture({"path": top.path}) for _ in range(6)]

    assert [r["frames"] for r in replies] == [1, 2, 3, 3, 3, 3]
    assert replies[2]["captured"] is True
    assert replies[3]["captured"] is False
    assert replies[3]["full"] is True
    assert replies[3]["limit"] == 3 * 1024
    # A refused frame costs no cook either: the whole point is bounded work.
    assert top.cooks == 3


def test_the_frame_buffer_stops_at_its_frame_count(monkeypatch, capture_bench):
    holder, top = capture_bench
    monkeypatch.setattr(handler, "_MAX_CAPTURE_FRAMES", 2)

    replies = [handler.m_capture({"path": top.path}) for _ in range(4)]

    assert [r["frames"] for r in replies] == [1, 2, 2, 2]
    assert replies[-1]["full"] is True


def test_reset_empties_a_full_buffer(monkeypatch, capture_bench):
    holder, top = capture_bench
    monkeypatch.setattr(handler, "_MAX_CAPTURE_FRAMES", 2)

    for _ in range(3):
        handler.m_capture({"path": top.path})
    reply = handler.m_capture({"path": top.path, "reset": True})

    assert reply["frames"] == 1
    assert reply["captured"] is True


class CapturingClient:
    """Answers `full` once the buffer it pretends to hold is at `capacity`."""

    def __init__(self, capacity):
        self.capacity = capacity
        self.frames = 0
        self.sheets = 0

    def call(self, method, **params):
        if method == "capture":
            if params.get("reset"):
                self.frames = 0
            if self.frames >= self.capacity:
                return {"frames": self.frames, "full": True, "captured": False}
            self.frames += 1
            return {"frames": self.frames, "captured": True}
        if method == "contact_sheet":
            self.sheets += 1
            return {
                "count": self.frames, "columns": 1, "rows": self.frames,
                "width": 1, "height": self.frames,
                "data": "AAAA" * self.frames,
            }
        raise AssertionError(method)


def test_the_host_stops_sampling_when_the_buffer_is_full(monkeypatch, tmp_path):
    monkeypatch.setattr(filmstrip.time, "sleep", lambda seconds: None)
    client = CapturingClient(capacity=3)

    result = filmstrip.contact_sheet(
        client, "/project1/out1", tmp_path / "sheet.png", frames=9, columns=1
    )

    assert client.frames == 3
    assert "buffer filled after 3 of 9 frames" in result["truncated"]


def test_a_sheet_that_fitted_is_not_marked_truncated(monkeypatch, tmp_path):
    monkeypatch.setattr(filmstrip.time, "sleep", lambda seconds: None)
    client = CapturingClient(capacity=99)

    result = filmstrip.contact_sheet(
        client, "/project1/out1", tmp_path / "sheet.png", frames=4, columns=1
    )

    assert "truncated" not in result
