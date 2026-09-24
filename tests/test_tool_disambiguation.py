"""Tools an agent confuses name each other where the agent is still reading.

An agent picks a tool from its description, and descriptions are long, so the
choice is made on the opening. An automated review of the server (Glama's
TDQS) named four pairs whose openings did not say which one to reach
for: each described itself well and never said where its neighbour starts.
The repair is in the docstrings; this holds it there, by requiring each tool of
a pair to name the other within the opening.

400 characters is two or three sentences of these docstrings, which is where
the repair put the distinction. The docstrings are read out of the source with
`ast`, not imported, so this runs with no index and no TouchDesigner.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SERVER = (
    Path(__file__).resolve().parent.parent / "src" / "td_atlas" / "mcp" / "server.py"
)

OPENING = 400

PAIRS = [
    ("td_health", "td_errors"),
    ("td_project_read", "td_project_text"),
    ("td_snapshot", "td_variant_save"),
    ("td_project_diff", "td_variant_diff"),
]


def _docstrings() -> dict[str, str]:
    tree = ast.parse(SERVER.read_text(encoding="utf-8"))
    return {
        node.name: ast.get_docstring(node) or ""
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("td_")
    }


DOCS = _docstrings()


@pytest.mark.parametrize(
    "tool, neighbour",
    [pair for a, b in PAIRS for pair in ((a, b), (b, a))],
)
def test_opening_names_the_neighbour(tool, neighbour):
    assert tool in DOCS, f"{tool} is not defined in server.py — the parse broke"
    opening = DOCS[tool][:OPENING]
    assert neighbour in opening, (
        f"the first {OPENING} characters of {tool}'s docstring never name "
        f"{neighbour}, so an agent choosing between the two is not told when "
        f"to take the other:\n{opening}"
    )
