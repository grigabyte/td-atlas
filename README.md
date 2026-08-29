# td-atlas

An atomised index of TouchDesigner, a live bridge into a running instance, and
an offline reader for saved projects — exposed to AI agents over MCP.

An agent building in TouchDesigner needs three things at once: exact knowledge
of the operators and parameters on *this* machine, control of a running
instance that can be undone in one step, and a way to read a saved project
without opening it. Miss the first and it guesses parameter names. Miss the
second and a failed step leaves half a network behind. Miss the third and every
question about an existing project needs the application running.

td-atlas is built on two observations:

1. **Almost everything an agent needs to know about TouchDesigner already ships
   inside the application.**
2. **TouchDesigner reports almost nothing when work silently does nothing.**

## The atom index

Two passes produce one SQLite index, exact for the build it was made from
rather than scraped from a wiki describing some other release.

**Static pass** — offline, no TouchDesigner process, 23–30 seconds measured
on an M-series Mac:

| Source in the app bundle | What it yields |
| --- | --- |
| `Config/TDParameterHelp.json` | 654 operators, 16,584 parameters: labels, prose, types |
| `Samples/Learn/OfflineHelp/` | 2,060 wiki pages — 654 Python classes, 686 operator pages, 185 glossary entries, 720 concept and technique articles |
| `Samples/Palette/` | **277 ready-made components** — projection mappers, corner-pinners, audio analysers |
| `Samples/Learn/OPSnippets/` | 483 working example networks, one per operator |
| `Config/Help/{command.help,exprhelp}` | 490 command and expression entries |

**Runtime pass** — instantiates every operator type inside a non-cooking
sandbox and reads what documentation does not record: defaults, numeric ranges
and clamps, **menu options**, parameter pages and ordering, connector counts,
and the 71 contracted type names TouchDesigner writes when it saves. Cooking is
disabled on the sandbox so that creating a Video Device In TOP does not open a
camera.

The two passes do not add up to the table above, and `td-atlas status` reports
the total rather than the static half: the runtime pass finds 13 operator types
the help JSON does not describe and 7,667 parameters it does not list, so an
index that has been probed holds **667 operators and 24,251 parameters** where
the static pass alone holds 654 and 16,584. Those extra parameters are the
reason the runtime pass exists — they are mostly the ones whose defaults and
menu options are only knowable by asking a live instance.

## The bridge

A Web Server DAT and a callbacks DAT, built **from a script rather than shipped
as a `.tox`** — so the bridge is readable, diffable, version-controlled, and
re-running the bootstrap upgrades it in place (or `td-atlas reload`, through
the bridge itself).

Beyond `exec`:

- **`td_health`** — the silent-failure detector. See below.
- **`render`** — any TOP's pixels back as PNG, plus contact sheets for anything
  time-based, because a strobe judged from one frame is a coin toss.
- **`batch`** — several operations inside one `ui.undo` block. A failed batch
  rolls back and leaves no partial network. A successful one is a single
  **Ctrl+Z** for the artist.
- **`errors`** — every node reporting an error or warning.

Requests are authenticated with a token by default.

## What TouchDesigner does not report

`errors` covers what TouchDesigner calls an error. `td_health` covers what it
does not:

```
TouchDesigner (TouchDesigner Non-Commercial) — 61/60 fps, 16/51 operators cooking
[ERROR] 33 operator(s) did not cook once in 91 frames. A branch nothing
        displays or records is never pulled, so it is not running at all
        /project1/AV/fb, /project1/AV/out, /project1/AV/rec, +30 more
[ERROR] 1 operator(s) had their resolution silently reduced by the licence
[ERROR] 1 output operator(s) switched off — these produce nothing and report
        no error   /project1/AV/aout (audio output)
[WARN ] 1 operator(s) cost more than 8 ms per cook   /project1/src_b (430 ms)
```

Every one of those was hit while building a real composition; none of them
raised an error. It also distinguishes a genuinely dead network from a paused
timeline or a backgrounded window, where the frame clock is frozen and there is
no evidence either way.

## The call journal

The status panel inside TouchDesigner holds the *last* call and the next one
overwrites it. That answers "is the bridge alive"; it does not answer "the
agent broke something yesterday, what was it". `~/.td-atlas/calls.jsonl` does:
one line per bridge call, written by the host, outliving the session.

