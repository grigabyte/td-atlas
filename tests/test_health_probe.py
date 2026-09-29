"""`health_probe`, the bridge half: which quiet operators had work to do.

Fake operators stand in for TouchDesigner's: `cook()` cooks one only when it
is dirty, and cooks its input first, which is the order trap the method has
to survive — measured live, cooking a Level TOP cooked the noise above it.
"""

from __future__ import annotations

import pytest

from td_atlas.component import handler


class Par:
    def __init__(self, name, value):
        self.name, self.value = name, value

    def eval(self):
        return self.value


class FakeOp:
    def __init__(self, path, dirty=False, cooks=5, family="TOP",
                 optype="noiseTOP", upstream=None):
        self.path = path
        self.dirty = dirty
        self.totalCooks = cooks
        self.family = family
        self.OPType = optype
        self.upstream = upstream

    def cook(self, force=False):
        if self.upstream is not None:
            self.upstream.cook()
        if self.dirty or force:
            self.totalCooks += 1
            self.dirty = False


@pytest.fixture
def ops(monkeypatch):
    table = {}
    monkeypatch.setattr(handler, "op", table.get, raising=False)
    return table


def test_an_upstream_cooked_by_its_consumer_still_counts_as_cooked(ops):
    noise = FakeOp("/p/noise", dirty=True)
    level = FakeOp("/p/level", dirty=True, upstream=noise, optype="levelTOP")
    ops.update({o.path: o for o in (noise, level)})
    # The consumer first: walking it cooks the noise before the noise's turn.
    answer = handler.m_health_probe({"paths": ["/p/level", "/p/noise"]})
    assert sorted(answer["cooked"]) == ["/p/level", "/p/noise"]
    assert answer["quiet"] == []


def test_static_never_cooked_outputs_and_containers_are_kept_apart(ops):
    static = FakeOp("/p/ramp")
    fresh = FakeOp("/p/new", dirty=True, cooks=0)
    rec = FakeOp("/p/rec", dirty=True, optype="moviefileoutTOP")
    box = FakeOp("/p/box", family="COMP", optype="baseCOMP")
    ops.update({o.path: o for o in (static, fresh, rec, box)})
    answer = handler.m_health_probe(
        {"paths": ["/p/ramp", "/p/new", "/p/rec", "/p/box", "/p/gone"]})
    assert answer["quiet"] == ["/p/ramp"]
    assert answer["never"] == ["/p/new"]
    assert answer["skipped"] == ["/p/rec", "/p/box"]
    assert answer["missing"] == ["/p/gone"]
    # Neither the recorder nor the never-cooked operator was asked to cook.
    assert rec.totalCooks == 5 and fresh.totalCooks == 0


def test_the_budget_leaves_the_rest_unprobed(ops, monkeypatch):
    monkeypatch.setattr(handler, "_PROBE_BUDGET_S", -1.0)
    ops.update({"/p/a": FakeOp("/p/a", dirty=True)})
    answer = handler.m_health_probe({"paths": ["/p/a"]})
    assert answer["unprobed"] == ["/p/a"] and answer["cooked"] == []


class ScriptOp(FakeOp):
    def __init__(self, path, text="", family="DAT", **kw):
        super().__init__(path, family=family, **kw)
        self.text = text
        self.added = []

    def scriptErrors(self):
        return self.text

    def clearScriptErrors(self):
        self.text = ""

    def addScriptError(self, msg):
        self.added.append(msg)
        self.text = "  Error: %s (%s)" % (msg, self.path)


def _recheck(ops, monkeypatch, dat, users=()):
    ops[dat.path] = dat
    for user in users:
        ops[user.path] = user
    monkeypatch.setattr(handler, "_callback_users", lambda target: list(users))
    state = handler.m_script_errors_recheck(
        {"step": "clear", "paths": [dat.path]})["state"]
    return state


def test_a_kept_traceback_whose_user_cooked_without_it_is_stale(ops, monkeypatch):
    dat = ScriptOp("/p/cb", '  Error: Traceback\n  File "/p/cb", line 3, in onCook\n'
                            "AttributeError: x (/p/cb)")
    user = FakeOp("/p/sc", optype="scriptCHOP")
    state = _recheck(ops, monkeypatch, dat, [user])
    assert dat.text == ""
    user.totalCooks += 12
    verdict = handler.m_script_errors_recheck({"step": "read", "state": state})
    assert verdict["verdicts"]["/p/cb"]["verdict"] == "stale"
    assert dat.text == ""


def test_an_unconfirmed_traceback_is_put_back_as_it_was(ops, monkeypatch):
    original = "  Error: Traceback\nKeyError: old (/p/cb)"
    dat = ScriptOp("/p/cb", original)
    state = _recheck(ops, monkeypatch, dat)
    verdict = handler.m_script_errors_recheck({"step": "read", "state": state})
    assert verdict["verdicts"]["/p/cb"]["verdict"] == "unknown"
    assert dat.text == original


def test_a_traceback_raised_again_is_live(ops, monkeypatch):
    dat = ScriptOp("/p/cb", "  Error: Traceback\nValueError: live (/p/cb)")
    state = _recheck(ops, monkeypatch, dat)
    dat.text = "  Error: Traceback\nValueError: live (/p/cb)"
    verdict = handler.m_script_errors_recheck({"step": "read", "state": state})
    assert verdict["verdicts"]["/p/cb"]["verdict"] == "live"


def test_array_lengths_are_read_from_the_source_and_the_chop(monkeypatch):
    class Dat:
        text = "uniform vec4 uSeg[256];\nuniform float uOk[8];\n"

    class Chop:
        path, numSamples = "/p/sim", 192

    class Small:
        path, numSamples = "/p/small", 8

    values = {"pixeldat": Dat(), "array0name": "uSeg", "array0arraytype":
              "uniformarray", "array0chop": Chop(), "array1name": "uOk",
              "array1arraytype": "uniformarray", "array1chop": Small()}

    class Glsl:
        class seq:
            class array:
                numBlocks = 2

        class par:
            pixeldat = Par("pixeldat", values["pixeldat"])

    monkeypatch.setattr(handler, "_par_value", lambda target, name: values.get(name))
    assert handler._array_mismatches(Glsl()) == [
        {"name": "uSeg", "declared": 256, "samples": 192, "chop": "/p/sim"}]


def test_a_traceback_cooks_do_not_rerun_is_not_called_fixed(ops, monkeypatch):
    """Critic, 2026-09-29: an onPulse error with its CHOP cooking every frame
    was judged fixed and cleared for good."""
    original = ('  Error: Traceback\n  File "/p/cb", line 9, in onPulse\n'
                "KeyError: x (/p/cb)")
    dat = ScriptOp("/p/cb", original)
    user = FakeOp("/p/sc", optype="scriptCHOP")
    state = _recheck(ops, monkeypatch, dat, [user])
    user.totalCooks += 30
    verdict = handler.m_script_errors_recheck({"step": "read", "state": state})
    assert verdict["verdicts"]["/p/cb"]["verdict"] == "unknown"
    assert dat.text == original


def test_restore_puts_every_cleared_text_back(ops, monkeypatch):
    original = "  Error: Traceback\nKeyError: old (/p/cb)"
    dat = ScriptOp("/p/cb", original)
    state = _recheck(ops, monkeypatch, dat)
    assert dat.text == ""
    assert handler.m_script_errors_recheck(
        {"step": "restore", "state": state})["restored"] == ["/p/cb"]
    assert dat.text == original
