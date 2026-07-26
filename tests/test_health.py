"""Tests for the silent-failure detector. No TouchDesigner required."""

from __future__ import annotations

import pytest

from td_atlas.bridge import health as health_mod


class FakeClient:
    """Replays two prepared health samples."""

    def __init__(self, samples):
        self.samples = list(samples)

    def call(self, method, **_kw):
        assert method == "health_sample"
        return self.samples.pop(0)


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
        "rootCookTime": 0.1,
        "license": {"type": "TouchDesigner Commercial"},
        "product": "TouchDesigner",
        "nodes": nodes,
    }
    base.update(over)
    return base


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(health_mod.time, "sleep", lambda _s: None)


def kinds(result):
    return {f.kind for f in result.findings}


# -- the failure that costs the most time -----------------------------------

def test_reports_a_branch_that_never_cooks():
    before = sample(0, [node("/p/a", 100), node("/p/b", 100)])
    after = sample(60, [node("/p/a", 160), node("/p/b", 100)])
    result = health_mod.check(FakeClient([before, after]))
    assert "not-cooking" in kinds(result)
    finding = next(f for f in result.findings if f.kind == "not-cooking")
    assert finding.paths == ["/p/b"]
    assert not result.ok


def test_a_fully_live_network_is_clean():
    before = sample(0, [node("/p/a", 100), node("/p/b", 50)])
    after = sample(60, [node("/p/a", 160), node("/p/b", 110)])
    result = health_mod.check(FakeClient([before, after]))
    assert result.ok
    assert not kinds(result)


# -- conditions that only look like a dead network --------------------------

def test_a_paused_timeline_is_not_reported_as_dead():
    # Every node is frozen, but so is the clock: there is no evidence either
    # way, and claiming the network is dead would be a false alarm.
    nodes = [node("/p/a", 100), node("/p/b", 100)]
    result = health_mod.check(
        FakeClient([sample(0, nodes, playing=False),
                    sample(0, nodes, playing=False)])
    )
    assert "paused" in kinds(result)
    assert "not-cooking" not in kinds(result)
    assert result.ok


def test_background_throttling_is_not_reported_as_dead():
    nodes = [node("/p/a", 100)]
    result = health_mod.check(
        FakeClient([sample(0, nodes), sample(2, nodes)])
    )
    assert "stalled" in kinds(result)
    assert "not-cooking" not in kinds(result)


# -- silent output failures -------------------------------------------------

def test_reports_an_output_device_switched_off():
    nodes = [node("/p/aout", 100, type="audiodeviceoutCHOP",
                  family="CHOP", active=False)]
    after = [node("/p/aout", 160, type="audiodeviceoutCHOP",
                  family="CHOP", active=False)]
    result = health_mod.check(FakeClient([sample(0, nodes), sample(60, after)]))
    assert "output-off" in kinds(result)
    assert not result.ok


def test_an_active_output_is_not_flagged():
    nodes = [node("/p/aout", 100, type="audiodeviceoutCHOP",
                  family="CHOP", active=True)]
    after = [node("/p/aout", 160, type="audiodeviceoutCHOP",
                  family="CHOP", active=True)]
    result = health_mod.check(FakeClient([sample(0, nodes), sample(60, after)]))
    assert "output-off" not in kinds(result)


# -- licence ----------------------------------------------------------------

def test_a_silently_clamped_resolution_is_an_error():
    # TouchDesigner calls this a warning, but it changes the deliverable.
    warn = "Warning: Resolution limited to max 1280x1280 with Non-Commercial key."
    before = [node("/p/a", 100, warnings=warn)]
    after = [node("/p/a", 160, warnings=warn)]
    result = health_mod.check(
        FakeClient([
            sample(0, before, license={"type": "TouchDesigner Non-Commercial"}),
            sample(60, after, license={"type": "TouchDesigner Non-Commercial"}),
        ])
    )
    assert "resolution-clamped" in kinds(result)
    assert not result.ok


def test_non_commercial_limits_are_stated():
    nodes = [node("/p/a", 100)]
    result = health_mod.check(
        FakeClient([
            sample(0, nodes, license={"type": "TouchDesigner Non-Commercial"}),
            sample(60, [node("/p/a", 160)],
                   license={"type": "TouchDesigner Non-Commercial"}),
        ])
    )
    licence = next(f for f in result.findings if f.kind == "licence")
    assert "1280x1280" in licence.message
    assert result.ok  # a note, not a failure


# -- performance ------------------------------------------------------------

def test_reports_an_expensive_operator():
    before = [node("/p/slow", 100, cookTime=430.0)]
    after = [node("/p/slow", 160, cookTime=430.0)]
    result = health_mod.check(FakeClient([sample(0, before), sample(60, after)]))
    finding = next(f for f in result.findings if f.kind == "expensive")
    assert "430 ms" in finding.paths[0]


def test_reports_a_collapsed_frame_rate():
    before = [node("/p/a", 100)]
    after = [node("/p/a", 110)]
    result = health_mod.check(
        FakeClient([sample(0, before), sample(10, after)])
    )
    assert "slow" in kinds(result)


def test_containers_are_not_expected_to_cook_every_frame():
    before = sample(0, [node("/p/box", 5, family="COMP", type="baseCOMP")])
    after = sample(60, [node("/p/box", 5, family="COMP", type="baseCOMP")])
    result = health_mod.check(FakeClient([before, after]))
    assert "not-cooking" not in kinds(result)
