"""Turning a loaded project into text an agent can read."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .model import Node, Project


def describe(
    project: Project,
    path: str | None = None,
    depth: int = 2,
    params: bool = False,
    limit: int = 400,
) -> str:
    """Render a project's structure as an indented tree.

    `path` narrows to one subtree, `depth` is how many levels of children to
    show below it, and `params` includes the values that differ from the
    operator's defaults — which is all an expanded project stores, so it is
    exactly the set of deliberate choices someone made.
    """
    roots: list[Node]
    if path:
        node = project.find(path)
        if node is None:
            available = ", ".join(sorted(n.path for n in project.roots))
            return f"No node at '{path}'. Top level: {available}"
        roots = [node]
    else:
        roots = project.roots

    lines = [
        f"{project.source.name} — TouchDesigner build "
        f"{project.build_version}, {len(list(project.walk()))} operators"
    ]
    shown = 0

    def walk(node: Node, level: int) -> None:
        nonlocal shown
        if shown >= limit:
            return
        shown += 1
        indent = "  " * level
        wiring = node.input_paths()
        suffix = f"  <- {', '.join(wiring)}" if wiring else ""
        kind = node.op_type or node.family or "?"
        lines.append(f"{indent}{node.name} ({kind}){suffix}")
        if params and node.parms:
            for name, value in sorted(node.parms.items()):
                lines.append(f"{indent}  .{name} = {value.render()}")
        if node.text:
            first = next(
                (ln for ln in node.text.splitlines() if ln.strip()), ""
            )
            lines.append(
                f"{indent}  text: {len(node.text)} chars — {first[:70]!r}"
            )
        if level < depth:
            for child in node.children:
                walk(child, level + 1)
        elif node.children:
            lines.append(f"{indent}  ... {len(node.children)} child node(s)")

    for root in roots:
        walk(root, 0)
    if shown >= limit:
        lines.append(f"... output truncated at {limit} operators")
    return "\n".join(lines)


@dataclass
class Match:
    node_path: str
    op_type: str
    line_number: int
    line: str


def grep(
    project: Project,
    pattern: str,
    regex: bool = True,
    ignore_case: bool = True,
    limit: int = 100,
) -> list[Match]:
    """Search the code held inside a project's DATs.

    Python callbacks, GLSL and other DAT text are the parts of a project a
    plain file search cannot reach, because they live inside the container.
    """
    flags = re.IGNORECASE if ignore_case else 0
    if regex:
        try:
            matcher = re.compile(pattern, flags)
        except re.error as exc:
            raise ValueError(f"invalid regular expression: {exc}") from exc
    else:
        matcher = re.compile(re.escape(pattern), flags)

    out: list[Match] = []
    for node in project.walk():
        if not node.text:
            continue
        for number, line in enumerate(node.text.splitlines(), 1):
            if matcher.search(line):
                out.append(
                    Match(node.path, node.op_type, number, line.strip()[:200])
                )
                if len(out) >= limit:
                    return out
    return out


def render_matches(matches: list[Match], pattern: str) -> str:
    if not matches:
        return f"No DAT contents match {pattern!r}."
    lines = [f"{len(matches)} match(es) for {pattern!r}:"]
    for match in matches:
        lines.append(
            f"  {match.node_path} ({match.op_type}):{match.line_number}: "
            f"{match.line}"
        )
    return "\n".join(lines)
