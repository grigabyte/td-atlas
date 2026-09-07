"""Writing a network back: JSON text -> the tree `toeexpand` made -> `collapse()`.

`serialize.py` prints a network as text; this is the return leg. The gate it
exists to satisfy is the round-trip invariant `AGENTS.md` states under
"Invariants a change has to keep" — dump to text, build back, dump again, and
the two texts are identical.

**This is a patcher, not a generator, and that is a measured decision.** A
`toeexpand` tree holds 40+ kinds of file (measured over the 222 expansions in
this project's cache: 50,290 `.n`, 45,416 `.parm`, 11,593 `.panel`, 9,363
`.text`, 5,210 `.cparm`, 2,336 `.table`, and 614 `.gnode`, 605 `.chop`, 512
`.ts`, 488 `.network` and thirty more). The text representation covers five of
them, and only parts of those. Building a tree from text alone would therefore
delete panel layouts, replicator settings, CHOP caches and custom parameter
definitions without saying so. So the source expansion is copied and only what
the text actually describes is overwritten; every other byte is carried across
untouched.

Two consequences, both of them limitations that have to be said out loud
rather than discovered later:

- **Rebuilding needs the original file.** There is no path from text alone to
  a `.toe`. Plan items 16 (variants) and 19 (write text on save) both work
  against a source file that is present, so this is not a blocker for them,
  but it is a boundary on what the text is: a *diffable view with an
  edit-back path*, not an independent source of truth.
- **The flags word of a parameter comes from the source `.parm`.** The text
  records the mode through its keys (`expr`, `bind`, `unrecognised`) and never
  the 32-bit word, so bits beyond `0x10` and `0x200` are unrecoverable from
  text. Reading them back off the source line is exact. For a parameter the
  text adds that the source does not have, the word is *composed* from the
  keys — `0`, plus `0x10` for `expr`, plus `0x200` for `bind` — and that is an
  estimate, not a measurement: any other bit the parameter should have carried
  is lost. `Changes.estimated_flags` counts those lines so a caller can see it.

The second measured trap this module has to answer is an asymmetry in the
reader: the same quoted constant is read two different ways depending on the
flags word. With no expression or bind bit, `formats._unquote` strips the outer
quotes and leaves `\\"` alone; with either bit — or with the `0x200000` bit —
`formats._take_field` also unescapes, so `\\"` becomes `"`. 55 lines in the
cache are affected (`Bodytext` in `alembicoutPOP/example2/comment1.parm` is
one). The renderers below close it by construction: each one keys its quoting
and escaping off exactly the bits the reader keys its splitting off, so
`read(render(read(line))) == read(line)` whichever branch the line takes. That
equality is checked over every `.parm` line in the cache, not argued.
"""

from __future__ import annotations

import shutil
import struct
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import formats
from .expand import (
    WORK_PREFIX,
    Expansion,
    ExpandError,
    cache_dir,
    collapse,
    expand,
)
from .formats import NodeFile, ParmValue
from .model import Node, Project, TypeResolver, load
from .serialize import join_text

# ---------------------------------------------------------------------------
# Line renderers: the exact inverses of the readers in formats.py
# ---------------------------------------------------------------------------

# `formats._unescape` turns these pairs into single characters. Only the two
# structural ones are ever produced going the other way: a real newline or tab
# cannot occur inside a line that was read with `splitlines()`, and `\\'` is an
# accepted spelling of a character that needs no escape.
_ESCAPE = str.maketrans({"\\": "\\\\", '"': '\\"'})

_BOM = "﻿"


def _needs_quotes(value: str) -> bool:
    """Whether a strictly-lexed field has to be quoted to survive `_take_field`.

    A bare field ends at the first space and is returned verbatim, so anything
    holding whitespace, opening with a quote (which would start a quoted
    field), opening with a byte-order mark (which `_take_field` peels off as a
    prefix) or empty (which would vanish) has to be quoted instead.
    """
    if not value:
        return True
    if value[0] in ('"', _BOM):
        return True
    return any(character.isspace() for character in value)


