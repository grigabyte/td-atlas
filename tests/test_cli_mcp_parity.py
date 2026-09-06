"""The CLI ↔ MCP parity section of `AGENTS.md`, checked against both surfaces.

`AGENTS.md` has asked for parity between `cli.py` and `mcp/server.py` since
the file existed, and the rule was prose. By the time the 2026-09-06 audit
counted, five gaps were declared and eleven were not — which is what a prose
invariant over two moving surfaces decays into. So the declaration became four
tables, and this reads them.

What it holds:

- every CLI capability and every MCP tool is either paired or declared as a
  one-sided gap, so adding one to a surface fails until it is paired or the
  gap is written down with a reason;
- for a pair, the argument *names* match after the declared renames, and any
  that do not are named in the divergences table;
- a divergence row whose argument no longer exists on either side fails, so
  the table cannot outlive what it describes;
- the two limits that were aligned rather than declared — `search --limit`
  against `td_search_operators(limit=)`, `project grep` against
  `td_project_grep(limit=)` — stay equal.

Both sides are read without touching the index or the bridge: the CLI through
`build_parser()`, which builds no store, and the server through `ast`, the
same textual route `test_skill_reference.py` takes and for the same reason.
"""

from __future__ import annotations

import argparse
import ast
import re
from pathlib import Path

import pytest

from td_atlas.cli import build_parser

ROOT = Path(__file__).resolve().parent.parent
AGENTS = ROOT / "AGENTS.md"
SERVER = ROOT / "src" / "td_atlas" / "mcp" / "server.py"

# The CLI's global flags are the one gap declared in prose rather than in the
# tables — see "the MCP surface cannot aim at a chosen instance" in AGENTS.md.
GLOBAL_FLAGS = {"db", "port", "project", "help", "func", "uses_selector"}

_CELL = re.compile(r"`([^`]+)`")
# A default this test cannot read off the source (a call, a name). Compared by
# identity, so it never equals the other side and the mismatch is loud.
_UNREADABLE = object()
_RENAME = re.compile(r"`([^`]+)`\s*→\s*`([^`]+)`")


# -- reading the declaration ------------------------------------------------

def _section(text: str, heading: str) -> str:
    """The body of one `### heading`, up to the next heading of any depth."""
    start = text.find(f"\n### {heading}\n")
    assert start >= 0, f"AGENTS.md has no '### {heading}' section"
    rest = text[start + 1 :]
    end = re.search(r"\n#{2,3} ", rest)
    return rest[: end.start()] if end else rest


def _rows(section: str) -> list[list[str]]:
    """The data rows of the one pipe table in a section, cells untouched."""
    out = []
    for line in section.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not cells or set("".join(cells)) <= set("- :"):
            continue  # the --- separator
        if cells[0].startswith("`") or "," in cells[0]:
            out.append(cells)
    return out


def _flag_to_dest(token: str) -> str:
    """`--no-text` and `-o/--output` as argparse spells them: `no_text`, `output`."""
    token = token.split("/")[-1].strip()
    return token.lstrip("-").replace("-", "_")


@pytest.fixture(scope="module")
def declared() -> dict:
    text = AGENTS.read_text(encoding="utf-8")

    pairs: dict[str, str] = {}
    renames: dict[str, dict[str, str]] = {}
    for cells in _rows(_section(text, "Paired")):
        cli = _CELL.search(cells[0]).group(1)
        mcp = _CELL.search(cells[1]).group(1)
        pairs[cli] = mcp
        mapping = {}
        if len(cells) > 2 and cells[2]:
            found = _RENAME.findall(cells[2])
            assert len(found) == cells[2].count("→"), (
                f"a rename cell must read '`cli`→`mcp`, ...': {cells[2]!r}"
            )
            mapping = {left: right for left, right in found}
        renames[cli] = mapping

    cli_only = {_CELL.search(c[0]).group(1) for c in _rows(_section(text, "CLI only"))}
    mcp_only = {
        name
        for cells in _rows(_section(text, "MCP only"))
        for name in _CELL.findall(cells[0])
    }

    divergences: dict[str, set[str]] = {}
    for cells in _rows(_section(text, "Declared argument divergences")):
        names = _CELL.findall(cells[0])
        assert len(names) == 2, f"a divergence row names one pair, got {names}"
        divergences[f"{names[0]} / {names[1]}"] = {
            _flag_to_dest(t) for t in _CELL.findall(cells[1])
        }
    return {
        "pairs": pairs,
        "renames": renames,
        "cli_only": cli_only,
        "mcp_only": mcp_only,
        "divergences": divergences,
    }


# -- reading the code -------------------------------------------------------

