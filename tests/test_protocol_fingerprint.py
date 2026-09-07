"""`PROTOCOL_VERSION` must move when the method table moves.

The bridge component runs inside TouchDesigner and outlives an upgrade of the
host package, so the two halves only stay in step because each side states a
number and refuses the other when they disagree. That check is worth nothing
if the number is left behind by an edit to the table it describes — which has
now happened twice, once for a removed method and once for an added parameter,
and both times the stale number named two different protocols.

So the table gets a witness. This module derives, from the source, a listing
of every bridge method and the parameter keys it reads, and compares it with
the listing recorded here against the current `PROTOCOL_VERSION`:

- edit the table without moving the number and the two listings differ;
- move the number without recording a listing and the lookup fails.

Two blind spots, stated rather than papered over:

- **Response shape.** A method that starts returning a differently shaped
  reply is a protocol change this cannot see, because the shape is built up
  across a function body rather than declared. What holds that half is the
  rule in `AGENTS.md`, the `CHANGELOG.md` obligation on every protocol change
  (decision 51) and review.
- **Parameter names read through a variable.** They are recorded as
  `<dynamic>` unless the variable is a loop target over a module-level literal
  table, which is the one such pattern in the handler today.

The version keys the table below are labels for recorded listings, not a
second copy of the constant: the current one is imported (decision 8) and used
to look a listing up.
"""

from __future__ import annotations

import ast
import pathlib

from td_atlas.bridge.client import (
    EXPECTED_PROTOCOL_VERSION,
    MIN_PROTOCOL_VERSION,
)
from td_atlas.component import handler

HANDLER = pathlib.Path(handler.__file__)

# One line per bridge method: its wire name, then every parameter key the
# method (and the module-level helpers it calls) reads, sorted. Regenerate with
# `python -c "import tests.test_protocol_fingerprint as t; print(t.derive())"`
# from the repository root, and only together with a new PROTOCOL_VERSION.
RECORDED = {
    7: """\
annotate: <dynamic> alpha color font_size mode name owner parent path position size text title
annotations: depth path
batch: ops owner undo_name
capture: path reset
claim_scope: owner path ttl
contact_sheet: columns width
errors: path
exec: code
extension_add: class_name code extension_name index name owner parent path position promote
flags: path paths
flags_set: flags owner path
health_sample: path
network: depth pars path
op_connect: from index owner to
op_create: connect name owner parent pars position text type
op_delete: owner path paths
op_disconnect: index owner path
op_info: pars path
op_types: paths
palette_load: file name owner parent position
par_set: owner pars path
ping: -
redo: -
release_scope: owner path
render: format height path width
save_tox: file path
scopes: -
status_note: health
undo: -""",
}


def _module_functions(tree: ast.Module) -> dict[str, ast.FunctionDef]:
    return {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }


def _literal_strings(node: ast.AST, position: int | None) -> set[str]:
    """String constants in a literal tuple/list, optionally one column of it.

    `for field, par_name in _ANNOTATE_TEXT_PARS` reads its parameter names out
    of column 0 of a table of pairs; column 1 holds TouchDesigner parameter
    names, which are not part of this protocol.
    """
    if not isinstance(node, (ast.Tuple, ast.List)):
        return set()
    found: set[str] = set()
    for element in node.elts:
        if isinstance(element, ast.Constant) and isinstance(element.value, str):
            if position in (None, 0):
                found.add(element.value)
        elif isinstance(element, (ast.Tuple, ast.List)):
            column = element.elts[position] if position is not None else None
            targets = element.elts if column is None else [column]
            found |= {
                item.value
                for item in targets
                if isinstance(item, ast.Constant) and isinstance(item.value, str)
            }
    return found


def _loop_sources(tree: ast.Module, node: ast.AST) -> dict[str, set[str]]:
    """Map each `for` target name inside `node` to the strings it can take."""
    constants = {
        target.id: statement.value
        for statement in tree.body
        if isinstance(statement, ast.Assign)
        for target in statement.targets
        if isinstance(target, ast.Name)
    }
    resolved: dict[str, set[str]] = {}
    for sub in ast.walk(node):
        if not isinstance(sub, ast.For):
            continue
        source = sub.iter
        if isinstance(source, ast.Name):
            source = constants.get(source.id)
        if source is None:
            continue
        if isinstance(sub.target, ast.Name):
            resolved.setdefault(sub.target.id, set()).update(
                _literal_strings(source, None)
            )
        elif isinstance(sub.target, (ast.Tuple, ast.List)):
            for position, element in enumerate(sub.target.elts):
                if isinstance(element, ast.Name):
                    resolved.setdefault(element.id, set()).update(
                        _literal_strings(source, position)
                    )
    return resolved