def render_field(value: str) -> str:
    """One strictly-lexed field, as `formats._take_field` will read it back."""
    if _needs_quotes(value):
        return '"' + value.translate(_ESCAPE) + '"'
    return value


def render_last_field(value: str) -> str:
    """The final value on a `.parm` line.

    `formats._split_values` treats the tail differently from the fields before
    it: it takes whatever is left, and only lexes it as a quoted string when it
    already starts with a quote. So an unquoted tail survives whole — spaces
    included — and only a value that would be mistaken for a quoted string, or
    one whose own whitespace the reader's `strip()` would eat, needs quoting.
    """
    if not value or value.strip() != value or value.lstrip(_BOM)[:1] == '"':
        return '"' + value.translate(_ESCAPE) + '"'
    return value


def render_constant(value: str) -> str:
    """The lone constant on a line carrying neither an expression nor a bind.

    This is the loose branch: `formats._unquote` strips one pair of outer
    quotes and unescapes nothing. A value that itself opens and closes with a
    quote therefore has to be wrapped, or reading it back would strip its own
    quotes off; so does one the reader's `strip()` would trim.
    """
    if value.strip() != value:
        return '"' + value + '"'
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return '"' + value + '"'
    return value


def render_parm_line(name: str, parm: ParmValue) -> str:
    """One line of a `.parm` file — the inverse of `formats._parm_value`.

    Which renderer each half gets is decided by the same bits the reader used
    to decide how many halves there are, which is what makes the unescaping
    asymmetry (see the module docstring) harmless in both directions.
    """
    flags = parm.flags
    if flags & formats._UNKNOWN_EXTRA_BIT:
        tail = render_field(parm.value)
        if parm.unrecognised:
            tail += " " + parm.unrecognised
        return f"{name} {flags} {tail}"

    halves = bool(flags & formats._EXPR_BIT) + bool(flags & formats._BIND_BIT)
    if not halves:
        return f"{name} {flags} {render_constant(parm.value)}"

    values: list[str] = [parm.value]
    if flags & formats._EXPR_BIT:
        values.append(parm.expr or "")
    if flags & formats._BIND_BIT:
        values.append(parm.bind or "")
    rendered = [render_field(v) for v in values[:-1]] + [render_last_field(values[-1])]
    return f"{name} {flags} {' '.join(rendered)}"


def _number(value: float) -> str:
    """Print a coordinate the way the file does: no `.0` on a whole number."""
    if value == int(value):
        return str(int(value))
    return repr(value)


def render_tile_line(node: NodeFile) -> str:
    return "tile " + " ".join(
        _number(v) for v in (node.x, node.y, node.width, node.height)
    )


def render_flags_line(flags: dict[str, str]) -> str:
    """The `flags` line — `flags =  name value name value`.

    `formats._flag_pairs` reads the tail as pairs, so a flag whose value the
    reader could not find (an odd trailing token, never observed in the cache)
    cannot be written back unambiguously; that is refused rather than guessed.
    """
    if any(value == "" for value in flags.values()):
        raise ExpandError(
            "a node flag has no value, which cannot be written back "
            "unambiguously: " + ", ".join(sorted(flags))
        )
    if not flags:
        # 32 `.n` files in the cache carry no flags at all and write
        # `flags = ` with a single trailing space, not two.
        return "flags = "
    body = " ".join(f"{name} {value}" for name, value in flags.items())
    return "flags =  " + body


def render_color_line(color: tuple[float, float, float]) -> str:
    # The trailing space is what toeexpand writes; `_floats` ignores it either
    # way, and keeping it makes an unchanged file's line identical.
    return "color " + " ".join(f"{v:g}" for v in color) + " "


