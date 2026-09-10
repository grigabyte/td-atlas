# The .toe/.tox file format

Derivative documents none of this. The formats were derived by experiment
against the example libraries that ship with TouchDesigner 2025.32460, and each
claim states its evidence. Re-run the checks against a new build before trusting
them there.

`toeexpand` (in `TouchDesigner.app/Contents/MacOS/` on macOS, `bin/` elsewhere)
unpacks a container into a directory tree; `toecollapse` repacks one.

## Driving the tools

`toeexpand` **exits with status 1 on success.** Detect success by the output
directory appearing, never by the exit code.

Both tools work in place. `toeexpand` writes `<name>.dir/` and `<name>.toc`
beside its input. `toecollapse` renames any existing target to `.bkp1`, and to
`.bkp2` on a second run (measured 2026-09-07 by collapsing the same directory
twice). Always operate on a copy.

`toecollapse` requires the `.toc` listing beside the `.dir`. Repacking is a
re-serialisation, not a copy. A round-tripped file is valid but not
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

### What each of those three forms costs

Measured on this machine, with the atom index present, over three shipped
palette components. Times are the wall clock of producing that form once from
a warm expansion cache; sizes are bytes on disk.

| Component | Operators | `.tox` on disk | Network text | Text gzipped | Expanded tree | Text alone | Text + `.tox` | Text + tree |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `Generators/checker.tox` | 21 | 3,942 | 17,955 | 2,326 | 238,448 | 0.001 s | 0.001 s | 0.008 s |
| `Tools/battery.tox` | 35 | 3,870 | 25,315 | 2,567 | 22,551 | 0.002 s | 0.002 s | 0.012 s |
| `Mapping/kantanMapper.tox` | 4,080 | 308,928 | 6,111,237 | 299,592 | 3,076,502 | 0.259 s | 0.260 s | 1.723 s |

Correctness decides it. The rebuild is a patcher, so **the text alone cannot
produce a `.toe`**, and without the source there is no restore at all.

A `.toe` is a compressed container, and across the 277 shipped palette
components the text runs a median 5.3x the size of the file it was printed from.
The spread is wide (0.01x to 24x, and for 45 of them the text is the *smaller*
of the two), so the three rows above are examples and not a law. Against the
text, the copy costs a median 19% and no measurable time, and
`project/variants.py` stores both. The expanded tree adds half the text again,
plus 9,647 files for `kantanMapper`, for something `toeexpand` reproduces on
demand.

## The network text format

The rows above measure the network text, td-atlas's own format.
`td-atlas project text` prints it and `td-atlas project write` reads it back.

**Standard JSON, non-standard printer.** Four syntaxes were compared over one
canonical model, a nested tree with sibling references by name and parameters
sorted. Holding the model still left the syntax as the only thing measured. The
four were plain JSON, JSON with a custom printer, YAML, and a line-oriented
format of our own. The line-oriented one is the smallest at every size and plain
JSON the largest, by 1.8x to 3.6x. Two things outweighed size:

- **The parser costs nothing.** JSON reads back with `json.loads`. YAML and the
  line-oriented format each needed a hand-written pair, an emitter and a parser,
  139 and 86 lines in two places, and any disagreement between the two halves is
  a silent loss of data. On six test networks each of the two hand-written
  formats round-tripped four of them, losing 5–6 and 11–90 operators
  respectively. What broke were Table DAT cells whose data contained the
  separator, parameter values with a trailing space, and the final newline of a
  YAML block scalar. JSON round-tripped all six with nothing lost.
- **DAT text is an array of lines.** Editing one line inside a DAT is the single
  most common real change, and naive JSON is worst there. It holds the whole
  payload as one string with `\n` escapes, so the diff is the whole payload.
  Measured at 57–123 bytes of diff for the array form against 1,209–5,985 for
  the naive one. Vectors and short lists print on one line for the same
  reason.

Two things the same measurement **ruled out**:

- **Node positions do not need a section of their own.** A drag already costs
  two lines, because `tile` is one line inside its node. A position section
  keyed by path is rewritten across a node's whole subtree when the node is
  renamed. That is two lines against sixty-eight on a component of 35 operators.
- **Defaults do not need stripping.** `toeexpand` already writes only the
  differences into `.parm`. Of 27,336 parameters, 554 equalled their default.
  That is 2.1%, worth 0.5–2% of the bytes. A pass to compare every value
  against the index does not pay for itself.

Those comparisons were run on 2026-08-28, against prototypes that were not kept.
They are evidence for the choice, and not numbers to re-run. The shipped
printer's own sizes are the table above, and a test re-measures them. The same
`kantanMapper.tox` prints 106,082 lines and 6,111,237 bytes. The shipped printer
prints 12–24% more bytes (and 7% more lines) than the prototype did, probably
because it emits `family`, `custom_parms`, `table` and `color` in every node
even when they are empty. That correction moves all four columns the same way,
so the comparison between the four formats holds.

## Nodes (`.n`)

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

