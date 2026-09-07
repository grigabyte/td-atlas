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

from td_atlas import config as cfg

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


# -- the numbers the skill states -------------------------------------------
#
# The reference and SKILL.md quote the size of the corpus — 2,060 wiki pages,
# 277 palette components, 185 glossary entries, 71 contracted type names —
# because an agent deciding whether to consult `td_docs` needs to know it is
# not consulting a stub. Those numbers come from an installation, so they move
# when the reader's TouchDesigner build does, and nothing checked them: the
# 2026-09-06 audit found the reference stating four numbers no test had ever
# read.
#
# The index is resolved at import time deliberately. `tests/conftest.py` points
# TD_ATLAS_HOME at a tmp directory for every test, which is what keeps the
# suite out of the developer's own state — collection runs before that fixture
# does, so this is where the real path is still visible.

_REAL_DB = cfg.db_path()

needs_index = pytest.mark.skipif(
    not _REAL_DB.exists(),
    reason=(
        "no atom index on this machine (build one with 'td-atlas build'). "
        "The same rule the install-dependent tests follow: skipping is honest "
        "here, because the check passes on any machine that has one."
    ),
)

# What to count, and the phrase that must state it. The phrase is pinned rather
# than "any number near the word", so a paragraph mentioning a second number
# cannot satisfy the check by accident — the same reason the tool count is
# pinned to '<n> MCP tools' above.
_CORPUS = {
    "wiki pages": (
        re.compile(r"([\d,]+) wiki pages"),
        "SELECT COUNT(*) FROM articles",
    ),
    "glossary entries": (
        re.compile(r"([\d,]+) glossary entries"),
        "SELECT COUNT(*) FROM articles WHERE categories LIKE '%Touch Glossary%'",
    ),
    "palette components": (
        re.compile(r"([\d,]+) (?:finished|ready-made) components"),
        "SELECT COUNT(*) FROM palette",
    ),
    "type aliases": (
        re.compile(r"alias table \(([\d,]+) entries\)"),
        "SELECT COUNT(*) FROM type_aliases",
    ),
}

GOTCHAS_MD = SKILL_DIR / "references" / "gotchas.md"
HEALTH = ROOT / "src" / "td_atlas" / "bridge" / "health.py"

_SKILL_TEXTS = (SKILL_MD, TOOLS_MD, GOTCHAS_MD)


@pytest.fixture(scope="module")
def index():
    import sqlite3

    conn = sqlite3.connect(f"file:{_REAL_DB}?mode=ro", uri=True)
    yield conn
    conn.close()


@needs_index
@pytest.mark.parametrize("label", sorted(_CORPUS))
def test_the_corpus_numbers_are_the_index_s(label, index):
    pattern, sql = _CORPUS[label]
    actual = index.execute(sql).fetchone()[0]

    found = []
    for path in _SKILL_TEXTS:
        for stated in pattern.findall(path.read_text(encoding="utf-8")):
            found.append((path.name, int(stated.replace(",", ""))))

    assert found, (
        f"no skill text states the {label} count in the form this test reads "
        f"({pattern.pattern!r}). Rewording it out of existence removes the "
        f"check with it — keep the phrase, or change it here as well."
    )
    wrong = [(name, n) for name, n in found if n != actual]
    assert not wrong, (
        f"the skill states {wrong} {label} and the index on this machine has "
        f"{actual}"
    )


# -- gotchas.md, which nothing read at all ----------------------------------

_FINDING_KIND = re.compile(
    r'Finding\(\s*"(?:error|warning|note)",\s*"([a-z0-9-]+)"'
)


def test_every_health_finding_is_described_in_gotchas():
    """`td_health` prints a kind with every finding — `[WARN ] stalled`. An
    agent that reads one and searches the skill for it must find the entry
    explaining what was actually detected; four kinds had no entry at all."""
    kinds = set(_FINDING_KIND.findall(HEALTH.read_text(encoding="utf-8")))
    assert len(kinds) > 10, "the parse of health.py broke, not the code"

    text = GOTCHAS_MD.read_text(encoding="utf-8")
    missing = sorted(k for k in kinds if f"`{k}`" not in text)
    assert not missing, (
        "td_health can report these and gotchas.md never names them, so an "
        f"agent that reads one has nowhere to look it up: {missing}"
    )


def test_gotchas_does_not_describe_a_detector_that_is_gone():
    """The other direction: a kind named in the skill and not in the code sends
    the reader looking for output that can never appear.

    Only hyphenated lower-case words in backticks are considered — that is what
    a finding kind looks like, and today every one of them in this file is one.
    A hyphenated word that is deliberately not a kind goes in `_NOT_A_KIND`.
    """
    kinds = set(_FINDING_KIND.findall(HEALTH.read_text(encoding="utf-8")))
    text = GOTCHAS_MD.read_text(encoding="utf-8")
    looks_like = set(re.findall(r"`([a-z][a-z0-9]*(?:-[a-z0-9]+)+)`", text))
    invented = sorted(looks_like - kinds - _NOT_A_KIND)
    assert not invented, (
        "gotchas.md names what read as td_health findings that health.py "
        f"cannot produce: {invented}"
    )


# Hyphenated backticked words in gotchas.md that are not finding kinds. Empty
# today, and listed rather than pattern-matched away so that the check above
# stays a check.
_NOT_A_KIND: set[str] = set()


# -- Alt+T ------------------------------------------------------------------

def test_no_skill_text_recommends_the_textport_shortcut():
    """The keyboard shortcut opens the textport only when TouchDesigner is
    focused and on the current desktop. Otherwise the keys land in the network
    editor and create operators in the artist's project — measured, and the
    reason SKILL.md gives the menu path instead."""
    offenders = [
        path.name
        for path in _SKILL_TEXTS
        if re.search(r"alt\+t", path.read_text(encoding="utf-8"), re.I)
    ]
    assert not offenders, (
        f"these recommend the textport keyboard shortcut: {offenders}"
    )
