# Architecture

## The premise

**Almost everything an agent needs to know about TouchDesigner already ships
inside the application.** A 5 MB parameter database, a 182 MB offline mirror of
the entire wiki, 483 worked examples, 277 finished components. An index derived
from the installed build is exact for that build, and it rebuilds in under half
a minute with no network (25.8 s wall clock, measured 2026-09-06 on build
2025.32460).

**TouchDesigner reports almost nothing when work silently does nothing.** A
branch nothing displays never cooks. An output device switched off produces
silence. A CPU-bound operator drags the frame rate to 3. A Non-Commercial
licence halves a resolution. None of these raise an error, and an agent has no
eyes on the node graph. Closing that failure mode is what the connector is for,
and `td_health` is the check that looks for it.

## Three layers

```
                     ┌─────────────────────────────────────┐
                     │            mcp/server.py            │
                     │        cli.py (same, for humans)    │
                     └──┬──────────────┬─────────────────┬─┘
                        │              │                 │
              ┌─────────▼───────┐  ┌───▼──────────┐  ┌───▼──────────┐
              │   atoms/        │  │   bridge/    │  │   project/   │
              │   the index     │  │   live TD    │  │   files      │
              └─────────┬───────┘  └───┬──────────┘  └───┬──────────┘
                        │              │                 │
                  atlas.db        HTTP + token       toeexpand
                        │              │                 │
                  ┌─────▼──────┐  ┌────▼─────────┐  ┌────▼─────────┐
                  │ app bundle │  │ component/   │  │ .toe / .tox  │
                  │ (offline)  │  │ inside TD    │  │ (on disk)    │
                  └────────────┘  └──────────────┘  └──────────────┘
```

Each layer stands alone. The index answers with TouchDesigner closed, and the
project reader parses files with it closed too. Only the bridge needs a live
instance, and a missing bridge comes back as a report.

## The index

Two passes into one SQLite database with FTS5.

**Static** reads the bundle: `TDParameterHelp.json`, the wiki mirror, the
palette, the snippet library, the expression help. It yields prose, what an
operator is for and what a parameter means.

**Runtime** instantiates every operator type inside a sandbox with cooking
disabled and reads what documentation does not record: defaults, ranges and
clamps, menu options, parameter pages and their order, connector counts. It
also derives the 71 contracted type names by saving the sandbox and reading
back what TouchDesigner wrote.

Cooking is disabled on the sandbox. A Video Device In TOP that is allowed to
cook opens a camera.

Each pass finds what the other misses. The help file documents 654 types; the
application exposes 647, and 13 of those are absent from the help. The help
documents parameter *groups* (`t`) where only members (`tx`, `ty`, `tz`) are
settable, so members inherit the group's prose and groups are marked
unsettable.

## The bridge

A Web Server DAT and a callbacks DAT, **built by a script**. The bridge is
readable and diffable, it lives in version control as source, and re-running
the bootstrap upgrades it in place. A running bridge can replace its own
handler with `td-atlas reload`, and nobody goes back to the textport.

Host and TouchDesigner meet in `~/.td-atlas`. The host writes the component
sources and a config with a generated token, and the bootstrap writes back a
session file naming the port it bound. That handshake leaves the client with
nothing to configure.

Three more behaviours:

- **Batches run inside `ui.undo`.** A failed batch rolls back and leaves no
  partial network; a successful one is a single Ctrl+Z for the artist, exactly
  like a hand edit.
- **Parameters are validated against the index before anything is sent.** A
  group name, a typo, an invalid menu entry, a value outside a clamp. Those
  four common mistakes come back as corrections.
- **Rendering returns pixels.** TouchDesigner is a visual tool, and an agent
  that cannot see its output is working blind. For anything time-based a single
  frame is a coin toss, so `bridge/filmstrip.py` samples over wall-clock time
  and has TouchDesigner tile the frames with numpy.

A request runs on TouchDesigner's main thread during a cook, which bounds what a
handler may do. Frames cannot advance inside one call, so contact sheets and
health checks are driven from the host in two or more calls.

## Which instance, and whether it is there

Several TouchDesigner processes can run at once, each with its own bridge on
its own port. `~/.td-atlas` also holds a registry of them. An entry counts as
**live** when the port is listening *and* the process still exists. Those are
the two facts host and bridge can check identically.

The registry does not record the process *name*. From inside TouchDesigner you
see its embedded interpreter, and from outside you see the application.

Registration happens on whichever install path was used. Through the released
`.tox` the Web Server DAT's `onServerStart` callback fires and does it. Through
the pasted bootstrap line it does not. Toggling `active` from a script does not
fire that callback, measured, so `bootstrap.py` registers the instance itself.

`td-atlas instances` is therefore the one command that answers a question about
bridges without making a bridge call. It opens the port and closes it again
without sending a request, so neither the token nor the protocol version is
involved, and an unreachable or outdated bridge is still listed. The CLI's
`--port`/`--project` select among them. The MCP surface has no equivalent, for
the reason `AGENTS.md` gives.

## The project reader

`toeexpand` turns a container into a tree of small text files. Reading it gives
structure, wiring, parameters, and the Python and GLSL held inside DATs. That
last one is usually the part that matters, and no file search reaches it,
because it lives inside the container.

Everything happens on a copy in a cache keyed by path, size and mtime, so
reading a project never modifies the original or litters beside it.

`diff` compares meaning. It lists added, removed, retyped, rewired and
re-parameterised operators, plus a line diff of changed DAT code. Nodes that
were only dragged are counted separately, so a mouse movement cannot bury the
real change.

The undocumented formats are described in [formats.md](formats.md).

## Health

`errors` covers what TouchDesigner calls an error. `bridge/health.py` covers
what it does not, by taking two samples a moment apart and comparing each
operator's cook count against the frame clock.

A paused timeline and a backgrounded window both freeze the frame clock, and
everything then looks dormant. Those two are named for what they are, and
neither is reported as a dead network.

## Design rules

- **Measure, do not guess.** Nearly everything this project depends on is
  undocumented. Each derived fact carries the evidence in a comment.
- **An honest gap beats a confident wrong answer.** Two heuristics were dropped
  for that reason. Both looked like complete success while being wrong.
- **Never write beside a user's file.** The TouchDesigner tools do; this
  project copies first.
- **Keep the offline layers offline.** A plain `pytest` needs no running
  TouchDesigner at all. `pyproject.toml` deselects the `live` marker, so a test
  that dials an instance is collected only when it is asked for by name. Some
  tests need TouchDesigner *installed* and skip without it. Those stay in the
  default run, and the same checks pass on any machine with the application
  present.
