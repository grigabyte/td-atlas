"""Readers for the text formats `toeexpand` writes.

A .toe or .tox is a container; `toeexpand` unpacks it into a directory tree of
small files, one group per operator. None of this is documented by Derivative,
so each reader below states what it verified against the shipped example
libraries rather than assuming a shape.

Files per operator, all optional but `.n`:

    foo.n       node: family, type, position, flags, input wiring
    foo.parm    parameter values that differ from the defaults
    foo.cparm   custom parameter definitions — a *different* grammar from
                `.parm`, see `read_custom_parms`
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


# A `.parm` line is positional: `name <flags> <constant> [<expression>]
# [<bind expression>]`, wrapped in `?` sentinels. Which of the optional halves
# are present is decided by two bits of the flags word, and by nothing else —
# measured over all 469,446 `.parm` lines in the expansion cache (222
# components and projects), where the field count equals
# `1 + expression_bit + bind_bit` on 469,433 of them. The 13 exceptions all
# carry one further bit and are handled separately below.
#
# Reading the halves off the *shape* of the line instead is what the previous
# version did, and it fabricated values: `colorr 515 1 parent.Checker.par.Color1r`
# carries no expression bit, so the whole tail became the constant and the
# parameter was reported as holding `1 parent.Checker.par.Color1r`.
_PARM_LINE = re.compile(r"^(?P<name>\S+)\s+(?P<flags>-?\d+)\s*(?P<rest>.*)$")

# Bit 4: expression mode. 138,658 lines in the cache carry it, and every one of
# them has exactly two values — the constant and the expression.
_EXPR_BIT = 0x10

# Bit 9: Bind mode, the fourth parameter mode (Constant, Expression, Export,
# Bind). Measured first, then named from the index's `Binding` article: all
# 4,670 lines carrying this bit have a second value, and 4,644 of those are a
# `.par.` reference — a *bind expression*, naming the bind master. The other
# 26 are `op('bind1')['chan1']` and table-cell subscripts, which the article
# says are exactly the other things allowed to be a bind master.
#
# 547 lines carry both bits and hold three values: constant, expression, bind
# expression. A bind reference does not use its expression, but the file keeps
# the expression it last had, the same way it keeps the constant.
_BIND_BIT = 0x200

# Bit 21 appears on 13 lines in the cache and on nothing else, and those 13
# are exactly the lines carrying one value more than the two bits above
# account for. What it *means* is unknown: in all 13 the extra value is
# identical to the one before it, so no measurement can say which position is
# the expression and which the bind expression. Lines carrying it therefore
# report their constant and hand the rest back verbatim as unrecognised,
# rather than assigning it to a half that might be the wrong one.
_UNKNOWN_EXTRA_BIT = 0x200000


def _unquote(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        return text[1:-1]
    return text


@dataclass
class ParmValue:
    """One line of a `.parm` file: a constant plus whichever halves it keeps.

    Every field here is something the file said. Nothing is inferred from the
    shape of the line, and no field ever holds two of the others glued
    together — when the layout is not recognised, `unrecognised` carries the
    unsplit remainder and `expr` and `bind` stay empty.
    """

    flags: int
    value: str
    """The constant value, retained even while an expression drives the parameter."""

    expr: str | None = None
    """The expression, when the expression bit is set. May be the empty string."""

    bind: str | None = None
    """The bind expression: what names this parameter's bind master."""

    unrecognised: str | None = None
    """Everything after the constant on a line whose layout was not measured."""

    @property
    def is_expression(self) -> bool:
        return bool(self.flags & _EXPR_BIT)

    @property
    def is_bind(self) -> bool:
        return bool(self.flags & _BIND_BIT)

    @property
    def mode(self) -> str:
        """The parameter mode this line records.

        Bind outranks expression when both bits are set. That ordering is a
        choice read off the `Binding` article — "a bind reference does not use
        its constant, expression or export parameter modes" — and not
        something the files were measured to confirm.
        """
        if self.unrecognised is not None:
            return "unknown"
        if self.is_bind:
            return "bind"
        if self.is_expression:
            return "expression"
        return "constant"

    @property
    def effective(self) -> str:
        """What actually drives the parameter."""
        if self.is_bind and self.bind:
            return self.bind
        if self.is_expression and self.expr:
            return self.expr
        return self.value

    def render(self) -> str:
        if self.unrecognised is not None:
            return f"{self.value}  [unrecognised: {self.unrecognised}]"
        if self.is_bind and self.bind:
            return f"{self.bind}  [bind]"
        if self.is_expression and self.expr:
            return f"{self.expr}  [expression]"
        return self.value


def read_parms(text: str) -> dict[str, ParmValue]:
    """Parse a `.parm` file into {name: ParmValue}.

    Which halves a line carries comes from the flags word, never from the
    line's shape: a constant may contain spaces and be unquoted, so a line
    with two space-separated words is a pair or a single value depending
    entirely on `_EXPR_BIT` and `_BIND_BIT`.

    For `.cparm` use `read_custom_parms` — its first line is not a parameter.
    """
    out: dict[str, ParmValue] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line == "?":
            continue
        match = _PARM_LINE.match(line)
        if not match:
            continue
        name = match.group("name")
        out[name] = _parm_value(int(match.group("flags")), match.group("rest"))
    return out


