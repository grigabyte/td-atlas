"""Tests for the silent-failure detector. No TouchDesigner required."""

from __future__ import annotations

import ast
import pathlib
import time

import pytest

from td_atlas.bridge import health as health_mod


class FakeClient:
    """Replays two prepared health samples, and records what was published."""

    def __init__(self, samples):
        self.samples = list(samples)
        self.published = []

    def call(self, method, **kw):
        if method == "status_note":
            self.published.append(kw.get("health"))
            return {"stored": True}
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
    separate pass measured at 1.90 ms over those 32 nodes, or 0.84 ms with
    the type filter this one uses — two conditions, not a range, and neither
    a ceiling. No network of thousands of nodes was available to time, so
    scaling is not measured either side.

    Printed per the invariant in `AGENTS.md` ("Invariants a change has to
    keep"): every new td_health section states its own cost in a test.
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


# -- the one-line verdict the bridge's status panel shows -------------------

def test_a_clean_check_summarises_as_nothing_wrong_found():
    nodes = [node("/project1/a", 10), node("/project1/b", 10)]
    after = [node("/project1/a", 70), node("/project1/b", 70)]
    result = health_mod.check(FakeClient([sample(0, nodes), sample(60, after)]))
    assert result.summary() == "60/60 fps, 2/2 cooking - nothing wrong found"


def test_the_summary_counts_findings_by_severity_and_never_lists_them():
    result = health_mod.Health(
        findings=[
            health_mod.Finding("error", "x", "boom", ["/project1/a"]),
            health_mod.Finding("error", "y", "boom", []),
            health_mod.Finding("warning", "z", "hmm", []),
        ],
        fps_target=60.0, fps_actual=30.0, nodes=9, cooking=4,
    )
    line = result.summary()
    assert line == "30/60 fps, 4/9 cooking - 2 errors, 1 warning"
    assert "\n" not in line and "/project1/a" not in line


def test_the_verdict_is_pushed_to_the_bridge_so_the_panel_can_show_it():
    nodes = [node("/project1/a", 10)]
    after = [node("/project1/a", 70)]
    client = FakeClient([sample(0, nodes), sample(60, after)])
    result = health_mod.check(client)
    assert client.published == [result.summary()]


def test_nothing_is_pushed_when_the_caller_asks_for_no_publishing():
    client = FakeClient([sample(0, [node("/project1/a", 10)]),
                         sample(60, [node("/project1/a", 70)])])
    health_mod.check(client, publish=False)
    assert client.published == []


def test_a_bridge_that_does_not_know_the_method_does_not_fail_the_check():
    """An older bridge answers 404; the verdict still comes back."""

    class Refusing(FakeClient):
        def call(self, method, **kw):
            if method == "status_note":
                raise RuntimeError("no method 'status_note'")
            return super().call(method, **kw)

    client = Refusing([sample(0, [node("/project1/a", 10)]),
                       sample(60, [node("/project1/a", 70)])])
    result = health_mod.check(client)
    assert result.ok and client.published == []


# -- what a warned operator's line says -------------------------------------

# The whole string TouchDesigner puts on the node at the head of a cook
# dependency loop, measured live on build 2025.32460. The stack behind the
# first line is the reason this is excerpted rather than printed.
COOK_LOOP_WARNING = (
    "Warning: Cook dependency loop detected. Check for exports, expressions "
    "or wiring that are creating this loop: \n"
    "\t# Cook stack starts\n"
    "\t/p/keep\n"
    "\t# Cook dependency loop starts\n"
    "\t/p/grade\n"
    "\t/p/comp\n"
    "\t/p/fb\n"
    "\t# Cook loop detected\n"
    "\t/p/grade (/p/grade)"
)


def test_a_warned_operator_is_named_together_with_what_it_is_warning_about():
    """A bare path sends the reader elsewhere to find out what is wrong."""
    before = [node("/p/grade", 100, warnings=COOK_LOOP_WARNING)]
    after = [node("/p/grade", 160, warnings=COOK_LOOP_WARNING)]
    result = health_mod.check(FakeClient([sample(0, before), sample(60, after)]))
    finding = next(f for f in result.findings if f.kind == "node-warnings")
    assert finding.paths == ["/p/grade (Cook dependency loop detected. Check "
                             "for exports, expressions or wiring that are "
                             "creating this loop:)"]
    # The cook stack stays out of it: that is what td_errors prints whole.
    assert "Cook stack starts" not in finding.render()


def test_a_one_line_warning_keeps_its_text_and_loses_only_the_prefix():
    warn = "Warning: The GLSL Shader has compile errors (Use Info DAT to see details)."
    before = [node("/p/g", 100, warnings=warn)]
    after = [node("/p/g", 160, warnings=warn)]
    result = health_mod.check(FakeClient([sample(0, before), sample(60, after)]))
    finding = next(f for f in result.findings if f.kind == "node-warnings")
    assert finding.paths == [
        "/p/g (The GLSL Shader has compile errors (Use Info DAT to see details).)"
    ]


# -- the invariant itself, not just its printout -----------------------------

# Every kind of finding `td_health` can report, and what states its cost.
# `AGENTS.md` ("Invariants a change has to keep") promises that a new section
# arrives with a measurement rather than an intention to take one; this
# listing is what makes that promise a gate instead of a habit. A new kind is
# not in it, the test below reds, and the way to make it green is to time the
# section in `test_prints_what_each_new_section_costs` and record the number
# here.
#
# The two marked TIMED are the sections that arrived after the invariant was
# written; `script-errors-unread` reads the same payload as `script-errors`
# and is covered by that section's own timing rather than claiming one of its
# own. The rest are read off fields the bridge's sample already carried
# before there was a gate: they cost a dictionary lookup on a payload already
# parsed, and no separate measurement was ever taken. That is recorded as it
# is rather than back-filled with a number nobody measured.
TIMED = "timed in test_prints_what_each_new_section_costs"
WITH_SCRIPT_ERRORS = "a second reading of the script-errors payload, timed with it"
PARSED_FIELD = "predates the gate: a lookup on the already-parsed sample"

SECTION_COSTS = {
    "shader-compile": TIMED,
    "script-errors": TIMED,
    "script-errors-unread": WITH_SCRIPT_ERRORS,
    "walk-truncated": PARSED_FIELD,
    "interval-clamped": PARSED_FIELD,
    "resolution-clamped": PARSED_FIELD,
    "licence": PARSED_FIELD,
    "node-errors": PARSED_FIELD,
    "node-warnings": PARSED_FIELD,
    "output-off": PARSED_FIELD,
    "not-cooking": PARSED_FIELD,
    "paused": PARSED_FIELD,
    "non-realtime": PARSED_FIELD,
    "expensive": PARSED_FIELD,
    "bypassed": PARSED_FIELD,
    "stalled": PARSED_FIELD,
    "slow": PARSED_FIELD,
}


def _finding_kinds() -> set[str]:
    """Every `kind` literal `health.py` builds a Finding with.

    Read from the source rather than by provoking each finding: provoking
    them is what the rest of this module does, and a section nobody wrote a
    test for is exactly the one this gate has to catch.
    """
    source = pathlib.Path(health_mod.__file__).read_text(encoding="utf-8")
    kinds = set()
    dynamic = []
    for call in ast.walk(ast.parse(source)):
        if not (isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id == "Finding"):
            continue
        kind = call.args[1] if len(call.args) > 1 else None
        if isinstance(kind, ast.Constant) and isinstance(kind.value, str):
            kinds.add(kind.value)
        else:
            dynamic.append(call.lineno)
    assert not dynamic, (
        f"health.py builds a Finding with a non-literal kind at line(s) "
        f"{dynamic}. This gate reads kinds out of the source, so a computed "
        f"one is invisible to it: name the kind with a literal, or replace "
        f"this derivation with something that can see yours."
    )
    return kinds


def test_every_health_section_states_its_cost():
    """The invariant in AGENTS.md, held rather than remembered.

    Before this, the invariant said every new `td_health` section states its
    own cost in a test, and the only test involved printed three numbers and
    asserted that two findings existed. A third section could arrive with no
    measurement at all and nothing would go red — the clause promised a gate
    that did not exist.

    Blind spot, stated: this fires on a new *kind*. A change that makes an
    existing kind read a new and expensive field of the sample keeps its name
    and stays invisible here; that half is the review's, as the reply shape
    is in `test_protocol_fingerprint.py`.
    """
    derived = _finding_kinds()
    assert derived == set(SECTION_COSTS), (
        f"missing from SECTION_COSTS: {sorted(derived - set(SECTION_COSTS))}; "
        f"recorded but no longer built: "
        f"{sorted(set(SECTION_COSTS) - derived)}.\n"
        f"A new td_health section states its own cost in a test — the "
        f"invariant in AGENTS.md. Time it in "
        f"test_prints_what_each_new_section_costs and record the number "
        f"beside its kind in SECTION_COSTS above."
    )


def test_the_sections_marked_timed_are_the_ones_the_timing_test_asserts_on():
    """Marking a kind TIMED must mean the timing test actually names it."""
    source = pathlib.Path(__file__).read_text(encoding="utf-8")
    body = source.split("def test_prints_what_each_new_section_costs")[1]
    body = body.split("\ndef ")[0]
    for kind, note in SECTION_COSTS.items():
        if note is TIMED:
            assert f'"{kind}"' in body, (
                f"{kind} is recorded as timed, but "
                f"test_prints_what_each_new_section_costs does not mention "
                f"it. Either time it there or record what it really costs."
            )
