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

**Static pass** — offline, no TouchDesigner process, about 12 seconds:

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

## Reading projects offline

TouchDesigner ships `toeexpand`, which unpacks a `.toe`/`.tox` into a tree of
text files. td-atlas reads that tree, so a project can be inspected, searched
and compared **without TouchDesigner running** — and without touching the
original, since everything happens on a copy in a cache.

```bash
td-atlas project read  myproject.toe --path /project1 --params
td-atlas project grep  myproject.toe "def onValueChange"
td-atlas project diff  before.toe after.toe
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

23 tools in three groups — see `skills/touchdesigner/references/tools.md`.

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
| [`skills/touchdesigner/references/tools.md`](skills/touchdesigner/references/tools.md) | All 23 MCP tools |
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
│   └── mcp/server.py       23 MCP tools over all three layers
└── tests/                  three need an installation, none a running instance
```

## Licence

MIT — see [LICENSE](LICENSE). TouchDesigner is a product of Derivative Inc.;
this project is not affiliated with them and redistributes nothing from the
installation, it only reads what is already on your machine.