```
$ td-atlas log --failures
08-29 22:23:51  FAIL  op_info             16.6ms  /project1/does_not_exist
        LookupError: no operator at path '/project1/does_not_exist'
08-29 22:23:51  FAIL  par_set             16.5ms  /tdatlas/jrn_noise
        AttributeError: /tdatlas/jrn_noise (noiseTOP) has no parameter 'nosuchpar'

$ td-atlas log --summary
11 calls, 5 failed (45%)   08-29 22:23 to 08-29 22:23

where it fails
  op_create          1 of 2
  par_set            1 of 2
  exec               1 of 1
```

`td_log` is the same thing for an agent, so it can read its own trail rather
than repeat a call that already refused.

Written by the host and not by the bridge, deliberately. A Table DAT inside
TouchDesigner dies with the process, and keeping it would mean saving the
project — which is a Save As that moves the artist's file. A file written from
inside a frame was measured at 124–200 us, three to five times the whole panel
repaint. On the host the append costs 41.6 us of nobody's frame, next to a
round trip that already cost 16 ms.

Bounded at 1 MiB — about 5,300 calls, measured — with the oldest lines dropped
first. Parameters are not logged: only the method, the outcome, the duration,
the path, the caller and a batch's step count. A DAT's text and a whole
network stay out of it, and the bridge token is scrubbed from every line
before it is written.

## Reading projects offline

TouchDesigner ships `toeexpand`, which unpacks a `.toe`/`.tox` into a tree of
text files. td-atlas reads that tree, so a project can be inspected, searched
and compared **without TouchDesigner running** — and without touching the
original, since everything happens on a copy in a cache.

```bash
td-atlas project read  myproject.toe --path /project1 --params
td-atlas project grep  myproject.toe "def onValueChange"
td-atlas project diff  before.toe after.toe
td-atlas project variant save myproject.toe --label before-the-rewire
td-atlas project variant diff myproject.toe --label before-the-rewire --other after
```

`grep` reaches code no file search can: the Python and GLSL inside DATs lives
in the container, not on disk. `diff` compares meaning — added, removed,
retyped, rewired and re-parameterised operators, plus a line diff of changed
DAT code, with pure repositioning kept separate so it cannot bury a real
change. Paired with `td_snapshot` that is an audit trail for agent edits.

The file formats are undocumented, so the readers were derived by measurement:
the payload prologue is a fixed 27 bytes with a length field (scanning for a
newline instead corrupts about a third of the shaders), bit 4 of a parameter's
flags word marks expression mode, and the 71 saved-name aliases come from
instantiating every type and comparing what TouchDesigner writes with what it
reports.

## The network text, written on save

The bridge can write the network out as text beside the `.toe` every time the
artist saves, so a project gets a diffable history in git without anybody
remembering to ask for one. It is **off by default** — writing a file into
somebody's own project folder is not something to start doing unasked — and
turning it on is one key in `~/.td-atlas/config.json`:

```json
{ "text_on_save": true }
```

The file is `<project>.network.json` beside the `.toe`, in the same format
`td-atlas project text` produces. The version number TouchDesigner adds on
each save is stripped, so one project keeps one text and git holds the
history. The write is atomic (a temporary in the same directory, then a
rename), and a file already at that name that is not one of ours is never
overwritten — the bridge refuses and says so on its status panel.

Measured on 2025.32460, the text costs about 0.25–0.30 ms per operator, so
networks are covered up to a cap of 2000 operators — beyond that the save
would grow by more than half a second and the bridge writes nothing, again
saying so on the panel. Raise it with `"text_on_save_max_ops"` if you would
rather wait.

The text covers the artist's own root components. TouchDesigner's `/local` and
`/perform`, the bridge's own `/tdatlas`, and the external `.tox` roots `/ui`
and `/sys` are left out — each by a measured rule the handler's comments give.

This text is printed from the live network, and the one `td-atlas project text`
prints is read from the expanded file, so the two are not byte-identical. Seven
classes of difference are known and nothing else was left over: measured on
2025.32460 over a purpose-built network of 45 operators — 21 built by the
fixture, the other 24 the annotation component's own subtree — **104 of 722
compared fields differ**.

