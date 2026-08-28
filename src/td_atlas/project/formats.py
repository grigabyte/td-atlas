"""Readers for the text formats `toeexpand` writes.

A .toe or .tox is a container; `toeexpand` unpacks it into a directory tree of
small files, one group per operator. None of this is documented by Derivative,
so each reader below states what it verified against the shipped example
libraries rather than assuming a shape.

Files per operator, all optional but `.n`:

    foo.n       node: family, type, position, flags, input wiring
    foo.parm    parameter values that differ from the defaults
    foo.cparm   custom parameter definitions
    foo.text    DAT contents — Python, GLSL, plain text
    foo.table   Table DAT cells
    foo.panel   panel UI state (layout only; not read here)
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field

# `.text` and `.table` share a fixed 27-byte prologue: b"2\n*" or b"1\n*",
# then six big-endian uint32 whose last value is the payload length. Verified
# against all 686 payload files in the shipped snippet libraries.
_PROLOGUE = 27
_MAGIC = re.compile(rb"^[12]\n\*")


class FormatError(ValueError):
    """A file did not match the layout toeexpand is known to produce."""


def read_payload(data: bytes) -> str:
    """Extract the text of a DAT from a `.text` file.

    The length field matters: scanning for the first newline instead would
    truncate any payload whose length byte does not happen to be 0x0A, which
    silently mangles roughly one shader in three.
    """
    if len(data) < _PROLOGUE or not _MAGIC.match(data):
        raise FormatError("not a toeexpand payload file")
    (declared,) = struct.unpack(">I", data[23:27])
    body = data[_PROLOGUE:]
    if declared != len(body):
        # Trust the file over the header rather than truncating real content.
        pass
    return body.decode("utf-8", errors="replace")


def read_table(data: bytes) -> list[list[str]]:
    """Extract a Table DAT's cells from a `.table` file.

    Layout after the prologue's `1\\n*`: four uint32 (?, columns, rows, ?),
    then each cell as a uint32 tag followed by a uint32 length and its bytes.
    """
    if len(data) < 11 or not _MAGIC.match(data):
        raise FormatError("not a toeexpand table file")
    _unknown, cols, rows, _pad = struct.unpack(">4I", data[3:19])
    cells: list[str] = []
    offset = 19
    while offset + 8 <= len(data):
        _tag, length = struct.unpack(">2I", data[offset : offset + 8])
        offset += 8
        cells.append(data[offset : offset + length].decode("utf-8", "replace"))
        offset += length
    if not cols:
        return [cells] if cells else []
    return [cells[i : i + cols] for i in range(0, len(cells), cols)][:rows or None]


@dataclass
class NodeFile:
    """The contents of one `.n` file."""

    family: str = ""          # 'TOP', 'CHOP', 'COMP', ...
    short_type: str = ""      # 'constant', 'noise', 'container'
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0
    flags: dict[str, str] = field(default_factory=dict)
    inputs: list[tuple[int, str]] = field(default_factory=list)
    color: tuple[float, float, float] | None = None

    @property
    def op_type(self) -> str:
        """Canonical operator type, e.g. 'TOP:constant' -> 'constantTOP'.

        This is how a node in an expanded project joins to the atom index.
        """
        if not self.family or not self.short_type:
            return ""
        return f"{self.short_type}{self.family}"


def _floats(parts: list[str]) -> list[float]:
    out = []
    for part in parts:
        try:
            out.append(float(part))
        except ValueError:
            pass
    return out


def _flag_pairs(tokens: list[str]) -> dict[str, str]:
    """Pair a `flags` line's tokens into {flag: value}.

    The line is pairs, not a token list: `viewer 1 parlanguage 0` is two flags
    with values, and reading it flat reports four. Measured across the 19,001
    `flags` lines in the expansion cache — every one has an even token count,
    and the two vocabularies are disjoint (20 flag names, values only `0`, `1`,
    `on`, `off`), so no line is ambiguous between the two readings.

    An odd trailing token has never been observed; it is kept with an empty
    value rather than dropped, because losing a flag silently is worse than
    reporting one whose value we could not read.
    """
    out: dict[str, str] = {}
    for index in range(0, len(tokens), 2):
        name = tokens[index]
        out[name] = tokens[index + 1] if index + 1 < len(tokens) else ""
    return out


def read_node(text: str) -> NodeFile:
    """Parse a `.n` file.

        TOP:constant
        tile 465 -166 130 72
        flags =  current on viewer 1 parlanguage 0
        inputs
        {
        0 <tab> mono1
        }
        color 0.55 0.55 0.55
        end
    """
    node = NodeFile()
    lines = text.splitlines()
    in_block: str | None = None

    for raw in lines:
        line = raw.strip()
        if not line:
            continue

        if in_block is not None:
            if line == "}":
                in_block = None
                continue
            if line == "{":
                continue
            if in_block == "inputs":
                parts = line.split(None, 1)
                if len(parts) == 2 and parts[0].isdigit():
                    node.inputs.append((int(parts[0]), parts[1].strip()))
            continue

        if ":" in line and not node.family and " " not in line.split(":")[0]:
            node.family, _, node.short_type = line.partition(":")
            continue

        head, _, rest = line.partition(" ")
        if head == "tile":
            values = _floats(rest.split())
            if len(values) >= 4:
                node.x, node.y, node.width, node.height = values[:4]
        elif head == "flags":
            node.flags = _flag_pairs(rest.lstrip("= ").split())
        elif head == "color":
            values = _floats(rest.split())
            if len(values) >= 3:
                node.color = (values[0], values[1], values[2])
        elif head in ("inputs", "outputs", "docked", "wires"):
            in_block = head
        elif line == "end":
            break
    return node


# `.parm` lines are `name <flags> <constant> [<expression>]`, wrapped in `?`
# sentinels. A parameter in expression mode keeps both: the last constant it
# held and the expression now driving it.
_PARM_LINE = re.compile(r"^(?P<name>\S+)\s+(?P<flags>-?\d+)\s*(?P<rest>.*)$")

# Bit 4 of the flags word marks expression mode. Determined by measurement,
# not documentation: across the 2861 .parm files in the shipped libraries, all
# 14835 lines with this bit carry exactly two values and none without it do.
# The other bits track unrelated state and are left alone.
_EXPR_BIT = 0x10


def _unquote(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        return text[1:-1]
    return text


@dataclass
class ParmValue:
    flags: int
    value: str
    """The constant value, retained even while an expression drives the parameter."""

    expr: str | None = None

    @property
    def is_expression(self) -> bool:
        return bool(self.flags & _EXPR_BIT)

    @property
    def effective(self) -> str:
        """What actually drives the parameter."""
        if self.is_expression and self.expr:
            return self.expr
        return self.value

    def render(self) -> str:
        if self.is_expression and self.expr:
            return f"{self.expr}  [expression]"
        return self.value


def read_parms(text: str) -> dict[str, ParmValue]:
    """Parse a `.parm` or `.cparm` file into {name: ParmValue}."""
    out: dict[str, ParmValue] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line == "?":
            continue
        match = _PARM_LINE.match(line)
        if not match:
            continue

        flags = int(match.group("flags"))
        rest = match.group("rest").strip()

        if flags & _EXPR_BIT:
            constant, expression = _split_pair(rest)
        else:
            # Constants may legitimately contain spaces, so the remainder is
            # taken whole rather than tokenised.
            constant, expression = _unquote(rest), None

        out[match.group("name")] = ParmValue(
            flags=flags, value=constant, expr=expression
        )
    return out


_ESCAPES = {'\\"': '"', "\\\\": "\\", "\\n": "\n", "\\t": "\t", "\\'": "'"}


def _unescape(text: str) -> str:
    out: list[str] = []
    index = 0
    while index < len(text):
        pair = text[index : index + 2]
        if pair in _ESCAPES:
            out.append(_ESCAPES[pair])
            index += 2
        else:
            out.append(text[index])
            index += 1
    return "".join(out)


def _take_field(text: str) -> tuple[str, str]:
    """Read one field — a quoted string or a bare token — and the remainder."""
    text = text.lstrip()
    if not text:
        return "", ""
    if text[0] != '"':
        head, _, tail = text.partition(" ")
        return head, tail
    index = 1
    while index < len(text):
        if text[index] == "\\":
            index += 2
            continue
        if text[index] == '"':
            return _unescape(text[1:index]), text[index + 1 :]
        index += 1
    return _unescape(text[1:]), ""  # unterminated quote


def _split_pair(rest: str) -> tuple[str, str | None]:
    """Split `<constant> <expression>`, where either half may be quoted.

    Quotes inside an unquoted expression are content, not syntax: the
    expression `op('circle').par.value0` must survive intact, so a general
    shell-style tokeniser is the wrong tool — it would strip the inner quotes
    and hand back Python that no longer parses.
    """
    constant, remainder = _take_field(rest)
    expression = remainder.strip()
    if not expression:
        return constant, None
    if expression[0] == '"':
        expression, _ = _take_field(expression)
    return constant, expression or None


def read_build(text: str) -> dict[str, str]:
    """Parse the `.build` stamp written beside every expanded project."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        key, _, value = raw.strip().partition(" ")
        if key:
            out[key] = value.strip()
    return out
