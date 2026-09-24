"""Check proposed parameter values against the index before touching TD.

An agent that guesses a parameter name gets an exception from TouchDesigner and
has to start a fresh round trip to find out what it should have written. The
index already knows every name, every menu option and every clamp, so the same
mistake can be caught locally and answered with the correct alternatives.
"""

from __future__ import annotations

import difflib
import posixpath
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .store import AtomStore

# Whether a network path exists in the running project: True, False, or None
# when nobody could ask. Supplied by the caller that can reach the bridge.
Exists = Callable[[str], "bool | None"]


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


# -- batch step keys ----------------------------------------------------------

# The keys each batchable bridge method reads out of its `params`, copied from
# the handler. The handler reads with `params.get(key, default)`, so a key it
# does not read is not an error there: it is ignored, and the default stands
# in. That is how `op_connect` with `input_index: 3` wired input 0 without a
# word ("Спотыкания помельче", first agent report). `owner` is in every row
# because the batch carries it into every step.
#
# A copy, and so held to its source: tests/test_build_precheck.py compares
# each row with the protocol fingerprint's own derivation of what the handler
# reads. Methods absent here (exec, capture, annotate with its computed keys,
# and the read-only ones) are not checked — an unlisted method is left to the
# bridge rather than refused on a guess.
STEP_KEYS: dict[str, tuple[str, ...]] = {
    "op_create": ("connect", "name", "owner", "parent", "pars", "position",
                  "text", "type"),
    "op_delete": ("owner", "path", "paths"),
    "op_connect": ("from", "index", "owner", "to"),
    "op_disconnect": ("index", "owner", "path"),
    "par_set": ("owner", "pars", "path"),
    "palette_load": ("file", "name", "owner", "parent", "position"),
    "extension_add": ("class_name", "code", "extension_name", "index", "name",
                      "owner", "parent", "path", "position", "promote"),
    "flags_set": ("flags", "owner", "path"),
}

# Each entry of op_create's `connect` list is read as `wiring["from"]` and
# `wiring.get("index", 0)` — the same silent default, one level down.
CONNECT_KEYS = ("from", "index")

# Spellings agents reach for, mapped to the key that is read. Tried before the
# fuzzy match, which cannot see that `input` means `index`.
_KEY_ALIASES = {
    "input": "index", "input_index": "index", "inputindex": "index",
    "in": "index", "slot": "index", "connector": "index",
    "source": "from", "src": "from", "from_path": "from",
    "target": "to", "dest": "to", "destination": "to", "to_path": "to",
    "op_type": "type", "optype": "type", "operator": "type",
    "params": "pars", "parameters": "pars", "values": "pars",
    "pos": "position", "xy": "position",
    "contents": "text",
}


def _nearest_key(key: str, allowed: tuple[str, ...]) -> str | None:
    alias = _KEY_ALIASES.get(key.lower())
    if alias in allowed:
        return alias
    close = difflib.get_close_matches(key, allowed, n=1, cutoff=0.6)
    return close[0] if close else None


def _unknown_keys(where: str, given: dict, allowed: tuple[str, ...]) -> list[str]:
    out = []
    for key in given:
        if key in allowed:
            continue
        nearest = _nearest_key(str(key), allowed)
        advice = f"did you mean '{nearest}'? " if nearest else ""
        out.append(
            f"{where}: unknown key '{key}' — {advice}the bridge reads only "
            f"{', '.join(allowed)} and ignores anything else, so a misnamed "
            f"key silently becomes its default"
        )
    return out


def step_key_problems(operations: list[dict]) -> list[str]:
    """Keys in a batch that the bridge would ignore, each with the right one.

    Exists because ignoring is the failure: the handler reads what it knows
    with a default, so `input_index` instead of `index` wires input 0 and
    reports success.
    """
    problems: list[str] = []
    for i, step in enumerate(operations):
        if not isinstance(step, dict):
            problems.append(f"step {i}: expected an object, got {type(step).__name__}")
            continue
        method = step.get("method")
        stray = [k for k in step if k not in ("method", "params")]
        if stray:
            problems.append(
                f"step {i} ({method}): {', '.join(map(repr, stray))} outside "
                f"'params' — a step is {{\"method\": ..., \"params\": {{...}}}}, "
                f"and anything beside those two is never read"
            )
        allowed = STEP_KEYS.get(method)
        params = step.get("params") or {}
        if allowed is None or not isinstance(params, dict):
            continue
        problems += _unknown_keys(f"step {i} ({method})", params, allowed)
        if method == "op_create":
            for j, wiring in enumerate(params.get("connect") or []):
                if isinstance(wiring, dict):
                    problems += _unknown_keys(
                        f"step {i} (op_create) connect[{j}]", wiring, CONNECT_KEYS
                    )
    return problems


