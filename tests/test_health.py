"""Tests for the silent-failure detector. No TouchDesigner required."""

from __future__ import annotations

import time

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


# -- a shader that did not compile ------------------------------------------

# Both logs are what build 2025.32460 actually returned from compileResult,
# for a broken and for a working pixel shader on a glslTOP.
FAILED_COMPILE = (
    "Vertex Shader Compile Results:\n\nCompiled Successfully\n\n"
    "=============\nPixel Shader Compile Results:\n"
    "ERROR: /project1/probe/frag1:2: 'notAThing' : undeclared identifier \n"
    "ERROR: 1 compilation errors.  No code generated.\n"
)
GOOD_COMPILE = (
    "Vertex Shader Compile Results:\n\nCompiled Successfully\n\n"
    "=============\nPixel Shader Compile Results:\n\nCompiled Successfully\n"
)
# What TouchDesigner itself says about the same failure, and all it says.
COMPILE_WARNING = (
    "Warning: The GLSL Shader has compile errors (Use Info DAT to see "
    "details). (/p/shader)"
)


def test_reports_a_shader_that_did_not_compile():
    before = [node("/p/shader", 100, type="glslTOP",
                   compileResult=FAILED_COMPILE, warnings=COMPILE_WARNING)]
    after = [node("/p/shader", 160, type="glslTOP",
                  compileResult=FAILED_COMPILE, warnings=COMPILE_WARNING)]
    result = health_mod.check(FakeClient([sample(0, before), sample(60, after)]))
    finding = next(f for f in result.findings if f.kind == "shader-compile")
    # The compiler's line, with the source DAT and the line number, is what
    # TouchDesigner's own warning withholds.
    assert finding.paths == [
        "/p/shader (ERROR: /project1/probe/frag1:2: 'notAThing' : "
        "undeclared identifier)"
    ]
    assert not result.ok


def test_a_successful_compile_log_is_not_a_failure():
    # A shader that built also returns a non-empty compileResult, so the
    # presence of text cannot be the verdict.
    before = [node("/p/shader", 100, type="glslTOP",
                   compileResult=GOOD_COMPILE)]
    after = [node("/p/shader", 160, type="glslTOP",
                  compileResult=GOOD_COMPILE)]
    result = health_mod.check(FakeClient([sample(0, before), sample(60, after)]))
    assert "shader-compile" not in kinds(result)
    assert result.ok


def test_an_empty_compile_result_is_not_a_failure():
    # glslPOP has no compileResult at all; the bridge sends nothing and the
    # host must stay quiet rather than guess.
    before = [node("/p/shader", 100, type="glslPOP", compileResult="")]
    after = [node("/p/shader", 160, type="glslPOP", compileResult="")]
    result = health_mod.check(FakeClient([sample(0, before), sample(60, after)]))
    assert "shader-compile" not in kinds(result)
    assert result.ok


# -- a traceback from a script or a callback --------------------------------

def test_reports_a_traceback_from_a_callback():
    # The shape build 2025.32460 returned for an Execute DAT whose
    # onFrameStart raised: a wrapper line that itself ends in 'Error', the
    # frame lines, the exception, then the operator path in brackets — and
    # errors() empty throughout.
    trace = (
        "  Error: Traceback (most recent call last):\n"
        '  File "/p/exec1", line 2, in onFrameStart\n'
        "ValueError: nothing here\n"
        " (/p/exec1)\n"
        "Warning: Traceback (most recent call last):\n"
    )
    errors = {"/p/exec1": trace}
    before = sample(0, [node("/p/exec1", 100, type="executeDAT",
                             family="DAT")], scriptErrors=errors,
                    scriptErrorsRaw=trace)
    after = sample(60, [node("/p/exec1", 160, type="executeDAT",
                             family="DAT")], scriptErrors=errors,
                   scriptErrorsRaw=trace)
    result = health_mod.check(FakeClient([before, after]))
    finding = next(f for f in result.findings if f.kind == "script-errors")
    assert finding.paths == ["/p/exec1 (ValueError: nothing here)"]
    assert not result.ok


