# The .toe/.tox file format

Derivative documents none of this. Everything below was derived by experiment
against the example libraries that ship with TouchDesigner 2025.32460, and each
claim states the evidence. Re-run the checks against a new build before
trusting them there.

`toeexpand` (in `TouchDesigner.app/Contents/MacOS/` on macOS, `bin/` elsewhere)
unpacks a container into a directory tree; `toecollapse` repacks one.

## Driving the tools

`toeexpand` **exits with status 1 on success.** Detect success by the output
directory appearing, never by the exit code.

Both tools work in place: `toeexpand` writes `<name>.dir/` and `<name>.toc`
beside its input, and `toecollapse` renames any existing target to `.bkp1`.
Always operate on a copy.

`toecollapse` requires the `.toc` listing beside the `.dir`. Repacking is a
re-serialisation, not a copy — a round-tripped file is valid but not
byte-identical (5,318 → 5,662 bytes on one sample).

## Directory layout

```
project.toe.dir/
  .build                  version stamp
  .start .grps .root .parm
  project1.n              a node at /project1
  project1.parm
  project1/               its children
    UI.n                  a node at /project1/UI
    UI/                   and so on
```

A node's path is its path within the tree, minus the extension. Per operator:

| File | Contents |
| --- | --- |
| `.n` | family, type, position, flags, input wiring |
| `.parm` | parameter values that differ from the defaults |
| `.cparm` | custom parameter definitions |
| `.text` | DAT contents — Python, GLSL, plain text |
| `.table` | Table DAT cells |
| `.panel` | panel UI state (layout only) |

## `.n` — nodes

```
TOP:constant
tile 465 -166 130 72
flags =  current on viewer 1 parlanguage 0
inputs
{
0 <tab> mono1
}
color 0.55 0.55 0.55
end
```

The first line is `FAMILY:type`, where `type` is a **contracted** name — see
below. `inputs` lists `index<tab>name`, naming siblings relative to the node's
parent, so wiring must be resolved against that parent to get absolute paths.

## `.parm` — parameters

```
?
radiusx 0 0.2
fillcolorr 17 1 op('circle_red').par.value0
externaltox 48 "" "parent.sys.fileFolder + '/slider.tox'"
label 0 some words with spaces
?
```

Format: `name <flags> <constant> [<expression>]`, wrapped in `?` sentinels.

**Bit `0x10` of the flags word marks expression mode.** Measured across the
2,861 `.parm` files in the shipped libraries: all 14,835 lines carrying that
bit have two values, and none of the lines without it do. The other bits track
unrelated state and are left alone.

A parameter in expression mode keeps both halves — the last constant it held
*and* the expression now driving it.

Two parsing traps:

- **Do not tokenise with a shell-style splitter.** Quotes inside an unquoted
  expression are content: `op('circle').par.value0` must survive intact, and
  `shlex` returns `op(circle).par.value0`, which is no longer valid Python.
- **Constants may contain spaces** and are not quoted, so the remainder of a
  non-expression line is taken whole rather than split.

## `.text` and `.table` — payloads

A fixed **27-byte prologue**: `b"2\n*"` (or `b"1\n*"` for tables) followed by
six big-endian `uint32`, the last of which is the payload length. The content
is everything after byte 27.

**Use the length field.** Scanning for the first newline instead — the obvious
approach, and correct on the first file anyone tries — truncates every payload
whose length byte does not happen to be `0x0A`. That is roughly one shader in
three. Verified exact on all 686 payload files across 60 snippet libraries.

Tables continue after the prologue with four `uint32` (unknown, columns, rows,
padding) and then each cell as a `uint32` tag, a `uint32` length, and its
bytes.

## Contracted type names

A saved file records a contraction, not the canonical operator type:

| In the file | Actually |
| --- | --- |
| `geoCOMP` | `geometryCOMP` |
| `evalDAT` | `evaluateDAT` |
| `parexecDAT` | `parameterexecuteDAT` |
| `compTOP` | `compositeTOP` |
| `audiodevoutCHOP` | `audiodeviceoutCHOP` |

**71 types are affected**, about one node in six in a typical project. Without
the mapping those nodes cannot be joined to the operator index.

The contraction is always a subsequence of the full name, which makes guessing
tempting and wrong: `parexecDAT` is a subsequence of both
`parameterexecuteDAT` and `pargroupexecuteDAT`, and choosing the shorter
candidate reports complete success while silently picking the wrong one.

td-atlas measures the map instead, during the runtime pass: every type is
instantiated in a sandbox, the sandbox is saved once and expanded once, and
each node's written spelling is compared against the type TouchDesigner reports
for it. See `derive_type_aliases` in `src/td_atlas/atoms/probe.py`.

## The offline wiki mirror

`Samples/Learn/OfflineHelp/https.docs.derivative.ca/` holds 2,060 pages.

Article text sits between `id="mw-content-text"` and `class="printfooter"`.
Three things must be stripped or every page carries them: the `[edit]` section
links, the family navbox (`catList navigation-not-searchable`), and the
glossary tooltips (`mw-lingo-tooltip`), which otherwise append a page of
unrelated definitions to every article.

**Pages whose wiki title contains a slash were mirrored into subdirectories** —
"TCP/IP DAT" lives at `TCP/IP_DAT.htm`, not in the root. A non-recursive glob
misses them.

Categories are in the `catlinks` block as `title="Category:X"`, and must be
read before the body is extracted since that block is part of the chrome that
gets stripped. MediaWiki appends `(page does not exist)` to the title of a
redlinked category; strip it.

## Other sources in the bundle

| Path (relative to `tfs/`) | Contents |
| --- | --- |
| `Config/TDParameterHelp.json` | 654 operators, 16,584 parameters: label, prose, type |
| `Samples/Palette/` | 277 ready-made components in 16 folders |
| `Samples/Learn/OPSnippets/Snippets/` | 483 example networks, one per operator |
| `Config/Help/command.help` | 189 commands; entry names at column zero, tab-indented bodies |
| `Config/Help/exprhelp` | 505 expressions; brace-delimited blocks, first line is the signature |

Note that `TDParameterHelp.json` documents parameter **groups** (`t` for
Translate) while only the members (`tx`, `ty`, `tz`) are settable, and that it
describes 654 types where the running application exposes 647 — 13 of which the
help file never mentions.