def render_inputs_block(inputs: list[tuple[int, str]]) -> list[str]:
    """The `inputs` block: an index and a sibling name.

    The separator is a space then a tab, not a tab: measured on all 17,867
    input lines in the expansion cache, every one of which uses `' \\t'`.
    `read_node` splits on any whitespace and would not notice, but writing a
    bare tab makes 28% of `.n` files differ byte for byte from what
    `toeexpand` wrote for no reason at all.
    """
    lines = ["inputs", "{"]
    lines += [f"{index} \t{source}" for index, source in sorted(inputs)]
    lines.append("}")
    return lines


def render_payload(body: bytes, prologue: bytes | None = None) -> bytes:
    """A `.text` file: the 27-byte prologue with its length field, then bytes.

    The source file's own prologue is reused when there is one — only its
    trailing length field is authoritative to `formats.read_payload`, but the
    other five words have never been surveyed, so carrying them across beats
    stamping a constant over them.
    """
    head = prologue if prologue and len(prologue) == 23 else b"2\n*" + struct.pack(
        ">5I", 1, 1, 1, 1, 2
    )
    return head + struct.pack(">I", len(body)) + body


def render_table(rows: list[list[str]], source: bytes | None = None) -> bytes:
    """A `.table` file.

    Measured over all 2,336 tables in the cache: every one opens `1\\n*` with
    the first word 1 and the fourth 0, and 71,760 of 71,767 cells carry the tag
    2. The tag is not read back by `formats.read_table`, so the seven cells
    that carry 1 do not affect the round trip; the source's own leading words
    are still reused where there is a source.
    """
    head = source[:3] if source and len(source) >= 19 else b"1\n*"
    unknown, _cols, _rows, pad = (
        struct.unpack(">4I", source[3:19]) if source and len(source) >= 19 else (1, 0, 0, 0)
    )
    cols = max((len(row) for row in rows), default=0)
    out = bytearray(head)
    # Rows first, then columns — the order formats.read_table reads. Both
    # sides had it reversed until 2026-08-29 and agreed with each other, which
    # is why the round trip never noticed.
    out += struct.pack(">4I", unknown, len(rows), cols, pad)
    for row in rows:
        padded = list(row) + [""] * (cols - len(row))
        for cell in padded:
            encoded = cell.encode("utf-8")
            out += struct.pack(">2I", 2, len(encoded)) + encoded
    return bytes(out)


# ---------------------------------------------------------------------------
# Patching a file in the expanded tree
# ---------------------------------------------------------------------------


def patch_parm_file(text: str, parms: dict[str, ParmValue]) -> str:
    """Rewrite only the lines whose parameters changed, keeping file order.

    Order and untouched lines are preserved rather than the whole file being
    re-rendered, so a rebuild of an unedited component reproduces its `.parm`
    files byte for byte and the invariant does not rest on the renderer being
    perfect for values nobody touched.

    Where a name appears twice — 2 files of 46,452 in the cache — the last
    occurrence is the one patched, because that is the one `read_parms` keeps.
    """
    lines = text.splitlines()
    last: dict[str, int] = {}
    for index, raw in enumerate(lines):
        stripped = raw.strip()
        if not stripped or stripped == "?":
            continue
        match = formats._PARM_LINE.match(stripped)
        if match:
            last[match.group("name")] = index

    seen: set[str] = set()
    for name, index in last.items():
        seen.add(name)
        if name in parms:
            lines[index] = render_parm_line(name, parms[name])

    removed = {name for name in seen if name not in parms}
    if removed:
        lines = [
            raw
            for index, raw in enumerate(lines)
            if index not in {last[name] for name in removed}
        ]

    added = [name for name in parms if name not in seen]
    if added:
        rendered = [render_parm_line(name, parms[name]) for name in added]
        # Before the closing `?` sentinel when there is one, so the file keeps
        # the shape toeexpand wrote.
        tail = len(lines)
        while tail > 0 and not lines[tail - 1].strip():
            tail -= 1
        if tail > 0 and lines[tail - 1].strip() == "?":
            tail -= 1
        lines[tail:tail] = rendered

    return "".join(line + "\n" for line in lines)


