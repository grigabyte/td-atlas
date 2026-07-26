"""Comparing two expanded projects.

A byte diff of .toe files says nothing, and a diff of the expanded tree buries
the answer in noise: every drag of a node rewrites its `tile` line. This
compares meaning instead — what was added, rewired, retyped, re-parameterised
or rewritten — and keeps pure repositioning in a separate bucket so it never
crowds out a real change.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field

from .model import Node, Project

# How many of an added node's parameters to show before summarising.
_ADDED_PARAM_LIMIT = 8


@dataclass
class ParamChange:
    name: str
    before: str | None
    after: str | None

    def render(self) -> str:
        if self.before is None:
            return f"+{self.name} = {self.after!r}"
        if self.after is None:
            return f"-{self.name} (was {self.before!r})"
        return f"{self.name}: {self.before!r} -> {self.after!r}"


@dataclass
class NodeChange:
    path: str
    op_type: str
    type_before: str | None = None
    type_after: str | None = None
    params: list[ParamChange] = field(default_factory=list)
    wiring_before: list[str] | None = None
    wiring_after: list[str] | None = None
    text_diff: str | None = None
    moved: bool = False

    @property
    def is_semantic(self) -> bool:
        """Whether anything beyond position changed."""
        return bool(
            self.type_before
            or self.params
            or self.wiring_before is not None
            or self.text_diff
        )

    def render(self) -> str:
        lines = [f"~ {self.path} ({self.op_type})"]
        if self.type_before:
            lines.append(f"    type: {self.type_before} -> {self.type_after}")
        if self.wiring_before is not None:
            lines.append(
                f"    inputs: {self.wiring_before or 'none'} -> "
                f"{self.wiring_after or 'none'}"
            )
        for change in self.params:
            lines.append(f"    {change.render()}")
        if self.text_diff:
            for line in self.text_diff.splitlines():
                lines.append(f"    {line}")
        return "\n".join(lines)


@dataclass
class ProjectDiff:
    added: list[Node] = field(default_factory=list)
    removed: list[Node] = field(default_factory=list)
    changed: list[NodeChange] = field(default_factory=list)
    moved_only: list[NodeChange] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.added or self.removed or self.changed)

    def render(self, show_moves: bool = False) -> str:
        if self.empty and not (show_moves and self.moved_only):
            return "No differences."
        out: list[str] = []
        for node in self.added:
            wiring = node.input_paths()
            suffix = f"  <- {', '.join(wiring)}" if wiring else ""
            out.append(f"+ {node.path} ({node.op_type}){suffix}")
            # A new node's non-default parameters are the substance of the
            # change, not a detail to look up separately.
            for name, value in sorted(node.parms.items())[:_ADDED_PARAM_LIMIT]:
                out.append(f"    {name} = {value.render()}")
            if len(node.parms) > _ADDED_PARAM_LIMIT:
                out.append(
                    f"    ... {len(node.parms) - _ADDED_PARAM_LIMIT} more "
                    f"parameter(s)"
                )
        for node in self.removed:
            out.append(f"- {node.path} ({node.op_type})")
        for change in self.changed:
            out.append(change.render())
        if show_moves and self.moved_only:
            out.append(
                f"\n({len(self.moved_only)} node(s) moved without other changes)"
            )
        elif self.moved_only:
            out.append(f"\n({len(self.moved_only)} node(s) only moved; use --moves to list)")
        return "\n".join(out)

    def summary(self) -> str:
        return (
            f"{len(self.added)} added, {len(self.removed)} removed, "
            f"{len(self.changed)} changed, {len(self.moved_only)} moved"
        )


def _text_diff(before: str, after: str, context: int = 2, limit: int = 60) -> str:
    lines = list(
        difflib.unified_diff(
            before.splitlines(),
            after.splitlines(),
            lineterm="",
            n=context,
        )
    )
    # Drop the ---/+++ header; the node path already identifies the file.
    lines = [line for line in lines if not line.startswith(("---", "+++"))]
    if len(lines) > limit:
        lines = lines[:limit] + [f"... ({len(lines) - limit} more diff lines)"]
    return "\n".join(lines)


def diff(
    before: Project, after: Project, include_text: bool = True
) -> ProjectDiff:
    """Compare two loaded projects."""
    old = before.nodes()
    new = after.nodes()
    result = ProjectDiff()

    for path in sorted(new.keys() - old.keys()):
        result.added.append(new[path])
    for path in sorted(old.keys() - new.keys()):
        result.removed.append(old[path])

    for path in sorted(old.keys() & new.keys()):
        a, b = old[path], new[path]
        change = NodeChange(path=path, op_type=b.op_type)

        if a.op_type != b.op_type:
            change.type_before, change.type_after = a.op_type, b.op_type

        names = set(a.parms) | set(b.parms)
        for name in sorted(names):
            old_value = a.parms[name].effective if name in a.parms else None
            new_value = b.parms[name].effective if name in b.parms else None
            if old_value != new_value:
                change.params.append(ParamChange(name, old_value, new_value))

        wiring_a, wiring_b = a.input_paths(), b.input_paths()
        if wiring_a != wiring_b:
            change.wiring_before, change.wiring_after = wiring_a, wiring_b

        if include_text and (a.text or b.text) and a.text != b.text:
            change.text_diff = _text_diff(a.text or "", b.text or "")

        change.moved = (a.node.x, a.node.y) != (b.node.x, b.node.y)

        if change.is_semantic:
            result.changed.append(change)
        elif change.moved:
            result.moved_only.append(change)

    return result
