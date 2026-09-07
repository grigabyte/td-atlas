"""Serialising a loaded project to JSON an agent can read and diff.

`render.describe` is a summary for a human reading a terminal; this is the
whole network, losslessly, for an agent that has to reason about it — every
operator's type, position, flags, wiring, parameters and DAT contents.

Two choices here were settled by measurement (the numbers, and what the same
measurement ruled out, are in docs/formats.md under "The network text, and why
it is JSON") and are the reason this is not a two-line `json.dumps`:

- **Standard JSON, non-standard printer.** DAT text is emitted as an array of
  lines rather than one string with `\\n` escapes. Editing one line inside a
  DAT — the single most common real change — costs 57–123 bytes of diff this
  way against 1209–5985 for naive JSON, a factor of 20 to 180. Vectors, flag
  maps and short flat lists stay on one line, which is what keeps a node from
  spreading over thirty. The result is still ordinary JSON: `json.loads` reads
  it with no custom parser, and there is no parser here to drift out of step
  with the printer. Hand-written line-based and YAML formats were measured
  smaller and rejected for exactly that reason — both of their prototypes lost
  data (Table DAT cells, trailing spaces in parameter values) on real files.

- **Nested tree, inputs by sibling name.** The alternative — one flat map keyed
  by absolute path — makes renaming a node rewrite every line of its subtree
  (68 lines against 2 on a 35-operator component).

Node positions deliberately stay inside the node. A separate positions section
keyed by path was measured and rejected: dragging a node already costs 2 lines
because `tile` is one line, and the section would reintroduce the rename cost.

Parameter defaults are not filtered out, because there are almost none to
filter: `toeexpand` writes only differences into `.parm` already, and of 27,336
parameters in the shipped libraries just 554 (2.1%) equal their default.
"""

from __future__ import annotations

import json
from typing import Any

from .model import Node, Project

# A container is printed on one line when its compact form fits this many
# characters, measured on the rendering itself rather than the rendering plus
# its indent — otherwise a node's formatting would change with how deeply it
# happens to be nested, and moving a subtree would rewrite all of it.
_INLINE_LIMIT = 100

# Keys always printed one entry per line, whatever their length. These are the
# ones where a one-entry-per-line diff is the point: a changed line of DAT
# text, a changed parameter, a changed table row.
_BLOCK_KEYS = frozenset(
    {"text", "table", "parms", "custom_parms", "children", "operators"}
)


def split_text(text: str) -> list[str]:
    """Split DAT text into the array of lines that goes into the JSON.

    `str.split("\\n")` and not `splitlines()`: only the former is the exact
    inverse of `"\\n".join`. `splitlines()` drops the information about a
    trailing newline and additionally breaks on form feed, vertical tab and
    U+2028, none of which a DAT is guaranteed not to contain — every one of
    those is a silent, unrecoverable edit to somebody's shader.
    """
    return text.split("\n")


def join_text(lines: list[str]) -> str:
    """Reassemble DAT text from the array of lines. Inverse of `split_text`."""
    return "\n".join(lines)


def node_data(node: Node, children: bool = True) -> dict[str, Any]:
    """One operator as plain data, ready for the printer.

    Nothing here is inferred: every field is something `model.py` read out of
    the expanded files. The absolute path is deliberately absent — it is the
    concatenation of the enclosing names, and writing it into every node would
    put back the rename cost the nested shape was chosen to avoid: renaming one
    component would rewrite a `path` line for every operator beneath it.

    `saved_type` appears only when the index resolved the contraction stored in
    the file to a different canonical name, so a reader sees both without a
    second lookup.
    """
    file = node.node
    data: dict[str, Any] = {
        "name": node.name,
        "type": node.op_type,
        "family": node.family,
    }
    if file.op_type and file.op_type != node.op_type:
        data["saved_type"] = file.op_type
    data["tile"] = [file.x, file.y, file.width, file.height]
    data["flags"] = dict(sorted(file.flags.items()))
    data["color"] = list(file.color) if file.color is not None else None
    # Index and name, not name alone: inputs can have gaps (input 0 unwired
    # while input 2 is), and the index is what says which.
    data["inputs"] = [[index, source] for index, source in sorted(node.inputs)]
    data["parms"] = {name: _parm(value) for name, value in sorted(node.parms.items())}
    data["custom_parms"] = {
        name: _parm(value) for name, value in sorted(node.custom_parms.items())
    }
    # Only when the node has custom parameter pages, the way `saved_type`
    # appears only when it differs: an unconditional key would add a line to
    # every operator in every file for the sake of the few that have one.
    if node.custom_pages:
        data["custom_pages"] = list(node.custom_pages)
    data["text"] = split_text(node.text) if node.text is not None else None
    data["table"] = node.table
    if children:
        data["children"] = [node_data(child) for child in node.children]
    return data