def test_a_traceback_on_a_comp_is_still_reported():
    # Replicator and extension errors live on COMPs, which the cooking checks
    # deliberately skip — the finding must not be skipped with them.
    trace = "Traceback (most recent call last):\nNameError: name 'foo'"
    errors = {"/p/rep": trace}
    nodes = [node("/p/rep", 5, family="COMP", type="replicatorCOMP")]
    result = health_mod.check(
        FakeClient([sample(0, nodes, scriptErrors=errors),
                    sample(60, nodes, scriptErrors=errors)])
    )
    assert "script-errors" in kinds(result)


def test_an_unattributed_traceback_is_still_reported():
    # If the per-operator read comes back empty while the recursive one did
    # not, the error is reported without a path rather than dropped.
    raw = "Traceback (most recent call last):\nZeroDivisionError: division"
    nodes = [node("/p/a", 100)]
    result = health_mod.check(
        FakeClient([sample(0, nodes, scriptErrors={}, scriptErrorsRaw=raw),
                    sample(60, [node("/p/a", 160)], scriptErrors={},
                           scriptErrorsRaw=raw)])
    )
    finding = next(f for f in result.findings if f.kind == "script-errors")
    assert finding.paths == []
    assert "ZeroDivisionError" in finding.message


# -- version contract: a bridge that predates these fields ------------------

def test_a_bridge_without_the_new_fields_reports_neither():
    before = sample(0, [node("/p/a", 100), node("/p/s", 100, type="glslTOP")])
    after = sample(60, [node("/p/a", 160), node("/p/s", 160, type="glslTOP")])
    for payload in (before, after):
        assert "scriptErrors" not in payload
        assert "compileResult" not in payload["nodes"][1]
    result = health_mod.check(FakeClient([before, after]))
    assert "shader-compile" not in kinds(result)
    assert "script-errors" not in kinds(result)
    assert result.ok


# -- what each new section costs the host -----------------------------------

def test_prints_what_each_new_section_costs():
    """Time the two new sections, host side.

    This measures parsing a payload, not collecting it inside TouchDesigner.
    Collection was timed separately on a live instance (build 2025.32460, a
    32-node project): one recursive scriptErrors call at the root cost 0.02 ms,
    and reading compileResult inside the walk that already exists replaced a
    separate pass costing 0.84–1.90 ms. No network of thousands of nodes was
    available to time, so scaling is not measured either side.

    Printed per `memory-bank/contract.md`: every new td_health section states
    its own cost in a test.
    """
    count = 2000
    plain = [node(f"/p/n{i}", 100 + i) for i in range(count)]
    plain_after = [node(f"/p/n{i}", 160 + i) for i in range(count)]
    shaders = [node(f"/p/g{i}", 100 + i, type="glslTOP",
                    compileResult=FAILED_COMPILE) for i in range(count)]
    shaders_after = [node(f"/p/g{i}", 160 + i, type="glslTOP",
                          compileResult=FAILED_COMPILE)
                     for i in range(count)]
    trace = "Traceback (most recent call last):\nNameError: name 'foo'"
    scripted = {f"/p/n{i}": trace for i in range(count)}

    def timed(first, second):
        start = time.perf_counter()
        result = health_mod.check(FakeClient([first, second]))
        return (time.perf_counter() - start) * 1000.0, result

    base, _ = timed(sample(0, plain), sample(60, plain_after))
    with_shaders, shader_result = timed(sample(0, shaders),
                                        sample(60, shaders_after))
    with_scripts, script_result = timed(
        sample(0, plain, scriptErrors=scripted),
        sample(60, plain_after, scriptErrors=scripted),
    )

    print(f"\nhealth parse over {count} nodes, host side only:")
    print(f"  baseline                : {base:.2f} ms")
    print(f"  + shader compile results: {with_shaders - base:+.2f} ms "
          f"({count} failing shaders, the worst case)")
    print(f"  + script errors         : {with_scripts - base:+.2f} ms "
          f"({count} tracebacks, the worst case)")
    print("  collection inside TouchDesigner, measured on a 32-node project: "
          "0.02 ms for the recursive scriptErrors call, compileResult read "
          "inside the existing walk; not measured at thousands of nodes")

    assert "shader-compile" in kinds(shader_result)
    assert "script-errors" in kinds(script_result)
