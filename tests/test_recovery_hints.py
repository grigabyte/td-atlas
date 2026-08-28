"""Every tool failure carries a hint, and every hint is one you can act on.

Two kinds of check here, and the first kind is the point. Per-branch tests only
cover the branches someone remembered to write a test for; the table-wide tests
walk `hints.HINTS` itself, so a hint added later is checked without anyone
adding a case — including the two rules a hint can break silently: naming a
tool or subcommand that does not exist, and recommending an install route that
does not (there is no `td-atlas` on PyPI).

Nothing here needs TouchDesigner. Exceptions are constructed the way the client
constructs them; the types were enumerated from a live build, but the suite
must not depend on one.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from td_atlas.bridge.client import BridgeError, BridgeUnavailable
from td_atlas.mcp import hints, server
from td_atlas.mcp.hints import HINTS, IndexMissing, classify, failure, guarded

ROOT = Path(__file__).resolve().parent.parent


def _fields(recovery: hints.Recovery) -> list[str]:
    return [recovery.cause, recovery.action, *recovery.resume]


ALL_TEXT = " ".join(
    text for recovery in HINTS.values() for text in _fields(recovery)
)


# -- the table as a whole ---------------------------------------------------

def _tool_names() -> set[str]:
    """The MCP tools that actually exist, read off the module's own functions."""
    return {
        name
        for name in dir(server)
        if name.startswith("td_") and callable(getattr(server, name))
    }


def _cli_subcommands() -> set[str]:
    source = (ROOT / "src" / "td_atlas" / "cli.py").read_text()
    return set(re.findall(r'add_parser\(\s*\n?\s*"([\w-]+)"', source))


def test_every_hint_says_what_happened_and_what_to_do():
    for key, recovery in HINTS.items():
        assert recovery.cause.strip(), key
        assert recovery.action.strip(), key


@pytest.mark.parametrize("key", sorted(HINTS))
def test_every_tool_a_hint_names_is_a_tool_that_exists(key):
    """A hint pointing at a tool that was renamed is worse than no hint."""
    tools = _tool_names()
    for name in set(re.findall(r"\btd_[a-z_]+\b", " ".join(_fields(HINTS[key])))):
        assert name in tools, f"{key} names a non-existent tool {name}"


# "the td-atlas bridge" is prose about this package, not a command to run. A
# hint quotes anything meant to be typed, the way doctor's `fix:` lines do, so
# the check below can tell the two apart.
_PROSE_AFTER_THE_NAME = {"bridge", "itself", "package"}


@pytest.mark.parametrize("key", sorted(HINTS))
def test_every_command_a_hint_names_is_a_real_subcommand(key):
    subcommands = _cli_subcommands()
    text = " ".join(_fields(HINTS[key]))
    for name in set(re.findall(r"'td-atlas ([a-z-]+)", text)):
        assert name in subcommands, f"{key} names a non-existent 'td-atlas {name}'"
    for name in set(re.findall(r"(?<!')td-atlas ([a-z-]+)", text)):
        assert name in subcommands or name in _PROSE_AFTER_THE_NAME, (
            f"{key} mentions 'td-atlas {name}' unquoted: quote a command, or "
            f"it is neither runnable nor checkable"
        )


def test_no_hint_recommends_an_uninstallable_package():
    """`td-atlas` is not on PyPI (checked: 404), so neither route can work."""
    assert "uvx" not in ALL_TEXT
    assert "pip install td-atlas" not in ALL_TEXT
    assert "pip install td_atlas" not in ALL_TEXT


def test_hints_that_know_no_action_name_nothing_to_call():
    for key in ("unmapped_bridge_error", "unmapped_error"):
        assert HINTS[key].resume == ()
        assert "none known" in HINTS[key].action


# -- classification ---------------------------------------------------------

@pytest.mark.parametrize(
    "reason,expected",
    [
        ("bridge_unreachable", "td-atlas install"),
        ("bridge_timeout", "main thread"),
        ("bridge_http", "td-atlas doctor"),
        ("bridge_protocol", "td-atlas reload"),
    ],
)
def test_each_way_the_bridge_can_be_unavailable_gets_its_own_repair(reason, expected):
    text = failure(BridgeUnavailable("whatever the message says", reason=reason))
    assert "cause: " in text and "fix: " in text
    assert expected in text


def test_an_unavailability_with_no_reason_still_gets_a_hint():
    text = failure(BridgeUnavailable("something else entirely"))
    assert "fix: " in text