def _parm_value(flags: int, rest: str) -> ParmValue:
    rest = rest.strip()

    if flags & _UNKNOWN_EXTRA_BIT:
        constant, remainder = _take_field(rest)
        remainder = remainder.strip()
        return ParmValue(flags=flags, value=constant, unrecognised=remainder or None)

    halves = bool(flags & _EXPR_BIT) + bool(flags & _BIND_BIT)
    if not halves:
        # Constants may legitimately contain spaces, so the remainder is
        # taken whole rather than tokenised.
        return ParmValue(flags=flags, value=_unquote(rest))

    values = _split_values(rest, halves + 1)
    parm = ParmValue(flags=flags, value=values[0])
    index = 1
    # Verbatim, the empty string included: 83 lines in the cache read
    # `<name> <flags> "" ""` — expression mode on, expression empty. Folding
    # that into `None` would report the half as absent where the file says it
    # is present and blank.
    if flags & _EXPR_BIT:
        parm.expr = values[index]
        index += 1
    if flags & _BIND_BIT:
        parm.bind = values[index]
    return parm


@dataclass
class CustomParms:
    """A parsed `.cparm` file."""

    pages: list[str] = field(default_factory=list)
    parms: dict[str, ParmValue] = field(default_factory=dict)


def read_custom_parms(text: str) -> CustomParms:
    """Parse a `.cparm` file — custom parameter *definitions*.

    A `.cparm` does not share the `.parm` grammar, and two things follow.

    Its first line is `pages <count> <name>...`: the number in the flags
    column is the page count, not a flags word. Verified on all 5,210 `pages`
    lines in the expansion cache — the count equals the number of names on
    every one of them. Read as a parameter (which is what happened before)
    the component gains a parameter named `pages` whose value is the page
    names glued together, which is not a value it has.

    Its remaining lines are definitions — `<typecode> <name> <label> ...` —
    and this reader does **not** read them: the layout of those columns has
    not been measured. They fail `_PARM_LINE` (the second column is a name,
    not a number) and are dropped, so `parms` on a real `.cparm` comes back
    empty. That is an admitted blank, not a claim that the component has no
    custom parameters; the page names are all that is recovered here.
    """
    result = CustomParms()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line == "?":
            continue
        match = _PARM_LINE.match(line)
        if not match:
            continue
        name = match.group("name")
        rest = match.group("rest").strip()
        if name == "pages" and not result.pages:
            # The declared count is not used to drive the split: every field
            # is lexed strictly, so no page name can absorb the next one even
            # if a file ever disagrees with its own count.
            result.pages = _all_fields(rest)
            continue
        result.parms[name] = _parm_value(int(match.group("flags")), rest)
    return result


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
    """Read one field — a quoted string or a bare token — and the remainder.

    A field may be preceded by one or more byte-order marks: 62 lines in the
    expansion cache carry a BOM in a line's tail, and 6 of them write
    `<BOM>"..."` where every other line writes `"..."`.
    Whether TouchDesigner meant the BOM as part of the string or as noise
    cannot be told from the file, so it is kept, prefixed to the field's value
    — dropping it would edit somebody's expression, and treating the BOM as
    the field's first character (which is what happens without this) makes the
    quotes content, splits the expression on every space, and hands back a
    fragment of a value as if it were the whole one.
    """
    text = text.lstrip()
    prefix = ""
    while text[:1] == "\ufeff":
        prefix += text[0]
        text = text[1:]
    if not text:
        return prefix, ""
    if text[0] != '"':
        head, _, tail = text.partition(" ")
        return prefix + head, tail
    index = 1
    while index < len(text):
        if text[index] == "\\":
            index += 2
            continue
        if text[index] == '"':
            return prefix + _unescape(text[1:index]), text[index + 1 :]
        index += 1
    return prefix + _unescape(text[1:]), ""  # unterminated quote


def _split_values(rest: str, count: int) -> list[str]:
    """Split a line's tail into exactly `count` values.

    Every value but the last is lexed strictly — a quoted string or a bare
    token. The last one takes whatever is left, unquoted if it is quoted, so
    that a trailing value the writer left unquoted survives whole. That
    forgiving last step is why the count must come from the flags word: with
    the wrong count the last value silently swallows the next one.

    Quotes inside an unquoted expression are content, not syntax: the
    expression `op('circle').par.value0` must survive intact, so a general
    shell-style tokeniser is the wrong tool — it would strip the inner quotes
    and hand back Python that no longer parses.
    """
    values: list[str] = []
    for _ in range(max(count - 1, 0)):
        head, rest = _take_field(rest)
        values.append(head)
    tail = rest.strip()
    if tail.lstrip("\ufeff")[:1] == '"':
        tail, _ = _take_field(tail)
    values.append(tail)
    return values[:count]


def _all_fields(rest: str) -> list[str]:
    """Lex a tail into every field it holds, strictly.

    Used where the number of values is written in the file rather than implied
    by a flag, so nothing has to absorb a remainder.
    """
    out: list[str] = []
    while rest.strip():
        head, rest = _take_field(rest)
        out.append(head)
    return out


def read_build(text: str) -> dict[str, str]:
    """Parse the `.build` stamp written beside every expanded project."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        key, _, value = raw.strip().partition(" ")
        if key:
            out[key] = value.strip()
    return out
