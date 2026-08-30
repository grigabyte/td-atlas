"""What a failed parameter read-back says, when TouchDesigner will not say it.

Measured in a live session (2026-08-30, build 2025.32460, journal entry 13): an
expression written to `ty` came back as

    Error in /project1/REF/masses parameter t

— the parameter *group*, not the member that was written, and no cause at all.
The agent that got it went looking at `tx` and `tz` too. Entry 5 of the same
journal is the other half: a write that succeeded and only failed its
read-back (`me.inputVal`, valid only inside a cook) was reported as a failure
three times running.

Everything that message lacks is known at the point of the write, so
`_read_back` puts it back: the member's own name, that the write itself landed,
and the two causes that account for most of these — offered as candidates,
because TouchDesigner does not say which and this project does not guess.
"""

from __future__ import annotations

import pytest

from td_atlas.component import handler


class FakePar:
    """A parameter whose `eval()` may raise the way TouchDesigner's does."""

    def __init__(self, name, raises=None):
        self.name = name
        self.val = None
        self.expr = None
        self.bindExpr = None
        self.mode = "ParMode.EXPRESSION"
        self._raises = raises

    def eval(self):
        if self._raises is not None:
            raise self._raises
        return self.val


class FakePars:
    def __init__(self, **pars):
        for name, par in pars.items():
            setattr(self, name, par)


class FakeOP:
    def __init__(self, path, op_type, **pars):
        self.path = path
        self.OPType = op_type
        self.par = FakePars(**pars)


# The message TouchDesigner actually produced, kept verbatim: the whole point
# is that this text names `t` while the caller wrote `ty`.
TD_MESSAGE = "Error in /project1/REF/masses parameter t"


def test_the_member_is_named_not_only_the_group():
    target = FakeOP(
        "/project1/REF/masses",
        "geometryCOMP",
        ty=FakePar("ty", raises=Exception(TD_MESSAGE)),
    )
    with pytest.raises(ValueError) as caught:
        handler._apply_pars(target, {"ty": {"expr": "0.25 * sin(absTime.seconds)"}})
    message = str(caught.value)
    assert "'ty'" in message, "the parameter the caller actually set must be named"
    assert TD_MESSAGE in message, "TouchDesigner's own text must survive"
    assert "group" in message, "the reader must be told why TD said 't'"


def test_the_write_is_reported_as_having_happened():
    """Journal entry 5: a good write reported as a failure cost three calls."""
    target = FakeOP(
        "/project1/AV/p_pos",
        "expressionCHOP",
        expr0expr=FakePar("expr0expr", raises=Exception("Error in ... expr0expr")),
    )
    with pytest.raises(ValueError) as caught:
        handler._apply_pars(target, {"expr0expr": {"expr": "me.inputVal * 4.0"}})
    message = str(caught.value)
    assert "written" in message
    assert "EXPRESSION" in message, "the stored mode says the write landed"
    assert "me.inputVal" in message, "the cook-only cause must be offered"


def test_the_group_claim_is_not_made_when_td_named_the_member():
    """Entry 5's message *does* name the parameter; claiming otherwise misleads."""
    target = FakeOP(
        "/project1/AV/p_pos",
        "expressionCHOP",
        expr0expr=FakePar("expr0expr", raises=Exception("Error in ... expr0expr")),
    )
    with pytest.raises(handler.ParEvalError) as caught:
        handler._apply_pars(target, {"expr0expr": {"expr": "me.inputVal * 4.0"}})
    assert "parameter group" not in str(caught.value)


def test_the_failure_is_its_own_type_not_a_plain_value_error():
    """A refused argument and a failed read-back mean opposite things."""
    target = FakeOP("/x", "geometryCOMP",
                    ty=FakePar("ty", raises=Exception(TD_MESSAGE)))
    with pytest.raises(handler.ParEvalError):
        handler._apply_pars(target, {"ty": 1.0})
    assert issubclass(handler.ParEvalError, ValueError), "old callers still catch it"


def test_the_operator_is_named():
    target = FakeOP(
        "/project1/REF/masses", "geometryCOMP",
        ty=FakePar("ty", raises=Exception(TD_MESSAGE)),
    )
    with pytest.raises(ValueError) as caught:
        handler._apply_pars(target, {"ty": {"expr": "sin(1)"}})
    assert "/project1/REF/masses" in str(caught.value)


def test_the_scope_cause_is_offered_without_being_asserted():
    """`math.sin` is named as a candidate, not diagnosed as the cause."""
    target = FakeOP(
        "/x", "geometryCOMP", ty=FakePar("ty", raises=Exception(TD_MESSAGE))
    )
    with pytest.raises(ValueError) as caught:
        handler._apply_pars(target, {"ty": {"expr": "sin(1)"}})
    message = str(caught.value)
    assert "math.sin" in message
    assert "most of these" in message, "candidates, not a verdict"


def test_the_original_exception_is_kept_as_the_cause():
    original = Exception(TD_MESSAGE)
    target = FakeOP("/x", "geometryCOMP", ty=FakePar("ty", raises=original))
    with pytest.raises(ValueError) as caught:
        handler._apply_pars(target, {"ty": 1.0})
    assert caught.value.__cause__ is original


def test_an_unreadable_mode_does_not_hide_the_error():
    """The mode is a nicety; failing to read it must not swallow the message."""

    class ModelessPar(FakePar):
        def __getattribute__(self, name):
            if name == "mode":
                raise RuntimeError("no mode here")
            return object.__getattribute__(self, name)

    target = FakeOP(
        "/x", "levelTOP",
        contrast=ModelessPar("contrast", raises=Exception("boom")),
    )
    with pytest.raises(ValueError) as caught:
        handler._apply_pars(target, {"contrast": 0})
    message = str(caught.value)
    assert "boom" in message
    assert "unknown" in message


def test_a_working_read_back_is_untouched():
    par = FakePar("contrast")
    target = FakeOP("/x", "levelTOP", contrast=par)
    assert handler._apply_pars(target, {"contrast": 1.0}) == {"contrast": 1.0}


def test_a_missing_parameter_still_says_so_plainly():
    """The wrapper must not turn 'no such parameter' into an evaluation story."""
    target = FakeOP("/x", "levelTOP")
    with pytest.raises(AttributeError) as caught:
        handler._apply_pars(target, {"contrsat": 1.0})
    assert "no parameter 'contrsat'" in str(caught.value)