@pytest.mark.parametrize(
    "error_type,expected_tool",
    [
        ("LookupError", "td_network"),
        ("AttributeError", "td_operator_schema"),
        ("TypeError", "td_op_info"),
        ("SyntaxError", "td_python_api"),
        ("ScopeHeld", "td_scopes"),
        ("Unauthorized", "td_doctor"),
    ],
)
def test_the_types_touchdesigner_raises_map_to_the_tool_that_answers_them(
    error_type, expected_tool
):
    """Mapped by `BridgeError.type` alone — never by the message text, which
    the handler is free to reword."""
    exc = BridgeError({"type": error_type, "message": "..."}, "op_info")
    text = failure(exc)
    assert f"continue with: {expected_tool}" in text or expected_tool in text
    assert "none known" not in text


def test_an_argument_the_bridge_refused_gets_no_invented_next_call():
    text = failure(BridgeError({"type": "ValueError", "message": "bad ttl"}, "claim"))
    assert "fix: " in text
    assert "continue with:" not in text


def test_an_unmapped_touchdesigner_exception_invents_nothing():
    exc = BridgeError({"type": "SomeNovelTDError", "message": "who knows"}, "exec")
    text = failure(exc)
    assert "SomeNovelTDError" in text
    assert "none known" in text
    assert "continue with:" not in text


def test_a_fault_in_this_package_is_named_as_one():
    text = failure(KeyError("snippet_path"))
    assert "KeyError" in text
    assert "inside td-atlas itself" in text
    assert "continue with:" not in text


def test_the_missing_index_is_classified_by_type_not_by_message():
    assert classify(IndexMissing("worded however")) is HINTS["index_missing"]


def test_a_head_keeps_the_tools_own_wording():
    exc = BridgeError({"type": "LookupError", "message": "no operator"}, "batch")
    text = failure(exc, head="batch failed and was rolled back — LookupError")
    assert text.startswith("batch failed and was rolled back")
    assert "cause: " in text


# -- the decorator ----------------------------------------------------------

def test_every_registered_tool_is_guarded():
    """A tool added without @guarded loses its hints and raises ToolError."""
    unguarded = [
        name
        for name in sorted(_tool_names())
        if not getattr(getattr(server, name), "__td_atlas_guarded__", False)
    ]
    assert not unguarded, f"missing @guarded: {', '.join(unguarded)}"


def test_guarded_turns_an_escaping_exception_into_a_hinted_string():
    @guarded
    def tool(path: str) -> str:
        raise BridgeUnavailable("down", reason="bridge_unreachable")

    text = tool("/project1")
    assert text.startswith("error: down")
    assert "fix: " in text


def test_guarded_keeps_the_signature_the_schema_is_built_from():
    import inspect

    @guarded
    def tool(path: str, depth: int = 1) -> str:
        return "fine"

    assert list(inspect.signature(tool).parameters) == ["path", "depth"]
    assert tool("/x") == "fine"


# -- the tools' own branches ------------------------------------------------

class _Cursor:
    def __init__(self, rows):
        self._rows = list(rows)

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _Store:
    """Enough of AtomStore for the refusal branches, with no database."""

    def __init__(self, rows=()):
        self.rows = list(rows)
        self.conn = self

    def execute(self, sql, params=()):
        return _Cursor(self.rows)

    def match(self, query):
        return query

    def search_ops(self, *args, **kwargs):
        return []

    def parameters(self, op_type):
        return []


@pytest.fixture
def no_bridge(monkeypatch):
    """Every bridge tool fails the same way: nothing is listening."""

    def explode():
        raise BridgeUnavailable("Cannot reach TouchDesigner", reason="bridge_unreachable")

    monkeypatch.setattr(server, "bridge", explode)


BRIDGE_TOOLS = [
    (server.td_network, ()),
    (server.td_op_info, ("/project1/noise1",)),
    (server.td_errors, ()),
    (server.td_exec, ("1 + 1",)),
    (server.td_render, ("/project1/out1",)),
    (server.td_undo, ()),
    (server.td_scopes, ()),
    (server.td_claim_scope, ("/project1", "me")),
    (server.td_release_scope, ("/project1", "me")),
    (server.td_snapshot, ()),
    (server.td_set_params, ("/project1/noise1", {"period": 2})),
    (server.td_build, ([{"method": "op_delete", "params": {"path": "/x"}}],)),
]


@pytest.mark.parametrize(
    "tool,args", BRIDGE_TOOLS, ids=[t.__name__ for t, _ in BRIDGE_TOOLS]
)
def test_no_bridge_tool_reports_a_dead_bridge_without_saying_what_to_do(
    tool, args, no_bridge, monkeypatch
):
    monkeypatch.setattr(server, "store", lambda: _Store())
    text = tool(*args)
    assert "cause: " in text and "fix: " in text, text
    assert "td-atlas install" in text


def test_a_search_that_matches_nothing_says_how_to_widen_it(monkeypatch):
    monkeypatch.setattr(server, "store", lambda: _Store())
    text = server.td_search_operators("a thing nothing does")
    assert "No operators match" in text
    assert "fix: " in text
    assert "td_search_parameters" in text