def _parm(value) -> Any:
    """A parameter as a bare string, or a map when the line carries more.

    The constant is kept alongside the expression because the file keeps it:
    it is the value the parameter falls back to when the expression is turned
    off, and discarding it would make the serialisation lossy. The same holds
    for a bind expression, which names the parameter's bind master.

    Every half the reader recovered appears under its own key. Nothing is ever
    concatenated into `value`, and a line whose layout the reader did not
    recognise emits `unrecognised` with the remainder verbatim — a reassembler
    has to see that it is not looking at a plain constant.
    """
    out: dict[str, Any] = {}
    # `is not None`, not truthiness: an expression the file stores as the
    # empty string is a stored expression, and dropping the key would tell a
    # reassembler the parameter is a plain constant.
    if value.is_expression and value.expr is not None:
        out["expr"] = value.expr
    if value.is_bind and value.bind is not None:
        out["bind"] = value.bind
    if value.unrecognised is not None:
        out["unrecognised"] = value.unrecognised
    if not out:
        return value.value
    out["value"] = value.value
    return out


def project_data(project: Project, path: str | None = None) -> dict[str, Any]:
    """A whole project — or one subtree — as plain data."""
    if path:
        node = project.find(path)
        if node is None:
            available = ", ".join(sorted(n.path for n in project.roots))
            raise LookupError(f"no node at '{path}'. Top level: {available}")
        roots = [node]
    else:
        roots = list(project.roots)

    return {
        "source": project.source.name,
        "build": dict(sorted(project.build.items())),
        "path": path or "/",
        "operator_count": sum(len(list(root.walk())) for root in roots),
        "operators": [node_data(root) for root in roots],
    }


def _scalar(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _inline(value: Any) -> str | None:
    """The compact one-line form, when it is short enough to use."""
    text = json.dumps(value, ensure_ascii=False, separators=(", ", ": "))
    if len(text) <= _INLINE_LIMIT and "\n" not in text:
        return text
    return None


def _emit(value: Any, level: int, key: str | None, out: list[str]) -> None:
    pad = "  " * level
    if not isinstance(value, (dict, list)):
        out.append(_scalar(value))
        return

    if not value:
        out.append("{}" if isinstance(value, dict) else "[]")
        return

    if key not in _BLOCK_KEYS:
        compact = _inline(value)
        if compact is not None:
            out.append(compact)
            return

    if isinstance(value, dict):
        out.append("{\n")
        items = list(value.items())
        for index, (name, item) in enumerate(items):
            out.append(f"{pad}  {_scalar(name)}: ")
            _emit(item, level + 1, name, out)
            out.append(",\n" if index < len(items) - 1 else "\n")
        out.append(pad + "}")
        return

    out.append("[\n")
    for index, item in enumerate(value):
        out.append(pad + "  ")
        # A list's items inherit no key, so an array of DAT lines stays one
        # line per entry while the rows of a table are each tried inline.
        _emit(item, level + 1, None, out)
        out.append(",\n" if index < len(value) - 1 else "\n")
    out.append(pad + "]")


def dumps(data: Any) -> str:
    """Print plain data as JSON with this module's line policy."""
    out: list[str] = []
    _emit(data, 0, None, out)
    out.append("\n")
    return "".join(out)


def to_text(project: Project, path: str | None = None) -> str:
    """A project as readable JSON. Raises LookupError for an unknown path."""
    return dumps(project_data(project, path=path))