def patch_node_file(text: str, node: NodeFile) -> str:
    """Rewrite the `tile`, `flags`, `color` lines and the `inputs` block.

    Every other line is carried across verbatim, including the blocks this
    project has never measured (`outputs`, `docked`, `wires` — none of them
    occurs in the 51,397 `.n` files in the cache, so re-rendering them would be
    a guess) and the type line, which is not the text's to change: a node whose
    type differs is a different operator, and that is handled by replacing the
    node, not by editing this line.
    """
    lines = text.splitlines()
    out: list[str] = []
    index = 0
    wrote_color = False
    wrote_inputs = False
    inside: str | None = None

    while index < len(lines):
        raw = lines[index]
        stripped = raw.strip()
        head = stripped.split(" ", 1)[0]

        if inside is not None:
            if stripped == "}":
                inside = None
            index += 1
            continue

        if head == "tile":
            out.append(render_tile_line(node))
        elif head == "flags":
            out.append(render_flags_line(node.flags))
        elif head == "color":
            wrote_color = True
            if node.color is not None:
                out.append(render_color_line(node.color))
        elif stripped in ("inputs", "outputs", "docked", "wires"):
            if stripped == "inputs":
                wrote_inputs = True
                # Kept even when empty: one `.n` in the cache writes an empty
                # `inputs { }` block, and dropping it is a byte-level edit to
                # a file the text never asked to change.
                out += render_inputs_block(node.inputs)
                inside = stripped
            else:
                out.append(raw)
                inside = stripped
                # An unmeasured block is copied through untouched.
                index += 1
                while index < len(lines):
                    out.append(lines[index])
                    if lines[index].strip() == "}":
                        break
                    index += 1
                inside = None
        elif stripped == "end":
            if not wrote_inputs and node.inputs:
                out += render_inputs_block(node.inputs)
                wrote_inputs = True
            if not wrote_color and node.color is not None:
                out.append(render_color_line(node.color))
                wrote_color = True
            out.append(raw)
        else:
            out.append(raw)
        index += 1

    return "".join(line + "\n" for line in out)


# ---------------------------------------------------------------------------
# Applying a whole text to an expanded tree
# ---------------------------------------------------------------------------


@dataclass
class Changes:
    """What applying the text did, and what it could not do.

    `gaps` is the honest half: every place the text asked for something this
    module cannot write is listed here rather than being applied approximately.
    A caller that finds `gaps` non-empty is looking at a rebuild that does not
    say what the text said.
    """

    files: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    created: list[str] = field(default_factory=list)
    estimated_flags: int = 0
    gaps: list[str] = field(default_factory=list)

    @property
    def touched(self) -> int:
        return len(self.files) + len(self.deleted) + len(self.created)


