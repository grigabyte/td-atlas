"""An error in a statement script is not chained to the eval attempt.

`exec` tries the code as an expression first. The statement run used to sit
inside the `except SyntaxError` of that try, so any error the script raised
carried the SyntaxError as its context, and the traceback an agent read began
with "SyntaxError: invalid syntax" at line 1 — a line that was fine (agent
report 5, point 10).
"""

from __future__ import annotations

import pytest

from td_atlas.component import handler


def test_a_name_error_on_line_two_carries_no_syntax_error_context():
    with pytest.raises(NameError) as caught:
        handler.m_exec({"code": "x = 1\ny = undefined_name + 1\n"})
    assert caught.value.__context__ is None


def test_an_expression_still_returns_its_value():
    assert handler.m_exec({"code": "1 + 2"})["result"] == 3
