# The command line

`td-atlas` is the same functionality the MCP tools expose, for a person at a
terminal. This page is the complete surface: every subcommand, every flag, and
what each one needs before it can work. It was read off `--help` and
`build_parser()` on 2026-09-07.

Getting installed is in [the README](../README.md#install); what to run when
something is broken is in [Troubleshooting](../README.md#troubleshooting). This
page assumes both and does not repeat them.

Unless the virtualenv is activated, call the command by path —
`.venv/bin/td-atlas`, or `.venv\Scripts\td-atlas` on Windows. A system Python
will not see the package, and `td-atlas doctor` will say so at its `mcp server`
link rather than leaving you guessing. Everything below writes `td-atlas` for
short.

## Global flags go before the subcommand

```bash
td-atlas --db /tmp/other.db op noiseTOP        # right
td-atlas op noiseTOP --db /tmp/other.db        # not a flag of `op`
```

| Flag | What it does |
| --- | --- |
| `--db PATH` | use this atom index instead of `~/.td-atlas/atlas.db` |
| `--port N` | talk to the bridge listening on this port |
| `--project SUBSTR` | talk to the running project whose name or path contains this |

`--port` and `--project` are **selectors** among several running instances —
see `td-atlas instances`, which lists them and marks the one these flags would
reach. Six commands reach a bridge and accept a selector: `probe`, `reload`,
`status`, `exec`, `render`, `doctor`. Given to any other, a selector is an
error rather than silence, because a flag that is quietly ignored is
indistinguishable from one that worked:

```
$ td-atlas --port 9977 search noise
error: --port before the subcommand selects which running TouchDesigner to
talk to, and 'search' does not talk to one — so the flag would be ignored.
```

`install --port` is a different flag with the same name, and it is not a
selector: it is the port the bridge being staged will bind (default 9977).

## What each command needs

The three layers are separable on purpose, and this is the table that says so.
"Bridge" means TouchDesigner running with the bridge component in it.

| Command | Needs |
| --- | --- |
| `install`, `instances`, `status`, `doctor`, `log`, `mcp` | nothing — they report on what is and is not there. `status` and `doctor` reach a bridge when one is up and name its absence when it is not |
| `build`, `release-tox`, every `project` action | TouchDesigner **installed** (they read the bundle, or shell out to `toeexpand`) |
| `search`, `op` | an index (`td-atlas build`) |
| `probe` | an index **and** a live bridge |
| `exec`, `render`, `reload` | a live bridge |

`instances` is the one command that answers a question about bridges without
making a bridge call: it reads the registry in `~/.td-atlas` and checks each
entry the way both sides can check it — does the port accept a connection,
does the process still exist. It opens that connection and closes it again
without sending a request, so no token and no protocol version are involved,
and it still answers when every bridge is dead or too old to talk. Which is
when you need it.

The `project` actions read an index when one exists and work without it; the
index only sharpens type resolution, because a saved file stores contracted
type names (`tox` for `textTOP`) that the index expands.

## Setting up and checking

### `td-atlas install`

Stages `bootstrap.py` and `handler.py` into `~/.td-atlas`, mints a token, and
prints two lines to paste: the `exec(open(...).read())` line for
TouchDesigner's textport, and the `claude mcp add` line for an MCP client.
Re-running it upgrades the staged sources in place; re-pasting the bootstrap
line upgrades the bridge inside a project in place.

| Flag | |
| --- | --- |
| `--port N` | the port the bridge will bind (default 9977) |
| `--no-auth` | stage without a token, so the bridge accepts any local caller |
| `--write-mcp-json DIR` | also add the server to `DIR/.mcp.json`, merged into whatever is there and not duplicated on a second run |

### `td-atlas release-tox`

Builds the bridge as a drag-and-drop `.tox` — the second install path, for
someone who would rather not touch a textport. No token is baked in; the
handler reads it from `~/.td-atlas/config.json` when its server starts, which
is why one file works on every machine.

| Flag | |
| --- | --- |
| `-o, --output PATH` | where to write (default `release/TdAtlas.tox`; the directory is created) |

### `td-atlas doctor`

Walks the chain link by link — environment, TouchDesigner, index, index build,
probe, bridge, MCP server — prints the command that repairs each broken link,
and exits non-zero when one is broken. It also reports how many `.toe`/`.tox`
expansions the read cache holds.

| Flag | |
| --- | --- |
| `--install-path PATH` | check against this TouchDesigner directory |
| `--clear-cache` | delete every cached expansion. The cache is capped and evicted least-recently-used on its own; this is the only way to take back a cache that has already grown, because it is disk that belongs to you |

### `td-atlas status`

Installation, index and bridge state in a few lines, with no exit-code
opinion. The line of index counts it prints is the canonical size of the
corpus on this machine: those numbers belong to your TouchDesigner build, not
to any document here.

### `td-atlas instances`

Every running TouchDesigner that has registered a bridge, with the port and
project of each, and how to aim `--port`/`--project` at one.

### `td-atlas mcp`

Runs the MCP server on stdio. It is how the tools exist, so it is not one of
them; MCP clients start it themselves from the line `install` prints.

## Building the index

### `td-atlas build`

The offline pass: reads the installed application bundle and writes the index.
23–30 seconds, no TouchDesigner process, no network.

| Flag | |
| --- | --- |
| `--install-path PATH` | build from this TouchDesigner directory instead of the discovered one |
| `--no-probe` | suppress the reminder to run `probe` next |

### `td-atlas probe`

The runtime pass: instantiates every operator type inside a sandbox with
cooking disabled and records what documentation does not — defaults, ranges
and clamps, menu options, parameter pages, connector counts, and the
contracted type names. Needs TouchDesigner open with the bridge, and occupies
its cook for about a minute.

| Flag | |
| --- | --- |
| `--chunk N` | operator types per bridge call (default 40) |

### `td-atlas reload`

Re-stages the component sources and has the running bridge replace its own
handler — the fast loop when you are editing
`src/td_atlas/component/handler.py`. It is the one command that talks to a
bridge the version check would otherwise reject, since replacing an outdated
handler is exactly its job; a mismatch is printed as a warning.

## Reading the index

### `td-atlas search QUERY`

Full-text search over operators, ranked names and labels first and widening to
body text only when the result set is thin.

| Flag | |
| --- | --- |
| `--family TOP\|CHOP\|SOP\|DAT\|MAT\|COMP` | narrow to one family |
| `--limit N` | how many results (default 15) |

### `td-atlas op TYPE`

An operator's full schema: every parameter with its type, default, range,
menu entries and page.

| Flag | |
| --- | --- |
| `--page NAME` | only parameters on this page |
| `--groups` | also list the documented parameter *groups* (`t`) and their members (`tx`, `ty`, `tz`). Off by default because a group is not settable — this flag is for cross-reading Derivative's own documentation, which names groups |
| `--include-hidden` | also show parameters TouchDesigner hides in its UI |

## The live instance

### `td-atlas exec CODE`

Runs Python inside TouchDesigner and prints stdout, stderr and the result.
`-` reads the source from stdin. It is arbitrary Python: unlike a batch of
edits, it carries no rollback.

### `td-atlas render PATH`

Saves a TOP's image to a file.

| Flag | |
| --- | --- |
| `-o, --output PATH` | where to write (default `render.png`) |
| `--width N`, `--height N` | resize; omitted, the TOP's own resolution is used |

### `td-atlas log`

The trail of bridge calls from `~/.td-atlas/calls.jsonl`, written by the host
and outliving the session. Parameters are never logged and the bridge token is
scrubbed from every line.

| Flag | |
| --- | --- |
| `-n, --number N` | how many of the most recent calls (default 20) |
| `--failures` | only the calls that were refused, with the repair for the last one |
| `--summary` | where calls fail most and what was slowest, over the whole journal |
| `--method M` | only this bridge method |

## Project files, without TouchDesigner running

`td-atlas project` reads, searches, compares and rewrites `.toe`/`.tox` files
from disk. Every action works on a **copy** in a cache keyed by the file's
path, size and mtime: both helpers work in place — `toeexpand` writes
`<name>.tox.dir` and `<name>.tox.toc` beside its input, and `toecollapse`
moves an existing target aside to `<name>.tox.bkp1`, or `<name>.tox.bkp2` on a
second run — so nothing here ever touches your file. A changed
project is re-expanded without being asked; `--refresh` is for a cache damaged
from outside this program.

| Action | |
| --- | --- |
| `project read FILE` | the operator tree. `--path /project1` for one subtree, `--depth N` (default 2), `--params` to include parameter values, `--refresh` |
| `project text FILE` | the whole network as JSON — types, positions, flags, wiring, parameters and DAT contents, losslessly. `--path`, `-o/--output FILE`, `--refresh` |
| `project write FILE --text JSON -o OUT` | edited JSON back into a `.toe`/`.tox`. The original is required, because the rebuild is a patcher and text alone cannot produce a container. `--text -` reads stdin. Refuses an output path that already exists |
| `project grep FILE PATTERN` | search the code inside the project's DATs — the Python and GLSL no file search reaches, because it lives inside the container. `--fixed` for a literal, `--limit N` (default 100) |
| `project diff FILE OTHER` | compare two projects by meaning: added, removed, retyped, rewired and re-parameterised operators, plus a line diff of changed DAT code. `--moves` lists the nodes that were only dragged, which are counted separately so a mouse movement does not bury the real change; `--no-text` skips DAT text diffs |
| `project expand FILE` | unpack into the cache and print the directory. `--refresh` |
| `project collapse DIR -o OUT` | repack a `<name>.tox.dir` directory into a container |
| `project scripts FILE -o DIR` | write every DAT's contents out as files |

The text format, and what a round trip through it does and does not preserve,
is in [formats.md](formats.md).

### `td-atlas project variant` — saved states of one project

A variant keeps the network as text **and** a byte copy of the `.toe`/`.tox`
it came from, under `~/.td-atlas/variants/`. Both, because the rebuild is a
patcher: without the source there is nothing to restore into.

| Action | |
| --- | --- |
| `project variant save FILE --label L` | keep the current state. `--note` says why it was worth keeping, `--path` dumps only one subtree as text |
| `project variant list [FILE]` | what has been saved, for one project or for all |
| `project variant restore FILE --label L -o OUT` | write a saved state out. `-o` may be a directory, which keeps the original file name |
| `project variant diff FILE --label L --other M` | compare two saved states of the same project. `--moves`, `--no-text` as in `project diff` |

A source file that has changed since a variant was saved is not a reason to
refuse the restore; the drift is reported instead.

## The MCP side

Seventeen capabilities on this page have an MCP tool that does the same thing,
counting each `project` action separately; the two surfaces then differ in
twenty-four tools and nine subcommands. Which, and why
each gap exists, is declared in
[AGENTS.md](../AGENTS.md#cli--mcp-parity) and held to the code by a test. The
tools themselves are listed in
[`plugin/skills/touchdesigner/references/tools.md`](../plugin/skills/touchdesigner/references/tools.md).

One difference is about the global flags rather than any one capability: the
MCP surface cannot aim at a chosen instance. `--port`/`--project` have no tool
equivalent; every live tool reaches whatever bridge discovery picks, and warns
in every reply when more than one is running.