def _parm_from_data(
    name: str, data: Any, source: ParmValue | None, where: str
) -> tuple[ParmValue, str | None]:
    """Rebuild a `ParmValue` from the text, taking the flags word from source.

    Returns the value and, when the word had to be composed instead of read,
    the sentence saying so. The word is what the text does not carry (see the
    module docstring): when the source has the line, it is exact; when the
    text asks for a mode the source line does not have, it is composed from
    the keys the text does carry, and that is an estimate.
    """
    if isinstance(data, dict):
        expr = data.get("expr")
        bind = data.get("bind")
        unrecognised = data.get("unrecognised")
        value = data.get("value", "")
    else:
        expr = bind = unrecognised = None
        value = data

    flags = None
    if source is not None:
        # The source's mode has to agree with the text's keys, or the flags
        # word would decide how many halves the line has while the text
        # decides what they hold — and the two would disagree silently.
        #
        # The `0x200000` bit is checked on its own and first, because it
        # overrides the other two on the way in: `_parm_value` hands the whole
        # tail back as `unrecognised` and leaves `expr` and `bind` empty even
        # when their bits are set. Comparing all three at once reports the 13
        # lines in the cache that carry it as mode changes they are not.
        if bool(source.flags & formats._UNKNOWN_EXTRA_BIT) == (unrecognised is not None):
            if unrecognised is not None:
                flags = source.flags
            elif (bool(source.flags & formats._EXPR_BIT) == (expr is not None)
                  and bool(source.flags & formats._BIND_BIT) == (bind is not None)):
                flags = source.flags

    gap = None
    if flags is None:
        flags = _composed_flags(expr, bind, unrecognised)
        gap = (
            f"{where}: parameter '{name}' is written in a mode the source line "
            f"did not have, so its flags word was composed from the text's "
            f"keys; bits other than 0x10 and 0x200 that the source line "
            f"carried are lost"
        ) if source is not None else (
            f"{where}: parameter '{name}' is not in the source file, so its "
            f"flags word was composed from the text's keys rather than read"
        )

    return ParmValue(
        flags=flags,
        value="" if value is None else str(value),
        expr=expr,
        bind=bind,
        unrecognised=unrecognised,
    ), gap


def _composed_flags(expr, bind, unrecognised) -> int:
    flags = 0
    if expr is not None:
        flags |= formats._EXPR_BIT
    if bind is not None:
        flags |= formats._BIND_BIT
    if unrecognised is not None:
        flags |= formats._UNKNOWN_EXTRA_BIT
    return flags


def _node_file_from_data(data: dict, source: NodeFile) -> NodeFile:
    """The `.n` fields the text carries, laid over the source node."""
    tile = data.get("tile") or [source.x, source.y, source.width, source.height]
    colour = data.get("color")
    return NodeFile(
        family=source.family,
        short_type=source.short_type,
        x=float(tile[0]),
        y=float(tile[1]),
        width=float(tile[2]),
        height=float(tile[3]),
        flags=dict(data.get("flags") or {}),
        inputs=[(int(i), str(s)) for i, s in (data.get("inputs") or [])],
        color=tuple(float(c) for c in colour) if colour else None,
    )


def _apply_node(root: Path, stem: Path, data: dict, node: Node | None,
                changes: Changes) -> None:
    where = "/" + stem.relative_to(root).as_posix()

    if node is None:
        changes.gaps.append(
            f"{where}: the text adds an operator that the source file does "
            f"not have. Creating one is not implemented: a new node needs a "
            f"`.n` file whose family and type line, panel state and custom "
            f"parameter definitions the text does not carry."
        )
        return

    n_file = stem.with_suffix(".n")
    wanted = _node_file_from_data(data, node.node)
    # The guard is on the parsed form, not on the rendered bytes: a file the
    # text did not change is never re-rendered at all, so an unedited rebuild
    # carries it across byte for byte and the invariant does not depend on the
    # renderers being right about values nobody touched.
    if wanted != node.node:
        current = n_file.read_text(encoding="utf-8", errors="replace")
        n_file.write_text(patch_node_file(current, wanted), encoding="utf-8")
        changes.files.append(where + ".n")

    _apply_parms(root, stem, data, node, changes)
    _apply_text(root, stem, data, node, changes)
    _apply_table(root, stem, data, node, changes)

    if list(data.get("custom_pages") or []) != list(node.custom_pages):
        changes.gaps.append(
            f"{where}: custom parameter pages differ. `.cparm` definition "
            f"lines have never been measured (see formats.read_custom_parms), "
            f"so this file is not written."
        )
    # Compared against what the *file* holds, not against the resolved name:
    # `type` is canonical only when the dump was made with the atom index
    # present, and `saved_type` carries the contraction whenever the two
    # differ. Comparing the resolved names instead reports every contracted
    # operator in the file as a type change whenever one side of the trip had
    # an index and the other did not — 5 of them in `checker.tox` alone.
    saved = data.get("saved_type") or data.get("type")
    if saved and node.node.op_type and saved != node.node.op_type:
        changes.gaps.append(
            f"{where}: the text changes the operator type from "
            f"'{node.node.op_type}' to '{saved}'. Replacing an operator is "
            f"not implemented; only its parameters, wiring, placement, flags "
            f"and contents can be written back."
        )

    wanted_children = {child["name"]: child for child in data.get("children") or []}
    have = {child.name: child for child in node.children}
    child_dir = stem
    for name, child_data in wanted_children.items():
        _apply_node(root, child_dir / name, child_data, have.get(name), changes)
    for name in have:
        if name not in wanted_children:
            _delete_node(root, child_dir / name, changes)


