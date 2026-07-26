# td-atlas

An atomised index of TouchDesigner, plus a live bridge into a running instance,
exposed to AI agents over MCP.

Existing agent connectors for TouchDesigner are thin RPC layers: a Web Server
DAT, an `exec(python)` endpoint, and a handful of CRUD calls. That is enough to
*poke* TouchDesigner and not enough to *build* in it. An agent driving those
tools guesses parameter names, cannot see what it made, and leaves half-applied
networks behind when a step fails.

td-atlas is built around a different premise: **almost everything an agent needs
to know about TouchDesigner already ships inside the application.**

## The atom index

Two passes produce one SQLite index, exact for the build it was made from
rather than scraped from a wiki describing some other release.

**Static pass** — offline, no TouchDesigner process, about 12 seconds:

| Source in the app bundle | What it yields |
| --- | --- |
| `Config/TDParameterHelp.json` | 654 operators, 16,584 parameters: labels, prose, types |
| `Samples/Learn/OfflineHelp/` | 2,060 wiki pages, including 656 Python class references |
| `Samples/Learn/OPSnippets/` | 483 working example networks, one per operator |
| `Config/Help/{command.help,exprhelp}` | 490 command and expression entries |

**Runtime pass** — instantiates every operator type inside a non-cooking
sandbox and reads what documentation does not record: defaults, numeric ranges
and clamps, **menu options**, parameter pages and ordering, connector counts.
Cooking is disabled on the sandbox so that creating a Video Device In TOP does
not open a camera.

The result is a schema an agent can consult offline, before it touches
TouchDesigner at all.

## The bridge

A Web Server DAT and a callbacks DAT, built **from a script rather than shipped
as a `.tox`** — so the bridge is readable, diffable, version-controlled, and
re-running the bootstrap upgrades it in place.

Beyond `exec`, it provides what an agent actually needs:

- **`render`** — any TOP's pixels back as PNG. TouchDesigner is a visual tool;
  an agent that cannot see its output is working blind.
- **`batch`** — several operations inside one `ui.undo` block. A failed batch
  rolls back and leaves no partial network. A successful one is a single
  **Ctrl+Z** for the artist, like any other edit.
- **`errors`** — every node currently reporting an error or warning, which
  otherwise exists only as a colour on a node the agent cannot see.
- **`network`** — structured description of a component and its wiring.

Requests are authenticated with a token by default, generated on install and
read from the same file by both sides.

## Reading projects offline

TouchDesigner ships `toeexpand`, which unpacks a `.toe` or `.tox` into a tree
of small text files. td-atlas reads that tree, so a project can be inspected,
searched and compared **without TouchDesigner running at all** — and without
touching the original, since everything happens on a copy in a cache.

```bash
td-atlas project read  myproject.toe --path /project1 --params
td-atlas project grep  myproject.toe "def onValueChange"
td-atlas project diff  before.toe after.toe
td-atlas project scripts myproject.toe -o ./extracted
```

`grep` reaches code that no file search can: the Python and GLSL inside DATs
lives in the container, not on disk.

`diff` compares meaning rather than bytes — added, removed, retyped, rewired
and re-parameterised operators, plus a line diff of changed DAT code. Nodes
that were only dragged are counted separately so they cannot bury a real
change:

```
2 added, 0 removed, 1 changed, 0 moved

+ /project1/agentblur (blurTOP)  <- /project1/agentnoise
    size = 8
+ /project1/agentnoise (noiseTOP)
    period = 4.25
    tx = absTime.seconds * 0.15  [expression]
    type = perlin3d
~ /project1/displace1 (displaceTOP)
    displaceweightx: '0' -> '0.42'
```

Paired with `td_snapshot`, that becomes an audit trail: snapshot, let the agent
work, snapshot again, diff.

The file formats are undocumented, so the readers were derived by measurement
against the shipped example libraries — the payload prologue is a fixed 27
bytes with a length field (scanning for a newline instead corrupts about a
third of the shaders), and bit 4 of a parameter's flags word marks expression
mode.

## Install

```bash
uv pip install -e .
td-atlas build          # offline index
td-atlas install        # stage the bridge, print the bootstrap line
```

Paste the printed line into TouchDesigner's textport (Alt+T) once per project:

```python
exec(open('/Users/you/.td-atlas/bootstrap.py').read())
```

Then complete the index with runtime facts:

```bash
td-atlas probe
```

## Use

```bash
td-atlas status
td-atlas search "displace an image with noise"
td-atlas op noiseTOP
td-atlas exec "op('/project1').create('noiseTOP','n1').path"
td-atlas render /project1/n1 -o out.png
```

## As an MCP server

```bash
claude mcp add td-atlas -- /path/to/td-atlas/.venv/bin/td-atlas mcp
```

Or in a client's config file:

```json
{
  "mcpServers": {
    "td-atlas": {
      "command": "/path/to/td-atlas/.venv/bin/td-atlas",
      "args": ["mcp"]
    }
  }
}
```

Twenty tools, in three groups.

**Index** — offline, no running TouchDesigner: `td_search_operators`,
`td_operator_schema`, `td_search_parameters`, `td_python_api`, `td_docs`,
`td_expression_help`, `td_example` (a real network shipped with TouchDesigner).

**Live** — acting on the running application: `td_status`, `td_network`,
`td_op_info`, `td_build`, `td_set_params`, `td_render`, `td_errors`,
`td_exec`, `td_undo`, `td_snapshot`.

**Project files** — reading `.toe`/`.tox` from disk: `td_project_read`,
`td_project_grep`, `td_project_diff`.

`td_build` and `td_set_params` check parameter names against the index before
sending anything, so the usual failure modes come back as corrections rather
than as tracebacks:

```
- t: is a parameter group, not a settable parameter (try: tx, ty, tz)
- typ: no such parameter (try: type, ty)
- type: 'simplex5d' is not a valid menu entry (try: simplex4d, simplex3d, ...)
- period: -3 is below the clamped minimum 0.0
```

## Development

```bash
uv pip install -e . pytest
pytest                  # 56 tests; only one needs TouchDesigner
td-atlas reload         # re-stage the bridge and reload it through itself
```

## Layout

```
src/td_atlas/
  install.py          locate a TouchDesigner installation
  config.py           the ~/.td-atlas handshake between host and TD
  atoms/
    extract_static.py offline pass over the app bundle
    probe.py          runtime pass, driven through the bridge
    htmltext.py       wiki HTML -> agent-readable text
    store.py          SQLite schema and FTS5 search
  bridge/client.py    host-side JSON-RPC client
  component/          code that runs *inside* TouchDesigner
    bootstrap.py      builds the bridge network in place
    handler.py        the RPC handler
  project/            reading .toe/.tox without TouchDesigner
    expand.py         driving toeexpand/toecollapse on a copy
    formats.py        the undocumented file formats
    model.py          the operator tree
    diff.py           semantic comparison
    render.py         tree description and code search
  mcp/server.py       MCP tools over all three layers
```
