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
| `.cparm` | custom parameter definitions — a *different* grammar, see below |
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
colorr 515 1 parent.Checker.par.Color1r
label 0 some words with spaces
?
```

Format: `name <flags> <constant> [<expression>] [<bind expression>]`, wrapped
in `?` sentinels. **Which optional halves are present is decided by the flags
word and by nothing else.** Two bits do it:

| Bit | Meaning | Values on the line |
| --- | --- | --- |
| `0x10` | Expression mode | constant, expression |
| `0x200` | **Bind** mode | constant, bind expression |
| both | bind reference that also stores an expression | constant, expression, bind expression |
| neither | Constant mode | constant only |

Measured across all **469,446** `.parm` lines in the expansion cache (222
components and projects, including everything in the shipped palette and
snippet libraries): the number of values on a line equals
`1 + expression_bit + bind_bit` on **469,433** of them. Counts per
combination: 326,118 constant, 138,658 expression, 4,110 bind, 547 both.

`0x200` is Bind mode — the fourth parameter mode, after Constant, Expression
and Export. Every one of the 4,670 lines carrying it has a second value, and
4,644 of those are a `.par.` reference: the *bind expression*, naming the
bind master. The remaining 26 are `op('bind1')['chan1']` subscripts and table
cells, which the wiki's `Binding` article names as exactly the other things
allowed to be a bind master.

**Bit `0x200000`: an extra value whose meaning is unknown.** It appears on 13
lines in the cache and on nothing else, and those 13 are exactly the lines
carrying one value more than the two bits above account for. In all 13 the
extra value is identical to the one before it —

```
Angleofview 69206592 210 op('./float1').par.Value0 op('./float1').par.Value0
```

— so no measurement can say which position is the expression and which the
bind expression. td-atlas reports the constant and returns the rest unsplit,
marked unrecognised.

Earlier versions of this file said the bits other than `0x10` "track unrelated
state and are left alone". That was wrong, and reading the halves off the
*shape* of the line instead of the flags word fabricated values: the
`colorr 515 …` line above carries no `0x10`, so the whole tail became the
constant and the parameter was reported as holding
`1 parent.Checker.par.Color1r` — a value it does not have.

Three parsing traps:

- **Do not tokenise with a shell-style splitter.** Quotes inside an unquoted
  expression are content: `op('circle').par.value0` must survive intact, and
  `shlex` returns `op(circle).par.value0`, which is no longer valid Python.
- **Constants may contain spaces** and are not quoted, so on a line with no
  mode bit the remainder is taken whole rather than split. This is why the
  count has to come from the flags word: the shape cannot tell a spaced
  constant from a pair.
- **A value may be preceded by a byte-order mark.** 62 lines in the cache
  carry a BOM in the tail and 6 of them write `<BOM>"…"` where every other
  line writes `"…"`. Treat the BOM as part of the value; treat it as the value's first character and the quotes become
  content, the expression splits on every space, and what comes back is a
  fragment.

Two more measured details: an expression may be stored as the empty string
(`Rawdata 201326673 "" ""`, 83 lines) — present and blank is not the same as
absent — and the flags word carries further bits (`0x1`, `0x2`, `0x20`,
`0x40`, `0x100`, `0x4000000`, `0x8000000` and others) whose meaning has not
been measured. None of them changes how many values the line holds.

## `.cparm` — custom parameter definitions

**A `.cparm` is a different grammar from a `.parm`**, not the same file with
different contents.

```
?
pages 3 "Scene Changer" Variables About
-1374399999 Resolution Resolution 1 3 0 0 1 1 3840 3 … "Scene Changer" 0 1 "The resolution…"
772804869 Help Help 1 1 0 0 1 1 1 2 0 "" "" About 0
?
```

The first line is `pages <count> <name>...`, and **the number in the flags
column is the page count, not a flags word** — verified on all 5,210 `pages`
lines in the cache, where the count equals the number of names on every one.
Read with the `.parm` grammar the component gains a parameter named `pages`
whose value is all its page names glued into one string.

The remaining lines are definitions — `<typecode> <name> <label> <columns…>
<page> <order> [<help>]` — and **td-atlas does not read them.** The layout of
those columns has not been measured, and they do not even match the `.parm`
line shape (the second column is a name, not a number), so they are dropped
and `custom_parms` comes back empty for a real `.cparm`. That is an admitted
blank, not a claim that the component has no custom parameters. The page names
are all that is recovered.

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