def _apply_parms(root: Path, stem: Path, data: dict, node: Node,
                 changes: Changes) -> None:
    where = "/" + stem.relative_to(root).as_posix()
    wanted_raw = data.get("parms")
    if wanted_raw is None:
        return
    built = {
        name: _parm_from_data(name, value, node.parms.get(name), where)
        for name, value in wanted_raw.items()
    }
    wanted = {name: parm for name, (parm, _) in built.items()}

    # Nothing is reported until something is actually written: an unedited
    # rebuild must come back with no gaps at all, or the signal is noise.
    if wanted == node.parms:
        return

    parm_file = stem.with_suffix(".parm")
    if not parm_file.exists():
        if wanted:
            changes.gaps.append(
                f"{where}: the text sets parameters on an operator whose "
                f"`.parm` file the source does not have, so no flags word is "
                f"available for any of them; not written."
            )
        return
    for name, (parm, gap) in built.items():
        if gap and node.parms.get(name) != parm:
            changes.estimated_flags += 1
            changes.gaps.append(gap)
    current = parm_file.read_text(encoding="utf-8", errors="replace")
    parm_file.write_text(patch_parm_file(current, wanted), encoding="utf-8")
    changes.files.append(where + ".parm")


def _apply_text(root: Path, stem: Path, data: dict, node: Node,
                changes: Changes) -> None:
    where = "/" + stem.relative_to(root).as_posix()
    if "text" not in data:
        return
    lines = data["text"]
    text_file = stem.with_suffix(".text")
    if lines is None:
        if node.text is not None and text_file.exists():
            text_file.unlink()
            changes.deleted.append(where + ".text")
        return
    body = join_text(list(lines))
    if body == node.text:
        return
    if not text_file.exists():
        changes.gaps.append(
            f"{where}: the text gives DAT contents to an operator that has no "
            f"`.text` file in the source; not written."
        )
        return
    existing = text_file.read_bytes()
    text_file.write_bytes(render_payload(body.encode("utf-8"), existing[:23]))
    changes.files.append(where + ".text")


def _apply_table(root: Path, stem: Path, data: dict, node: Node,
                 changes: Changes) -> None:
    where = "/" + stem.relative_to(root).as_posix()
    if "table" not in data:
        return
    rows = data["table"]
    table_file = stem.with_suffix(".table")
    if rows is None:
        if node.table is not None and table_file.exists():
            table_file.unlink()
            changes.deleted.append(where + ".table")
        return
    if rows == node.table:
        return
    if not table_file.exists():
        changes.gaps.append(
            f"{where}: the text gives table cells to an operator that has no "
            f"`.table` file in the source; not written."
        )
        return
    existing = table_file.read_bytes()
    table_file.write_bytes(render_table([list(r) for r in rows], existing))
    changes.files.append(where + ".table")


def _delete_node(root: Path, stem: Path, changes: Changes) -> None:
    """Remove every file belonging to one operator, and its children."""
    where = "/" + stem.relative_to(root).as_posix()
    if stem.is_dir():
        shutil.rmtree(stem)
    for sibling in sorted(stem.parent.glob(stem.name + ".*")):
        if sibling.is_file():
            sibling.unlink()
    changes.deleted.append(where)