def _key(node: ast.AST, loops: dict[str, set[str]]) -> set[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, ast.Name) and loops.get(node.id):
        return set(loops[node.id]) | {"<dynamic>"}
    return {"<dynamic>"}


def _params_read(tree: ast.Module, function: ast.FunctionDef) -> set[str]:
    """Every key this function body pulls out of its `params` mapping."""
    loops = _loop_sources(tree, function)
    keys: set[str] = set()
    for sub in ast.walk(function):
        if (
            isinstance(sub, ast.Call)
            and isinstance(sub.func, ast.Attribute)
            and sub.func.attr in ("get", "pop")
            and isinstance(sub.func.value, ast.Name)
            and sub.func.value.id == "params"
            and sub.args
        ):
            keys |= _key(sub.args[0], loops)
        elif (
            isinstance(sub, ast.Subscript)
            and isinstance(sub.value, ast.Name)
            and sub.value.id == "params"
        ):
            keys |= _key(sub.slice, loops)
        elif (
            isinstance(sub, ast.Compare)
            and len(sub.comparators) == 1
            and isinstance(sub.comparators[0], ast.Name)
            and sub.comparators[0].id == "params"
            and any(isinstance(op, (ast.In, ast.NotIn)) for op in sub.ops)
        ):
            keys |= _key(sub.left, loops)
    return keys


def _calls(function: ast.FunctionDef, known: dict[str, ast.FunctionDef]) -> set[str]:
    return {
        sub.func.id
        for sub in ast.walk(function)
        if isinstance(sub, ast.Call)
        and isinstance(sub.func, ast.Name)
        and sub.func.id in known
    }


def derive() -> str:
    """The listing as the source has it now.

    A method's parameters are read inside its own body and inside the helpers
    it calls — `m_annotate` hands its whole `params` to `_annotate_apply` —
    so the walk follows calls between module-level functions rather than
    stopping at the entry point.
    """
    tree = ast.parse(HANDLER.read_text(encoding="utf-8"))
    functions = _module_functions(tree)
    lines = []
    for name in sorted(handler.METHODS):
        entry = handler.METHODS[name].__name__
        keys: set[str] = set()
        seen: set[str] = set()
        pending = [entry]
        while pending:
            current = pending.pop()
            if current in seen or current not in functions:
                continue
            seen.add(current)
            keys |= _params_read(tree, functions[current])
            pending.extend(_calls(functions[current], functions) - seen)
        lines.append(f"{name}: {' '.join(sorted(keys)) or '-'}")
    return "\n".join(lines)


def test_the_method_table_matches_the_listing_recorded_for_this_version():
    recorded = RECORDED.get(EXPECTED_PROTOCOL_VERSION)
    assert recorded is not None, (
        f"PROTOCOL_VERSION is {EXPECTED_PROTOCOL_VERSION} and no listing is "
        f"recorded for it. Run `derive()` in this module and add its output to "
        f"RECORDED, so the next change to the method table has something to "
        f"differ from."
    )
    assert derive() == recorded, (
        "The bridge method table changed without moving PROTOCOL_VERSION. "
        "Which methods exist, and which parameters each reads, is the wire "
        "between the host and a bridge component that outlives an upgrade of "
        "this package — see the rule in AGENTS.md. Raise PROTOCOL_VERSION in "
        "src/td_atlas/component/handler.py, follow it with "
        "MIN_PROTOCOL_VERSION, record the new listing here, and write the "
        "change up in CHANGELOG.md."
    )


def test_the_minimum_follows_the_expected_version():
    """No version back is supported (decision 41), so the two move together."""
    assert MIN_PROTOCOL_VERSION == EXPECTED_PROTOCOL_VERSION


def test_the_listing_covers_every_method_in_the_table():
    """A fingerprint that silently skipped a method would hold nothing."""
    named = {line.split(":", 1)[0] for line in derive().splitlines()}
    assert named == set(handler.METHODS)