The first line is `FAMILY:type`, where `type` is a **contracted** name (see
[Contracted type names](#contracted-type-names)). `inputs` lists
`index<tab>name`, naming siblings relative to the node's parent, so wiring must
be resolved against that parent to get absolute paths.

The whitespace matters if you ever write one of these back. Three findings,
measured over the 51,397 `.n` files and the 17,867 input lines in this project's
expansion cache. Every input line separates the index from the name with a
**space then a tab**, never a bare tab. A node that has flags writes `flags =`
followed by **two** spaces, and the 32 nodes that have none write it with one.
One node writes an empty `inputs { }` block, which is not the same file as one
with no block at all. None of that changes how a reader parses the file, and all
of it changes 28% of the files byte for byte if you get it wrong.

Lines other than `tile`, `flags`, `color` and the `inputs` block do occur
(`view 8 0 1 1 1 0 0 0 0 1 1`, for one) and no reader here parses them, so a
writer must carry them across untouched and never re-render the file. The
`outputs`, `docked` and `wires` blocks the reader knows how to skip appear in
**none** of the 51,397 files, so their layout stays unmeasured.

## Parameters (`.parm`)

```
?
radiusx 0 0.2
fillcolorr 17 1 op('circle_red').par.value0
externaltox 48 "" "parent.sys.fileFolder + '/slider.tox'"
colorr 515 1 parent.Checker.par.Color1r
label 0 some words with spaces
?
```

A line is `name <flags> <constant> [<expression>] [<bind expression>]`, wrapped
in `?` sentinels. **Which optional halves are present is decided by the flags
word and by nothing else.** Two bits do it:

| Bit | Meaning | Values on the line |
| --- | --- | --- |
| `0x10` | Expression mode | constant, expression |
| `0x200` | **Bind** mode | constant, bind expression |
| both | bind reference that also stores an expression | constant, expression, bind expression |
| neither | Constant mode | constant only |

The number of values on a line equals `1 + expression_bit + bind_bit` on
**469,433** lines out of all **469,446** `.parm` lines in the expansion cache
(222 components and projects, including everything in the shipped palette and
snippet libraries). Counts per combination are 326,118 constant, 138,658
expression, 4,110 bind, 547 both.

`0x200` is Bind mode, the fourth parameter mode, after Constant, Expression and
Export. Every one of the 4,670 lines carrying it has a second value, and 4,645
of those are a `.par.` reference, the *bind expression* naming the bind master.
Of the remaining 25, most are `op('bind1')['chan1']` subscripts and table cells,
which the wiki's `Binding` article names as exactly the other things allowed to
be a bind master. A few are neither, `(me, 'PivotDistance')` and
`ext.PopDialogExt._EnteredText` among them, and what makes those valid bind
masters was not measured.

**Bit `0x200000` adds a value whose meaning is unknown.** It appears on 13 lines
in the cache and on nothing else, and those 13 are exactly the lines carrying
one value more than the two bits above account for. In all 13 the extra value is
identical to the one before it.

```
Angleofview 69206592 210 op('./float1').par.Value0 op('./float1').par.Value0
```

No measurement can say which position is the expression and which the bind
expression. td-atlas reports the constant and returns the rest unsplit, marked
unrecognised.

Reading the halves off the *shape* of the line fabricates values. The
`colorr 515 …` line above carries no `0x10`, so the whole tail becomes the
constant and the parameter is reported as holding
`1 parent.Checker.par.Color1r`, a value it does not have.

Three parsing traps:

- **Do not tokenise with a shell-style splitter.** Quotes inside an unquoted
  expression are content. `op('circle').par.value0` must survive intact, and
  `shlex` returns `op(circle).par.value0`, which is no longer valid Python.
- **Constants may contain spaces** and are not quoted, so on a line with no mode
  bit the remainder is taken whole. The shape cannot tell a spaced constant from
  a pair, so the count has to come from the flags word.
- **A value may be preceded by a byte-order mark.** 62 lines in the cache carry
  a BOM in the tail and 6 of them write `<BOM>"…"` where every other line writes
  `"…"`. Treat the BOM as part of the value. Treat it as the value's first
  character and the quotes become content, the expression splits on every space,
  and what comes back is a fragment.

Two more measured details. An expression may be stored as the empty string
(`Rawdata 201326673 "" ""`, 83 lines), and present and blank is not the same as
absent. The flags word carries further bits (`0x1`, `0x2`, `0x20`, `0x40`,
`0x100`, `0x4000000`, `0x8000000` and others) whose meaning has not been
measured. None of them changes how many values the line holds.

### Writing a `.parm` line back

**The same quoted constant is read two different ways depending on the flags
word, and a writer has to reproduce both.** With no mode bit the constant is the
whole remainder with one pair of outer quotes stripped and nothing unescaped;
with `0x10`, `0x200` or `0x200000` set it is lexed as a field and `\"` becomes
`"`. So

```
Bodytext 0        "say \"hi\""     ->  say \"hi\"
Bodytext 16       "say \"hi\"" e   ->  say "hi"
```

55 lines in the cache sit on the strict side of that split; `Bodytext` in
`alembicoutPOP/example2/comment1.parm` is one. A writer that picks one spelling
for both corrupts whichever half it did not pick, so the escaping has to be
keyed off exactly the bits the reader keys its splitting off.

Three more rules follow from the reader:

- Every value but the last is lexed strictly, so it needs quoting when it is
  empty, holds whitespace, or opens with a quote or a BOM.
- The **last** value takes the whole remainder and is only unquoted when it
  already starts with a quote, so it survives bare unless it would be
  mistaken for a quoted string or has whitespace at its ends.
- On a line with no mode bit, a constant that itself opens and closes with a
  quote has to be wrapped again, or reading it back strips its own quotes.

With those, `read(write(read(line))) == read(line)` on all **472,699**
`.parm` and `.cparm` value lines in the cache, and 415,090 of them come back
byte for byte as well. The rest differ only in spelling, a value written bare
that the file quoted or the other way round, and `toecollapse` does not care
about that.

**The flags word itself cannot be recovered from a network dumped to text.**
td-atlas's text form records the mode through its keys and never the 32-bit
word, so a write-back reads the word off the original `.parm` line. Rebuilding
a `.toe` therefore requires the original file; there is no path from text
alone.

## Custom parameter definitions (`.cparm`)

**A `.cparm` is a different grammar from a `.parm`.**

```
?
pages 3 "Scene Changer" Variables About
-1374399999 Resolution Resolution 1 3 0 0 1 1 3840 3 … "Scene Changer" 0 1 "The resolution…"
772804869 Help Help 1 1 0 0 1 1 1 2 0 "" "" About 0
?
```

The first line is `pages <count> <name>...`, and **the number in the flags
column is the page count, not a flags word**. That is verified on all 5,210
`pages` lines in the cache, where the count equals the number of names on every
one. Read with the `.parm` grammar, the component gains a parameter named
`pages` whose value is all its page names glued into one string.

The remaining lines are definitions, `<typecode> <name> <label> <columns…>
<page> <order> [<help>]`, and **td-atlas does not read them.** The layout of
those columns has not been measured, and they do not match the `.parm` line
shape (the second column is a name, not a number). They are dropped, and
`custom_parms` comes back empty for a real `.cparm`. That is an admitted blank,
and not a claim that the component has no custom parameters. The page names are
all that is recovered.

## Payloads (`.text` and `.table`)

The prologue is a fixed **27 bytes**: `b"2\n*"` (or `b"1\n*"` for tables)
followed by six big-endian `uint32`, the last of which is the payload length.
The content is everything after byte 27.

**Use the length field.** Scanning for the first newline is the obvious
approach, and correct on the first file anyone tries. It truncates every payload
whose length byte does not happen to be `0x0A`, roughly one shader in three.
Verified exact on all 686 payload files across 60 snippet libraries.

Tables continue after the prologue with four `uint32` (unknown, columns, rows,
padding) and then each cell as a `uint32` tag, a `uint32` length, and its
bytes.

Measured over all 2,336 tables in the cache, every one opens `1\n*` with the
first `uint32` 1 and the fourth 0, and 71,760 of the 71,767 cells carry the tag
`2`. What the tag means is unknown, and the seven cells that carry `1` are not
distinguishable by anything else measured here. No reader in this project uses
the tag, so writing `2` for every cell reproduces the cells exactly and leaves
that one byte per cell a guess on seven of them.

### Writing a payload back

Only the last of the six prologue words is used by the reader, and the other
five have never been surveyed, so a writer that rewrites a `.text` should keep
the original file's first 23 bytes and replace only the length field. With
that, all 9,502 `.text` files in the cache rewrite byte for byte.

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
tempting and wrong. `parexecDAT` is a subsequence of both
`parameterexecuteDAT` and `pargroupexecuteDAT`, and choosing the shorter
candidate reports complete success while silently picking the wrong one.

td-atlas measures the map during the runtime pass. Every type is instantiated in
a sandbox, the sandbox is saved once and expanded once, and each node's written
spelling is compared against the type TouchDesigner reports for it. See
`derive_type_aliases` in `src/td_atlas/atoms/probe.py`.

## The offline wiki mirror

`Samples/Learn/OfflineHelp/https.docs.derivative.ca/` holds 2,060 pages.

Article text sits between `id="mw-content-text"` and `class="printfooter"`.
Three things must be stripped, or every page carries them: the `[edit]` section
links, the family navbox (`catList navigation-not-searchable`), and the glossary
tooltips (`mw-lingo-tooltip`), which otherwise append a page of unrelated
definitions to every article.

**Pages whose wiki title contains a slash were mirrored into subdirectories.**
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

`TDParameterHelp.json` documents parameter **groups** (`t` for Translate) while
only the members (`tx`, `ty`, `tz`) are settable. It describes 654 types where
the running application exposes 647, and the help file never mentions 13 of
them.
