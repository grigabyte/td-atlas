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

Fifteen tools, in two groups. The index tools cost nothing and need no running
TouchDesigner — `td_search_operators`, `td_operator_schema`,
`td_search_parameters`, `td_python_api`, `td_docs`, `td_expression_help`. The
bridge tools act on the live application — `td_status`, `td_network`,
`td_op_info`, `td_build`, `td_set_params`, `td_render`, `td_errors`,
`td_exec`, `td_undo`.

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
pytest                  # 34 tests, none need TouchDesigner
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
  mcp/server.py       MCP tools over both layers
```
