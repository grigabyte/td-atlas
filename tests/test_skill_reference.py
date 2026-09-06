"""The skill's tool reference, checked against the tools that actually exist.

`plugin/skills/touchdesigner/references/tools.md` is the first thing an agent reads
before it touches TouchDesigner, and it is a hand-written list of every MCP
tool with its parameters. A hand-written list of a moving target rots the
moment a tool is added, renamed or given a new argument — and a skill that
names a tool which does not exist is worse than no skill, because the agent
believes it over its own observations.

So the list is treated the way `test_packaging.py` treats the generated
manifest: it stays hand-written, because the "use it for" column is prose no
generator can produce, but the machine-checkable half of every row — the name
and the parameter list — is compared against `td_atlas.mcp.server` here. The
header count is checked too, since a stale "forty tools" is the same lie in
smaller type.

The parse is deliberately textual on both sides: reading the decorated
functions out of the source rather than importing them keeps this test off the
index and off the bridge, so it runs with no TouchDesigner and no built index.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "src" / "td_atlas" / "mcp" / "server.py"
SKILL_DIR = ROOT / "plugin" / "skills" / "touchdesigner"
TOOLS_MD = SKILL_DIR / "references" / "tools.md"
SKILL_MD = SKILL_DIR / "SKILL.md"

_TOOL_DEF = re.compile(
    r"@mcp\.tool\(\)\s*\n(?:@[^\n]*\n)*\s*(?:async\s+)?def\s+(\w+)\s*\(([^)]*)\)"
)
_TOOL_ROW = re.compile(r"`(td_\w+)\(([^)]*)\)`")

# The count is required as a digit in one fixed phrase rather than sniffed out
# of the prose: the header also says how many groups the tools fall into, and a
# test that accepts any number in the paragraph would accept that one.
_HEADER_COUNT = re.compile(r"\b(\d+) MCP tools\b")


def _split_params(text: str) -> list[str]:
    return [
        part.split(":")[0].split("=")[0].strip()
        for part in " ".join(text.split()).split(",")
        if part.strip()
    ]


@pytest.fixture(scope="module")
def code_tools() -> dict[str, list[str]]:
    source = SERVER.read_text()
    tools = {
        m.group(1): _split_params(m.group(2))
        for m in _TOOL_DEF.finditer(source)
    }
    assert tools, "no @mcp.tool() functions found — the parse broke, not the code"
    return tools


@pytest.fixture(scope="module")
def documented() -> dict[str, list[str]]:
    return {
        m.group(1): _split_params(m.group(2))
        for m in _TOOL_ROW.finditer(TOOLS_MD.read_text())
    }


def test_every_tool_is_documented(code_tools, documented):
    missing = sorted(set(code_tools) - set(documented))
    assert not missing, (
        "tools exist that the skill never names, so an agent reading it will "
        f"not know they are there: {missing}"
    )


def test_nothing_documented_that_does_not_exist(code_tools, documented):
    invented = sorted(set(documented) - set(code_tools))
    assert not invented, (
        "the skill names tools the server does not expose; an agent will call "
        f"them and get nothing: {invented}"
    )


def test_parameters_match(code_tools, documented):
    wrong = {
        name: (code_tools[name], documented[name])
        for name in sorted(set(code_tools) & set(documented))
        if code_tools[name] != documented[name]
    }
    assert not wrong, (
        "parameter lists in the skill disagree with the signatures "
        f"(name: (code, skill)): {wrong}"
    )


def test_header_count_is_the_real_count(code_tools):
    """The tool count in the header of the reference is the count."""
    head = TOOLS_MD.read_text().split("\n##", 1)[0]
    stated = _HEADER_COUNT.search(head)
    assert stated, (
        "the reference header must state the count as '<n> MCP tools' so this "
        "test can hold it to the code"
    )
    assert int(stated.group(1)) == len(code_tools), (
        f"the header says {stated.group(1)} tools, the server exposes "
        f"{len(code_tools)}"
    )


def test_skill_only_names_tools_that_exist(code_tools):
    """SKILL.md itself, not just the reference, is held to the same rule."""
    named = set(re.findall(r"`(td_\w+)", SKILL_MD.read_text()))
    invented = sorted(named - set(code_tools))
    assert not invented, f"SKILL.md names tools that do not exist: {invented}"