# -- OP references ------------------------------------------------------------

# How TouchDesigner resolves a relative path written into an OP-reference
# parameter. Measured on 2025.32460 (2026-09-24), in a sandbox holding geo1
# (geometryCOMP) and mat1 (constantMAT) side by side, geo1 holding innermat:
#   material='mat1'        -> /box/mat1
#   material='../mat1'     -> None
#   material='./innermat'  -> /box/geo1/innermat
#   material='innermat'    -> None
# and a Select TOP beside a noise n1: 'n1' -> /box/n1, '../n1' -> None,
# './n1' -> None. A bare name is a sibling, and '../' climbs out of the
# network the operator sits in — unlike Python's `geo1.op('../mat1')`, which
# is where agents learn the spelling.
#
# Only the '../' form is judged, since it is the one that means something else
# in Python. It is refused only when the path TouchDesigner will look up is
# known to be missing: reaching one level up is legitimate, and refusing a
# write TouchDesigner would resolve is worse than letting a wrong one through.

def op_reference_target(value: Any, owner: str) -> str | None:
    """The absolute path a '../'-relative OP reference resolves to, or None.

    None for anything this does not judge: not a string, not starting with
    '../', a pattern or a list of several operators (Render TOP's `geometry`
    takes 'geo*' or 'a b'), or an owner path that is not absolute.
    """
    if not isinstance(value, str) or not value.startswith("../"):
        return None
    if re.search(r"[\s*?\[\]^]", value) or not owner.startswith("/"):
        return None
    parent = posixpath.dirname(owner.rstrip("/")) or "/"
    return posixpath.normpath(posixpath.join(parent, value))


def op_reference_lookups(values: dict[str, Any], owner: str) -> set[str]:
    """Every path `op_reference_problem` will ask `exists` about, for one call.

    The target TouchDesigner will resolve, and the sibling the writer most
    likely meant, so the refusal can say whether that one is there.
    """
    wanted: set[str] = set()
    for value in values.values():
        target = op_reference_target(value, owner)
        if target:
            parent = posixpath.dirname(owner.rstrip("/")) or "/"
            wanted.add(target)
            wanted.add(posixpath.normpath(posixpath.join(parent, value[3:])))
    return wanted


def op_reference_problem(
    name: str, value: Any, owner: str, exists: Exists | None = None
) -> str | None:
    """Why a '../' OP reference will read back as None, or None if it will not.

    `exists` answers for the running project. Without it, or when it cannot
    say, nothing is refused.
    """
    target = op_reference_target(value, owner)
    if target is None or exists is None:
        return None
    if exists(target) is not False:
        return None
    parent = posixpath.dirname(owner.rstrip("/")) or "/"
    rest = value[len("../"):]
    sibling = posixpath.normpath(posixpath.join(parent, rest))
    there = " (which exists)" if exists(sibling) else ""
    return (
        f"'{value}' resolves to {target}, which does not exist. An OP-reference "
        f"parameter resolves from the network {owner} sits in, not from "
        f"{owner} itself, so '../' climbs out of {parent}. A sibling is "
        f"written '{rest}', or absolutely '{sibling}'{there}"
    )


# -- parameter values -----------------------------------------------------------

_TRAILING_INDEX = re.compile(r"^(?P<base>.*?\D)(?P<index>\d+)$")


def _tuple_siblings(
    name: str, settable: dict[str, dict]
) -> tuple[str, list[str]] | None:
    """The tuple a mistyped member name belongs to, and its known members.

    `amp5` on a noisePOP whose index knows amp0..amp3, or `tw` beside
    tx/ty/tz. A near-miss inside a tuple is told the tuple, not the nearest
    string: difflib offered `amp0` for `amp2`, which is another axis and not a
    typo (second agent report, item 4).
    """
    match = _TRAILING_INDEX.match(name)
    if match:
        base = match.group("base")
        members = sorted(
            (m for m in settable if re.fullmatch(re.escape(base) + r"\d+", m)),
            key=lambda m: int(m[len(base):]),
        )
        if members:
            return base, members
    by_group: dict[str, list[str]] = {}
    for member, row in settable.items():
        group = row.get("group_name")
        if group and group != member:
            by_group.setdefault(group, []).append(member)
    for cut in (1, 2):
        base = name[:-cut]
        if base and base in by_group:
            members = sorted(
                by_group[base], key=lambda m: settable[m].get("vec_index") or 0
            )
            return base, members
    return None