def test_an_unknown_operator_type_points_at_the_search(monkeypatch):
    monkeypatch.setattr(server, "store", lambda: _Store())
    text = server.td_operator_schema("blurTOPP")
    assert "No operator type" in text
    assert "td_search_operators" in text


def test_a_missing_index_tells_you_which_command_builds_it(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "_store", None)
    monkeypatch.setattr(server.cfg, "db_path", lambda: tmp_path / "nothing.db")
    text = server.td_search_operators("blur")
    assert "td-atlas build" in text
    assert "cause: " in text


def test_parameters_refused_locally_point_at_the_schema(monkeypatch):
    class _Check:
        ok = False

        def render(self):
            return "noiseTOP has no parameter 'perod'. Did you mean: period?"

    monkeypatch.setattr(server, "store", lambda: _Store())
    monkeypatch.setattr(server, "validate_params", lambda *a, **k: _Check())
    text = server.td_set_params("/project1/noise1", {"perod": 2}, op_type="noiseTOP")
    assert "Did you mean" in text
    assert "td_operator_schema" in text


def test_a_palette_name_in_two_folders_is_refused_with_the_way_to_choose(monkeypatch):
    rows = [
        {"name": "operatorPath", "category": "Tools", "path": "/a.tox", "summary": ""},
        {"name": "operatorPath", "category": "Techniques", "path": "/b.tox", "summary": ""},
    ]
    monkeypatch.setattr(server, "store", lambda: _Store(rows))
    text = server.td_palette_load("operatorPath")
    assert "category=" in text
    assert "td_palette" in text


def test_a_palette_name_that_does_not_exist_points_at_the_search(monkeypatch):
    monkeypatch.setattr(server, "store", lambda: _Store())
    text = server.td_palette_load("blurThing")
    assert "td_palette" in text
    assert "fix: " in text


def test_an_index_naming_a_file_that_is_gone_says_to_rebuild(monkeypatch, tmp_path):
    rows = [
        {
            "name": "checker",
            "category": "Tools",
            "path": str(tmp_path / "gone.tox"),
            "summary": "",
        }
    ]
    monkeypatch.setattr(server, "store", lambda: _Store(rows))
    text = server.td_palette_load("checker")
    assert "td-atlas build" in text
    assert "cause: " in text


def test_no_example_network_is_a_gap_with_somewhere_else_to_go(monkeypatch):
    rows = [{"type": "noiseTOP", "snippet_path": ""}]
    monkeypatch.setattr(server, "store", lambda: _Store(rows))
    text = server.td_example("noiseTOP")
    assert "ships no example" in text
    assert "td_operator_schema" in text


def test_a_label_that_cannot_be_a_filename_says_what_is_allowed():
    text = server.td_snapshot(label="../etc/passwd")
    assert "cause: " in text
    assert "letters, digits" in text


def test_a_broken_regex_is_the_callers_argument_not_a_td_atlas_fault(monkeypatch):
    from td_atlas import project
    from td_atlas.project import render as project_render

    monkeypatch.setattr(project, "load_file", lambda *a, **k: object())
    monkeypatch.setattr(project, "index_resolver", lambda *a, **k: None)

    def bad_grep(*args, **kwargs):
        raise ValueError("unbalanced parenthesis at position 3")

    monkeypatch.setattr(project_render, "grep", bad_grep)
    text = server.td_project_grep("/x.toe", "op(")
    assert "regular expression" in text
    assert "inside td-atlas itself" not in text


def test_a_project_file_that_cannot_be_unpacked_points_at_the_installation(monkeypatch):
    from td_atlas import project
    from td_atlas.project import ExpandError

    def bad_load(*args, **kwargs):
        raise ExpandError("toeexpand failed on /x.toe")

    monkeypatch.setattr(project, "load_file", bad_load)
    monkeypatch.setattr(project, "index_resolver", lambda *a, **k: None)
    text = server.td_project_read("/x.toe")
    assert "td-atlas doctor" in text
    assert "toeexpand" in text


def test_the_doctor_failing_to_run_names_the_cli_that_shows_why(monkeypatch):
    from td_atlas import cli

    def explode(args):
        raise OSError("no such directory")

    monkeypatch.setattr(cli, "doctor_checks", explode)
    text = server.td_doctor()
    assert "td-atlas doctor" in text
    assert "cause: " in text


@pytest.mark.parametrize("exc", [RuntimeError("nobody mapped this"), KeyError("boom")])
def test_a_bug_in_this_package_is_reported_as_one_rather_than_as_a_ToolError(
    monkeypatch, exc
):
    """Including exception types nobody anticipated — a KeyError out of a tool
    is the case where an unhinted ToolError would reach the agent."""

    def explode():
        raise exc

    monkeypatch.setattr(server, "bridge", explode)
    text = server.td_errors()
    assert "inside td-atlas itself" in text
    assert "none known" in text
    assert "continue with:" not in text