| fields | class | why |
| --- | --- | --- |
| 80 | custom parameter placement | The file keeps a custom parameter's *value* in `.parm` beside the built-in ones and its *definition* in `.cparm`, which the reader does not parse — so the reader files the value under `parms` where the live side files it under `custom_parms`. 40 parameters, two fields each. |
| 10 | custom parameter at its default | A custom parameter still at its default has no `.parm` line at all, so the file side has nothing to show; the live side prints every custom parameter. |
| 8 | float text formatting | The file keeps TouchDesigner's own printing (`2e+06`), the live side prints `2000000`. |
| 3 | parameter at its default with a flags word | `.parm` carries a line for a default-valued parameter whose flags word is not zero; the live side drops anything `isDefault`. |
| 1 | COMP input wiring | A COMP's operator input is stored in a `.network` file, which the offline reader does not parse, so the file-side text shows no inputs where the live-side text shows them. |
| 1 | flag vocabulary | `.n` flags the live gather has no name for (`showDocked`). |
| 1 | the `.tox` save's own root parameter | `enableexternaltox` is written into the root of a saved `.tox` and of no `.toe` — an artefact of how the measurement was taken, not of the text. |

None of those numbers is a memory: `tests/live_network.py` builds the network
again and `tests/test_live_text_diff.py` fails if a field falls outside these
seven classes. It skips where no TouchDesigner is running.

Two more classes stood here until the measurement found their cause, and both
were bugs of ours rather than limits of the text. The `.table` header's row and
column counts were read the wrong way round, so a 3×2 table came back as 2×3 —
and the round trip had never caught it, because the writer repeated the same
swap. And a panel COMP's wire to the COMP beside it sits in the file's `inputs`
block while live it hangs off `inputCOMPConnectors`, which the live gather did
not read; it now reads both connector lists, each wire under its own
connector's index. Both sides are fixed.

## Install

**Platforms.** Everything here was developed and measured on macOS. Windows is
supported by intent but **unverified**: no Windows machine was involved. The
install-discovery layout follows Derivative's published install tree rather
than measurement; `toeexpand`'s path separators are inferred from its macOS
output; the clipboard copy (`clip`) has never been run. And one thing is known
to be weaker there — the token file is narrowed with `chmod`, which on Windows
sets only the read-only attribute and does not keep other accounts on the
machine out. Linux is not supported: TouchDesigner is not released for it.

```bash
uv pip install -e .
td-atlas build          # offline index
td-atlas install        # stage the bridge, print the bootstrap and MCP lines
```

`td-atlas install` prints two things to paste. First, into TouchDesigner's
textport (Dialogs → Textport and DATs), once per project:

```python
exec(open('/Users/you/.td-atlas/bootstrap.py').read())
```

Second, a `claude mcp add` line for your MCP client — see below. Pass
`--write-mcp-json DIR` to additionally write (or merge into) `DIR/.mcp.json`
with that same entry.

Then complete the index with runtime facts:

```bash
td-atlas probe
```

## As an MCP server

Run `td-atlas install` and paste the `claude mcp add …` line it prints — it
points at the current interpreter by absolute path, so it keeps working
regardless of the MCP client's own working directory or whether any
virtualenv is activated. Wiring it in by hand looks like:

```bash
claude mcp add td-atlas -- /path/to/python -m td_atlas.cli mcp
```

41 tools in three groups — see `skills/touchdesigner/references/tools.md`.

**Index** (offline): `td_search_operators`, `td_operator_schema`,
`td_search_parameters`, `td_python_api`, `td_docs`, `td_glossary`,
`td_palette`, `td_expression_help`, `td_example`.

**Live**: `td_status`, `td_health`, `td_network`, `td_op_info`, `td_build`,
`td_set_params`, `td_render`, `td_errors`, `td_exec`, `td_undo`, `td_snapshot`.

**Project files**: `td_project_read`, `td_project_grep`, `td_project_diff`.

`td_build` and `td_set_params` validate parameter names against the index
before sending, so the usual mistakes come back as corrections:

```
- t: is a parameter group, not a settable parameter (try: tx, ty, tz)
- typ: no such parameter (try: type, ty)
- type: 'simplex5d' is not a valid menu entry (try: simplex4d, simplex3d, ...)
- period: -3 is below the clamped minimum 0.0
```

## The skill

`skills/touchdesigner/` is an agent-facing guide: how to work through the
connector, and a reference of every trap that produced no error while costing
real time — dormant branches, CPU operators hiding in a menu, feedback loops
that converge to grey, an audio codec that reports success and writes no file,
a licence that halves your resolution without saying so.

It installs as a plugin rather than by copying the directory, so that updating
it is one command instead of a second `cp` nobody remembers to run. This
repository is its own marketplace:

```
/plugin marketplace add grigabyte/td-atlas
/plugin install touchdesigner@td-atlas
```