def _menu_position(row: dict, value: Any) -> int | None:
    names = row.get("menu_names") or []
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 <= value < len(names) else None
    if isinstance(value, str) and value in names:
        return names.index(value)
    return None


def _presence_problem(
    name: str, row: dict, values: dict, settable: dict, fresh: bool
) -> Problem | None:
    """A tuple member written where its tuple is too short for it to exist.

    The probe records, for the members a size menu adds, the first setting at
    which each appears ('parsize=3' for noisePOP's amp2). Below that size
    `par.amp2` is still there, and writing to it raises "Index out of range"
    (measured on 2025.32460), which inside a batch rolls back every step. So
    it is judged here, but only where the size is known: set in the same
    write, or a fresh operator's default.
    """
    rule = row.get("appears_when")
    if not rule or "=" not in rule:
        return None
    governor, _, needed = rule.partition("=")
    gov_row = settable.get(governor)
    if gov_row is None:
        return None
    if governor in values:
        # The handler applies `pars` in the order given, so a size set after
        # the member arrives too late for it.
        order = list(values)
        if order.index(governor) > order.index(name):
            return Problem(
                name,
                f"is written before {governor}, which it needs at '{needed}' "
                f"or later — pars are applied in order, and until then "
                f"TouchDesigner refuses the write with 'Index out of range'. "
                f"Put {governor} first",
                [f"{governor}='{needed}'"],
            )
        current = values[governor]
        if isinstance(current, dict):
            return None
    elif fresh:
        current = gov_row.get("default")
    else:
        return None
    have = _menu_position(gov_row, current)
    need = _menu_position(gov_row, needed)
    if have is None or need is None or have >= need:
        return None
    return Problem(
        name,
        f"exists only when {governor} is '{needed}' or later in its menu, and "
        f"here it is '{current}'; below that TouchDesigner refuses the write "
        f"with 'Index out of range'. Set {governor} first, in the same pars",
        [f"{governor}='{needed}'"],
    )


def validate_params(
    store: AtomStore,
    op_type: str,
    values: dict[str, Any],
    owner: str = "",
    exists: Exists | None = None,
    fresh: bool = False,
) -> Validation:
    """Check a {name: value} mapping against an operator's known schema.

    Returns cleanly when the operator has not been probed — without runtime
    facts there is nothing authoritative to check against, and a false
    rejection is worse than letting TouchDesigner answer.

    `owner` is the path of the operator the values are for, and `exists` asks
    the running project whether a path is there; together they let an OP
    reference be judged the way TouchDesigner will resolve it. `fresh` says the
    operator is created by this write, so what it is not given holds its
    default.
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
    # "Never probed" means no row carries a runtime style, not "nothing is
    # settable": since 2026-08-30 a parameter the probe missed still counts as
    # settable unless other rows sit under it as a group, so the old emptiness
    # check would no longer fire and an unprobed operator would be judged
    # against help text alone.
    if not any(r["style"] is not None for r in rows):
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
                continue
            siblings = _tuple_siblings(name, settable)
            if siblings:
                base, members = siblings
                result.problems.append(
                    Problem(
                        name,
                        f"no such parameter — '{base}' is a tuple, and these "
                        f"are the members the index knows",
                        members,
                    )
                )
                continue
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

        presence = _presence_problem(name, row, values, settable, fresh)
        if presence is not None:
            result.problems.append(presence)
            continue

        # Expressions, bindings and pulses bypass value checks: what they
        # evaluate to is only knowable inside TouchDesigner.
        if isinstance(value, dict):
            if not ({"expr", "bind", "pulse"} & set(value)):
                result.problems.append(
                    Problem(name, "object value needs one of expr/bind/pulse")
                )
            continue

        if row["is_op"] and owner:
            reason = op_reference_problem(name, value, owner, exists)
            if reason:
                result.problems.append(Problem(name, reason))
                continue

        # A StrMenu is a string with suggestions, not a closed menu: its
        # entries are what the dropdown offers, and any text is a legal value.
        # The index holds ["*"] for every CHOP's renamefrom/renameto, and
        # checking against that refused `renameto`, which TouchDesigner takes
        # (first agent report). A plain Menu stays closed.
        if row["is_menu"] and row["menu_names"] and row["style"] != "StrMenu":
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