def _walk(parser: argparse.ArgumentParser, prefix: str = "") -> dict[str, dict]:
    """Every leaf subcommand, as `name -> {dest: default}`.

    `project` and `project variant` are containers, not capabilities: a leaf is
    a parser with no subparsers of its own, which is the level an MCP tool
    corresponds to.
    """
    subs = next(
        (a.choices for a in parser._actions if isinstance(a, argparse._SubParsersAction)),
        None,
    )
    if not subs:
        return {}
    out: dict[str, dict] = {}
    for name, sub in subs.items():
        path = f"{prefix}{name}"
        deeper = _walk(sub, prefix=f"{path} ")
        if deeper:
            out.update(deeper)
            continue
        out[path] = {
            a.dest: a.default
            for a in sub._actions
            if a.dest not in GLOBAL_FLAGS
        }
    return out


@pytest.fixture(scope="module")
def cli_commands() -> dict[str, dict]:
    commands = _walk(build_parser())
    assert commands, "build_parser() exposed no subcommands — the walk broke"
    return commands


@pytest.fixture(scope="module")
def mcp_tools() -> dict[str, dict]:
    """`tool -> {parameter: default}`, read out of the source, never imported."""
    tree = ast.parse(SERVER.read_text(encoding="utf-8"))
    tools: dict[str, dict] = {}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        decorated = any(
            isinstance(d, ast.Call)
            and isinstance(d.func, ast.Attribute)
            and d.func.attr == "tool"
            for d in node.decorator_list
        )
        if not decorated:
            continue
        args = node.args.args
        defaults: list = [None] * (len(args) - len(node.args.defaults))
        defaults += [
            d.value if isinstance(d, ast.Constant) else _UNREADABLE
            for d in node.args.defaults
        ]
        tools[node.name] = dict(zip((a.arg for a in args), defaults))
    assert tools, "no @mcp.tool() functions found — the parse broke, not the code"
    return tools


# -- the checks -------------------------------------------------------------

def test_every_cli_command_is_paired_or_declared(cli_commands, declared):
    accounted = set(declared["pairs"]) | declared["cli_only"]
    missing = sorted(set(cli_commands) - accounted)
    assert not missing, (
        "CLI capabilities that AGENTS.md neither pairs with an MCP tool nor "
        f"declares as a deliberate gap: {missing}"
    )


def test_every_mcp_tool_is_paired_or_declared(mcp_tools, declared):
    accounted = set(declared["pairs"].values()) | declared["mcp_only"]
    missing = sorted(set(mcp_tools) - accounted)
    assert not missing, (
        "MCP tools that AGENTS.md neither pairs with a CLI command nor "
        f"declares as a deliberate gap: {missing}"
    )


def test_the_declaration_names_nothing_that_is_gone(cli_commands, mcp_tools, declared):
    stale_cli = sorted(
        (set(declared["pairs"]) | declared["cli_only"]) - set(cli_commands)
    )
    stale_mcp = sorted(
        (set(declared["pairs"].values()) | declared["mcp_only"]) - set(mcp_tools)
    )
    assert not stale_cli and not stale_mcp, (
        "AGENTS.md declares capabilities that no longer exist "
        f"(CLI: {stale_cli}, MCP: {stale_mcp})"
    )


def test_paired_arguments_match_or_are_declared(cli_commands, mcp_tools, declared):
    """The check the prose rule never made: an argument on one side only is a
    divergence, and a divergence has to be written down with a reason."""
    undeclared = {}
    for cli, mcp in declared["pairs"].items():
        renames = declared["renames"][cli]
        ours = {renames.get(dest, dest) for dest in cli_commands[cli]}
        theirs = set(mcp_tools[mcp])
        gap = ours ^ theirs
        allowed = declared["divergences"].get(f"{cli} / {mcp}", set())
        if gap - allowed:
            undeclared[f"{cli} / {mcp}"] = sorted(gap - allowed)
    assert not undeclared, (
        "arguments that exist on one surface and not the other, and are not "
        f"in the divergences table: {undeclared}"
    )


def test_no_divergence_row_outlives_its_argument(cli_commands, mcp_tools, declared):
    orphans = {}
    for key, names in declared["divergences"].items():
        cli, _, mcp = key.partition(" / ")
        assert cli in cli_commands and mcp in mcp_tools, f"unknown pair {key!r}"
        renames = declared["renames"][cli]
        known = {renames.get(d, d) for d in cli_commands[cli]} | set(mcp_tools[mcp])
        gone = sorted(names - known)
        if gone:
            orphans[key] = gone
    assert not orphans, (
        "the divergences table explains arguments that exist on neither "
        f"surface any more: {orphans}"
    )


@pytest.mark.parametrize(
    "cli, mcp, dest, param",
    [
        ("search", "td_search_operators", "limit", "limit"),
        ("project grep", "td_project_grep", "limit", "limit"),
    ],
)
def test_aligned_limits_stay_aligned(cli_commands, mcp_tools, cli, mcp, dest, param):
    """Two limits the audit found differing for no recorded reason (15/12 and
    100/60). They were aligned rather than declared, which only means anything
    if something holds them equal — nothing else does."""
    ours = cli_commands[cli].get(dest)
    theirs = mcp_tools[mcp].get(param)
    assert ours == theirs, (
        f"`td-atlas {cli} --{dest}` defaults to {ours} and {mcp}'s `{param}` "
        f"to {theirs}; they are supposed to be the same number"
    )