Later, `/plugin marketplace update` pulls in whatever the skill has learned.

## As a bundle

`.mcpb` is an MCP Bundle: a zip holding a local MCP server plus a
`manifest.json` describing it, which a supporting client installs when you
open the file. Build one from a clean checkout:

```bash
.venv/bin/python scripts/build_mcpb.py
```

It writes `dist/td-atlas-<version>.mcpb`, regenerates `packaging/manifest.json`
from `pyproject.toml`, and writes `dist/server.json` — the submission for the
official MCP registry, carrying the SHA-256 of the bundle built beside it.
Publishing is a separate, deliberate step: `scripts/publish.sh`.

Two things the bundle does not carry, and cannot: the index, which is built on
your machine from your installation and holds machine-specific values, and the
bridge, which needs `td-atlas install` and one pasted line. So a bundle install
gives you the project-file tools immediately and tells you which command
unlocks the rest.

## Documentation

| Document | For |
| --- | --- |
| [`skills/touchdesigner/SKILL.md`](skills/touchdesigner/SKILL.md) | Agents *using* the connector |
| [`skills/touchdesigner/references/gotchas.md`](skills/touchdesigner/references/gotchas.md) | Every trap that produced no error |
| [`skills/touchdesigner/references/tools.md`](skills/touchdesigner/references/tools.md) | All 41 MCP tools |
| [`AGENTS.md`](AGENTS.md) | Agents *contributing to* this repository |
| [`docs/architecture.md`](docs/architecture.md) | How the three layers fit together, and why |
| [`docs/formats.md`](docs/formats.md) | The reverse-engineered `.toe`/`.tox` format, with evidence |

## Development

```bash
uv pip install -e . pytest
pytest                  # three tests need an installation, none a running instance
td-atlas reload         # re-stage the bridge and reload it through itself
```

See [AGENTS.md](AGENTS.md) before changing anything — particularly the code
that runs inside TouchDesigner, which is Python 3.11 with no third-party
imports and a hard rule against blocking.

## Layout

```
td-atlas/
├── README.md               this file
├── AGENTS.md               contributor guide, human or agent
├── LICENSE                 MIT
├── pyproject.toml
├── docs/
│   ├── architecture.md     the three layers and the reasoning
│   └── formats.md          the undocumented .toe format, measured
├── .claude-plugin/         this repository as a plugin, and as its marketplace
│   ├── plugin.json
│   └── marketplace.json
├── packaging/manifest.json the MCPB manifest, generated from pyproject.toml
├── scripts/
│   ├── build_mcpb.py       build the bundle and the registry submission
│   └── publish.sh          the one step that sends anything outward
├── skills/touchdesigner/   the agent skill, installed as the plugin above
│   ├── SKILL.md
│   └── references/{gotchas,tools}.md
├── src/td_atlas/
│   ├── install.py          locate a TouchDesigner installation
│   ├── config.py           the ~/.td-atlas handshake between host and TD
│   ├── cli.py              command line, parity with the MCP tools
│   ├── journal.py          the append-only trail of bridge calls
│   ├── atoms/              the offline index
│   │   ├── extract_static.py   pass over the app bundle
│   │   ├── probe.py            runtime pass, incl. type-alias derivation
│   │   ├── htmltext.py         wiki HTML -> text, with categories
│   │   ├── validate.py         parameter checking before anything is sent
│   │   └── store.py            SQLite schema, FTS5 search and ranking
│   ├── bridge/             talking to a running instance
│   │   ├── client.py           JSON-RPC over HTTP, stdlib only
│   │   ├── health.py           the silent-failure detector
│   │   └── filmstrip.py        contact sheets, with a stdlib PNG encoder
│   ├── component/          code that runs *inside* TouchDesigner
│   │   ├── bootstrap.py        builds the bridge network in place
│   │   └── handler.py          the RPC handler
│   ├── project/            reading .toe/.tox without TouchDesigner
│   │   ├── expand.py           driving toeexpand/toecollapse on a copy
│   │   ├── formats.py          the undocumented file formats
│   │   ├── model.py            the operator tree and type resolution
│   │   ├── diff.py             semantic comparison
│   │   └── render.py           tree description and code search
│   └── mcp/server.py       40 MCP tools over all three layers
└── tests/                  three need an installation, none a running instance
```

## Licence

MIT — see [LICENSE](LICENSE). TouchDesigner is a product of Derivative Inc.;
this project is not affiliated with them and redistributes nothing from the
installation, it only reads what is already on your machine.
