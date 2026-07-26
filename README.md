# td-atlas

An atomised index of TouchDesigner, a live bridge into a running instance, and
an offline reader for saved projects — exposed to AI agents over MCP.

Existing agent connectors for TouchDesigner are thin RPC layers: a Web Server
DAT, an `exec(python)` endpoint, and a handful of CRUD calls. That is enough to
*poke* TouchDesigner and not enough to *build* in it. An agent driving those
tools guesses parameter names, cannot see what it made, leaves half-applied
networks behind when a step fails, and — worst of all — has no way to notice
that what it built is not running.

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

## What nothing else catches

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

```bash
uv pip install -e .
td-atlas build          # offline index
td-atlas install        # stage the bridge, print the bootstrap line
```

Paste the printed line into TouchDesigner's textport (Dialogs → Textport and
DATs), once per project:

```python
exec(open('/Users/you/.td-atlas/bootstrap.py').read())
```

Then complete the index with runtime facts:

```bash
td-atlas probe
```

## As an MCP server

```bash
claude mcp add td-atlas -- /path/to/td-atlas/.venv/bin/td-atlas mcp
```

23 tools in three groups — see `skill/touchdesigner/references/tools.md`.

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

`skill/touchdesigner/` is an agent-facing guide: how to work through the
connector, and a reference of every trap that produced no error while costing
real time — dormant branches, CPU operators hiding in a menu, feedback loops
that converge to grey, an audio codec that reports success and writes no file,
a licence that halves your resolution without saying so.

Install it where your agent looks for skills, e.g.:

```bash
cp -r skill/touchdesigner ~/.claude/skills/
```

## Development

```bash
uv pip install -e . pytest
pytest                  # 67 tests; only one needs TouchDesigner
td-atlas reload         # re-stage the bridge and reload it through itself
```

## Layout

```
src/td_atlas/
  install.py          locate a TouchDesigner installation
  config.py           the ~/.td-atlas handshake between host and TD
  atoms/
    extract_static.py offline pass over the app bundle
    probe.py          runtime pass, incl. saved-name alias derivation
    htmltext.py       wiki HTML -> agent-readable text, with categories
    validate.py       parameter checking before anything is sent
    store.py          SQLite schema, FTS5 search and ranking
  bridge/
    client.py         host-side JSON-RPC client
    health.py         the silent-failure detector
    filmstrip.py      contact sheets, with a stdlib PNG encoder
  component/          code that runs *inside* TouchDesigner
    bootstrap.py      builds the bridge network in place
    handler.py        the RPC handler
  project/            reading .toe/.tox without TouchDesigner
    expand.py         driving toeexpand/toecollapse on a copy
    formats.py        the undocumented file formats
    model.py          the operator tree and type resolution
    diff.py           semantic comparison
    render.py         tree description and code search
  mcp/server.py       MCP tools over all three layers
skill/touchdesigner/  agent-facing guide and gotchas reference
```