def apply_text(root: Path, data: dict, project: Project) -> Changes:
    """Lay the parsed text over an expanded tree at `root`.

    `project` must be the tree as it is on disk: the source is what supplies
    every field the text does not carry, the parameter flags words above all.
    """
    changes = Changes()
    wanted = {op["name"]: op for op in data.get("operators") or []}
    have = {node.name: node for node in project.roots}

    scoped = data.get("path", "/")
    if scoped and scoped != "/":
        # A subtree dump names only part of the file; the rest of it must not
        # be read as "everything else was deleted".
        for name, op in wanted.items():
            target = _stem_for_path(root, scoped, name)
            node = project.find(_operator_path(scoped, name))
            _apply_node(root, target, op, node, changes)
        return changes

    for name, op in wanted.items():
        _apply_node(root, root / name, op, have.get(name), changes)
    for name in have:
        if name not in wanted:
            _delete_node(root, root / name, changes)
    return changes


def _operator_path(scoped: str, name: str) -> str:
    head = scoped.rstrip("/")
    return head if head.rsplit("/", 1)[-1] == name else f"{head}/{name}"


def _stem_for_path(root: Path, scoped: str, name: str) -> Path:
    path = _operator_path(scoped, name).lstrip("/")
    return root / path


# ---------------------------------------------------------------------------
# The whole trip
# ---------------------------------------------------------------------------


class OutputExists(ExpandError):
    """Something already sits where the rebuilt file would go.

    Its own type so a caller can recognise this one refusal without matching
    on the message text.
    """


def rebuild(
    source: str | Path,
    text: str,
    output: str | Path,
    resolver: TypeResolver | None = None,
    install=None,
) -> Changes:
    """Write `text` back over `source` and collapse the result into `output`.

    Nothing is written beside `source`: the expansion is copied into the cache
    first, because `toecollapse` renames whatever already sits at its
    destination to `.bkp` and must never do that to a user's file.

    `output` must not exist. The check lives here rather than on one of the
    two surfaces because both of them reach this function and only the MCP
    tool had it: `td-atlas project write` went straight past into
    `toecollapse`, which renames whatever is already there to `.bkp` — the
    exact thing this module exists to prevent.
    """
    import json

    source = Path(source).expanduser().resolve()
    output = Path(output).expanduser()
    if output.exists():
        raise OutputExists(
            f"{output} already exists. Repacking writes the file whole rather "
            f"than merging into it, so nothing of the user's is overwritten "
            f"here: write beside it and compare the two with "
            f"'td-atlas project diff'."
        )
    data = json.loads(text)
    if not isinstance(data, dict) or "operators" not in data:
        raise ExpandError(
            "that text is not a network dump: it has no 'operators' key. "
            "Produce one with `td-atlas project text`."
        )

    expansion = expand(source)
    # A fresh directory per call, not a fixed one: two rebuilds running at
    # once would otherwise delete each other's tree mid-collapse, and the
    # failure looks like a corrupt file rather than a collision.
    cache_dir().mkdir(parents=True, exist_ok=True)
    # The prefix is the one the cache's eviction skips, so a long rebuild
    # cannot have its working tree deleted out from under it.
    work = Path(tempfile.mkdtemp(prefix=WORK_PREFIX, dir=cache_dir()))

    name = output.name
    if Path(name).suffix.lower() not in (".toe", ".tox"):
        name += source.suffix
    tree = work / f"{name}.dir"
    shutil.copytree(expansion.root, tree)
    template = work / f"{name}.toc"
    if expansion.toc and expansion.toc.exists():
        shutil.copyfile(expansion.toc, template)

    project = load(
        Expansion(
            source=expansion.source,
            root=tree,
            toc=template if template.exists() else None,
            cached=True,
        ),
        resolver=resolver,
    )
    try:
        changes = apply_text(tree, data, project)
        collapse(
            tree, output, install=install,
            toc_template=template if template.exists() else None,
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return changes
