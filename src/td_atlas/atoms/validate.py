"""Check proposed parameter values against the index before touching TD.

An agent that guesses a parameter name gets an exception from TouchDesigner and
has to start a fresh round trip to find out what it should have written. The
index already knows every name, every menu option and every clamp, so the same
mistake can be caught locally and answered with the correct alternatives.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import Any

from .store import AtomStore


@dataclass
class Problem:
    parameter: str
    message: str
    suggestions: list[str] = field(default_factory=list)

    def render(self) -> str:
        text = f"{self.parameter}: {self.message}"
        if self.suggestions:
            text += f" (try: {', '.join(self.suggestions)})"
        return text


@dataclass
class Validation:
    op_type: str
    problems: list[Problem] = field(default_factory=list)
    checked: int = 0

    @property
    def ok(self) -> bool:
        return not self.problems

    def render(self) -> str:
        if self.ok:
            return f"all {self.checked} parameter(s) valid for {self.op_type}"
        lines = [f"{len(self.problems)} problem(s) for {self.op_type}:"]
        lines += [f"  - {p.render()}" for p in self.problems]
        return "\n".join(lines)


def validate_params(
    store: AtomStore, op_type: str, values: dict[str, Any]
) -> Validation:
    """Check a {name: value} mapping against an operator's known schema.

    Returns cleanly when the operator has not been probed — without runtime
    facts there is nothing authoritative to check against, and a false
    rejection is worse than letting TouchDesigner answer.
    """
    result = Validation(op_type=op_type)
    rows = store.parameters(op_type)
    if not rows:
        result.problems.append(
            Problem(op_type, f"unknown operator type '{op_type}'")
        )
        return result

    settable = {r["name"]: r for r in rows if r["settable"]}
    groups = {r["name"]: r for r in rows if not r["settable"]}
    if not settable:
        return result  # never probed; defer to TouchDesigner

    for name, value in values.items():
        result.checked += 1
        row = settable.get(name)
        if row is None:
            # A group heading is the most common near-miss: the agent read the
            # docs, which document 't', not 'tx'/'ty'/'tz'.
            if name in groups:
                members = [
                    r["name"] for r in rows if r["group_name"] == name and r["settable"]
                ]
                result.problems.append(
                    Problem(
                        name,
                        "is a parameter group, not a settable parameter",
                        members,
                    )
                )
            else:
                result.problems.append(
                    Problem(
                        name,
                        "no such parameter",
                        difflib.get_close_matches(
                            name, list(settable), n=5, cutoff=0.5
                        ),
                    )
                )
            continue

        if row["read_only"]:
            result.problems.append(Problem(name, "is read-only"))
            continue

        # Expressions, bindings and pulses bypass value checks: what they
        # evaluate to is only knowable inside TouchDesigner.
        if isinstance(value, dict):
            if not ({"expr", "bind", "pulse"} & set(value)):
                result.problems.append(
                    Problem(name, "object value needs one of expr/bind/pulse")
                )
            continue

        if row["is_menu"] and row["menu_names"]:
            names = row["menu_names"]
            if isinstance(value, str) and value not in names:
                result.problems.append(
                    Problem(
                        name,
                        f"'{value}' is not a valid menu entry",
                        difflib.get_close_matches(value, names, n=5, cutoff=0.3)
                        or names[:8],
                    )
                )
            elif isinstance(value, int) and not isinstance(value, bool):
                if not 0 <= value < len(names):
                    result.problems.append(
                        Problem(
                            name,
                            f"menu index {value} out of range 0..{len(names) - 1}",
                            names[:8],
                        )
                    )
            continue

        if row["is_number"] and isinstance(value, (int, float)):
            if row["clamp_min"] and row["min_value"] is not None:
                if value < row["min_value"]:
                    result.problems.append(
                        Problem(
                            name,
                            f"{value} is below the clamped minimum "
                            f"{row['min_value']}",
                        )
                    )
            if row["clamp_max"] and row["max_value"] is not None:
                if value > row["max_value"]:
                    result.problems.append(
                        Problem(
                            name,
                            f"{value} is above the clamped maximum "
                            f"{row['max_value']}",
                        )
                    )
            continue

        if row["is_string"] and not isinstance(value, str):
            result.problems.append(
                Problem(name, f"expects a string, got {type(value).__name__}")
            )

    return result
