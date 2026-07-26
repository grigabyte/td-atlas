# Architecture

## The premise

Two observations shape everything here.

**Almost everything an agent needs to know about TouchDesigner already ships
inside the application.** A 5 MB parameter database, a 182 MB offline mirror of
the entire wiki, 483 worked examples, 277 finished components. Deriving the
index from the installed build means it is exact for that build, rather than
scraped from a wiki that may describe a different release — and it means the
index can be rebuilt in twelve seconds with no network.

**TouchDesigner reports almost nothing when work silently does nothing.** A
branch nothing displays never cooks. An output device switched off produces
silence. A CPU-bound operator drags the frame rate to 3. A Non-Commercial
licence halves a resolution. None of these raise an error, and an agent has no
eyes on the node graph. This is the failure mode the connector exists to close,
and it is why `td_health` is not an afterthought.

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

Each layer stands alone. The index answers without TouchDesigner running; the
project reader parses files without it too. Only the bridge needs a live
instance, and its failure is reported rather than fatal.

## The index

Two passes into one SQLite database with FTS5.

**Static** reads the bundle: `TDParameterHelp.json`, the wiki mirror, the
palette, the snippet library, the expression help. It yields prose — what an
operator is for, what a parameter means.

**Runtime** instantiates every operator type inside a sandbox with cooking
disabled and reads what documentation does not record: defaults, ranges and
clamps, menu options, parameter pages and their order, connector counts. It
also derives the 71 contracted type names by saving the sandbox and reading
back what TouchDesigner wrote.

Cooking is disabled on the sandbox deliberately: creating a Video Device In TOP
that is allowed to cook opens a camera.

The passes are complementary in both directions. The help file documents 654
types; the application exposes 647, and 13 of those are absent from the help.
The help documents parameter *groups* (`t`) where only members (`tx`, `ty`,
`tz`) are settable, so members inherit the group's prose and groups are marked
unsettable.

## The bridge

A Web Server DAT and a callbacks DAT, **built by a script rather than shipped
as a `.tox`**. The consequences are worth the small extra work: the bridge is
readable and diffable, it lives in version control as source, re-running the
bootstrap upgrades it in place, and the running bridge can replace its own
handler (`td-atlas reload`) without sending anyone back to the textport.

Host and TouchDesigner meet in `~/.td-atlas`: the host writes the component
sources and a config with a generated token, and the bootstrap writes back a
session file naming the port it bound. That handshake is why the client needs
no configuration.

Three design choices earn their keep:

- **Batches run inside `ui.undo`.** A failed batch rolls back and leaves no
  partial network; a successful one is a single Ctrl+Z for the artist, exactly
  like a hand edit.
- **Parameters are validated against the index before anything is sent.** The
  four common mistakes — a group name, a typo, an invalid menu entry, a value
  outside a clamp — come back as corrections instead of tracebacks.
- **Rendering returns pixels.** TouchDesigner is a visual tool; an agent that
  cannot see its output is working blind. For anything time-based, a single
  frame is a coin toss, so `bridge/filmstrip.py` samples over wall-clock time
  and has TouchDesigner tile the frames with numpy.

A request runs on TouchDesigner's main thread during a cook, which bounds what
a handler may do and means frames cannot advance inside one call. That is why
contact sheets and health checks are driven from the host in two or more calls.

## The project reader

`toeexpand` turns a container into a tree of small text files. Reading it gives
structure, wiring, parameters and — usually the part that matters — the Python
and GLSL held inside DATs, which no file search can reach because it lives
inside the container.

Everything happens on a copy in a cache keyed by path, size and mtime, so
reading a project never modifies the original or litters beside it.

`diff` compares meaning rather than bytes: added, removed, retyped, rewired and
re-parameterised operators, plus a line diff of changed DAT code. Nodes that
were only dragged are counted separately, because otherwise every mouse
movement buries the real change.

The undocumented formats are described in [formats.md](formats.md).

## Health

`errors` covers what TouchDesigner calls an error. `bridge/health.py` covers
what it does not, by taking two samples a moment apart and comparing each
operator's cook count against the frame clock.

It also declines to cry wolf. A paused timeline and a backgrounded window both
freeze the frame clock, and everything then looks dormant — so those are named
for what they are rather than reported as a dead network. A false alarm here
would cost an agent exactly the time the tool exists to save.

## Design rules

- **Measure, do not guess.** Nearly everything this project depends on is
  undocumented. Each derived fact carries the evidence in a comment.
- **An honest gap beats a confident wrong answer.** Two heuristics were dropped
  for this reason; both looked like complete success while being wrong.
- **Never write beside a user's file.** The TouchDesigner tools do; this
  project copies first.
- **Keep the offline layers offline.** 66 of 67 tests run without
  TouchDesigner. Tests that need a running application cannot be trusted to
  run.
