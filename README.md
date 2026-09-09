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

**What it does** — [The atom index](#the-atom-index) ·
[The bridge](#the-bridge) ·
[What TouchDesigner does not report](#what-touchdesigner-does-not-report) ·
[The call journal](#the-call-journal) ·
[Reading projects offline](#reading-projects-offline) ·
[The network text, written on save](#the-network-text-written-on-save)

**Getting it running** — [Install](#install) ·
[Compatibility](#compatibility) ·
[As an MCP server](#as-an-mcp-server) ·
[The skill](#the-skill) ·
[As a bundle](#as-a-bundle) ·
[Troubleshooting](#troubleshooting)

**Working on it** — [Documentation](#documentation) ·
[Development](#development) ·
[Layout](#layout) ·
[Contributing](CONTRIBUTING.md) ·
[Licence](#licence)

## The atom index

Two passes produce one SQLite index, exact for the build it was made from
rather than scraped from a wiki describing some other release.

**Static pass** — offline, no TouchDesigner process, 23–30 seconds measured
on an M-series Mac:

| Source in the app bundle | What it yields |
| --- | --- |
| `Config/TDParameterHelp.json` | every operator and parameter: labels, prose, types |
| `Samples/Learn/OfflineHelp/` | the offline wiki — Python classes, operator pages, glossary entries, concept and technique articles |
| `Samples/Palette/` | **ready-made components** — projection mappers, corner-pinners, audio analysers |
| `Samples/Learn/OPSnippets/` | working example networks, one per operator |
| `Config/Help/{command.help,exprhelp}` | command and expression entries |

**Runtime pass** — instantiates every operator type inside a non-cooking
sandbox and reads what documentation does not record: defaults, numeric ranges
and clamps, **menu options**, parameter pages and ordering, connector counts,
and the contracted type names TouchDesigner writes when it saves. Cooking is
disabled on the sandbox so that creating a Video Device In TOP does not open a
camera.

The two passes do not add up, and the runtime pass is the reason: it finds
operator types the help JSON does not describe and thousands of parameters it
does not list — mostly the ones whose defaults and menu options are only
knowable by asking a live instance.

**The counts are a property of your build, not of this README.** Run
`td-atlas status`: it prints the operators, parameters and wiki articles your
index actually holds, and that line — not this page — is what the project
treats as authoritative. For scale, on the build this README was written
against (2025.32460, macOS) it reads
`667 ops, 24251 params, 2060 articles`.

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
         /project1/AV/fb, /project1/AV/out, /project1/AV/rec
[ERROR] 1 operator(s) had their resolution silently reduced by the licence —
        the output is smaller than asked for
[ERROR] 1 output operator(s) switched off — these produce nothing and report
        no error
         /project1/AV/aout (audio output)
[WARN ] 1 operator(s) cost more than 8 ms per cook (a whole frame at
        60 fps is 16.7 ms)
         /project1/src_b (430 ms)
[note ] Non-Commercial licence: resolution is capped at 1280x1280, realtime
        H.264/H.265 export on Nvidia GPUs is unavailable, and the result may
        not be used in paid work
```

Findings from a real session, re-rendered by the printer the code has now and
wrapped for this page. The path lists are cut to what was recorded: the real
output prints up to six paths under a finding and then `+N more`, and the
clamped-resolution finding names its operators too.

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

Both samples are cut short. `--failures` ends with the mapped repair for the
most recent refusal — a `cause:` and a `fix:` line; `--summary` also names the
refusal types on a `reasons:` line and lists the five slowest calls.

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
| 80 | custom parameter placement | The file keeps a custom parameter's *value* in `.parm` beside the built-in ones and its *definition* in `.cparm`, which the reader does not parse — so the reader files the value under `parms` where the live side files it under `custom_parms`. Forty parameters, two fields each. |
| 10 | custom parameter at its default | A custom parameter still at its default has no `.parm` line at all, so the file side has nothing to show; the live side prints every custom parameter. |
| 8 | float text formatting | The file keeps TouchDesigner's own printing (`2e+06`), the live side prints `2000000`. |
| 3 | parameter at its default with a flags word | `.parm` carries a line for a default-valued parameter whose flags word is not zero; the live side drops anything `isDefault`. |
| 1 | COMP input wiring | A COMP's operator input is stored in a `.network` file, which the offline reader does not parse, so the file-side text shows no inputs where the live-side text shows them. |
| 1 | flag vocabulary | `.n` flags the live gather has no name for (`showDocked`). |
| 1 | the `.tox` save's own root parameter | `enableexternaltox` is written into the root of a saved `.tox` and of no `.toe` — an artefact of how the measurement was taken, not of the text. |

None of those numbers is a memory: `tests/live_network.py` builds the network
again and `tests/test_live_text_diff.py` fails if a field falls outside these
seven classes. It sits behind the `live` marker, so a plain `pytest` does not
collect it at all; `pytest -m live` runs it, and needs an instance with the
bridge.

Two more classes stood here until the measurement found their cause, and both
were bugs of ours rather than limits of the text. The `.table` header's row and
column counts were read the wrong way round, so a 3×2 table came back as 2×3 —
and the round trip had never caught it, because the writer repeated the same
swap. And a panel COMP's wire to the COMP beside it sits in the file's `inputs`
block while live it hangs off `inputCOMPConnectors`, which the live gather did
not read; it now reads both connector lists, each wire under its own
connector's index. Both sides are fixed.

## Install

Three things have to be there first.

- **TouchDesigner.** The index is built from *your* copy of the application and
  holds the values that copy reports, so there is nothing to download.
- **Python 3.11 or newer**, on the host.
- **An AI agent that speaks MCP**, because that is who calls these tools. This
  was built and measured against
  [Claude Code](https://docs.claude.com/en/docs/claude-code/overview) — the
  `claude mcp add` line below is its command — and any client that speaks
  [the Model Context Protocol](https://modelcontextprotocol.io) reaches the
  same tools. You talk to the agent; the agent talks to TouchDesigner.

See [Compatibility](#compatibility) for platforms and for what the connector
can change in your project.

One line, from a terminal:

```bash
curl -fsSL https://raw.githubusercontent.com/grigabyte/td-atlas/main/install.sh | sh
```

[`install.sh`](install.sh) finds a Python, clones the repository, makes a
virtualenv beside it, installs the package, builds the index and stages the
bridge — printing every command before it runs it. It asks two questions,
where to clone and whether to build the index now, and takes the default for
both when there is no terminal to ask. td-atlas itself lands in two places,
the checkout you name and `~/.td-atlas`; besides those, `uv` or `pip` fills
its own package cache as it would for any package. No sudo, no system
directory, and your shell startup files are left alone. Run it again on an existing checkout
and it updates that checkout rather than starting over. It is a POSIX shell
script, so Windows takes the sequence below instead.

By hand is the same sequence:

```bash
git clone https://github.com/grigabyte/td-atlas
cd td-atlas
uv venv                     # or: python3 -m venv .venv
uv pip install -e .         # or: .venv/bin/pip install -e .
```

There is no package on PyPI, so `pip install td-atlas` and `uvx td-atlas` will
not find anything — the checkout *is* the install. Then, from the checkout:

```bash
.venv/bin/td-atlas build      # offline index, 23–30 s, no TouchDesigner process
.venv/bin/td-atlas install    # stage the bridge, print the bootstrap and MCP lines
```

Everything below writes `td-atlas` for short. Unless the virtualenv is
activated, call it by path — `.venv/bin/td-atlas`, or
`.venv\Scripts\td-atlas` on Windows — because a system Python will not see
the package.

`td-atlas install` prints two things to paste. First, into TouchDesigner's
textport (Dialogs → Textport and DATs), once per project:

```python
exec(open('/Users/you/.td-atlas/bootstrap.py').read())
```

Second, a `claude mcp add` line for your MCP client — see below. Pass
`--write-mcp-json DIR` to additionally write (or merge into) `DIR/.mcp.json`
with that same entry.

Then, with TouchDesigner open and the bridge staged, complete the index with
the runtime facts only a live instance knows:

```bash
td-atlas probe
```

Then `td-atlas doctor`, which is the only thing here that says whether the
install actually took. It names the repair on every link that is not `ok` and
exits non-zero when one is broken, so it also tells you where you are if you
came in halfway: on a host with nothing built it reports
`index : FAIL … fix: td-atlas build` and
`bridge : warn … fix: td-atlas install`.

## Compatibility

**TouchDesigner.** Everything here was measured against build **2025.32460**.
The index is not a copy of a wiki — it is read out of the application directory
you point at, so another build gives another index; `td-atlas doctor` catches
an index built from a TouchDesigner that has since been moved, updated or
replaced, which nothing else reports. The bridge and the host agree on a
protocol version and refuse each other when they disagree, naming the side that
is behind.

**Python.** 3.11 or newer on the host. The code that runs *inside*
TouchDesigner is held to 3.11 with no third-party imports, because that is the
interpreter the application ships.

**Operating systems.**

| | |
| --- | --- |
| macOS | developed and measured here; every number in this README comes from it |
| Windows | the code paths run in CI (`windows-latest`, Python 3.11-3.14, first run 2026-09-07); **TouchDesigner on Windows is unverified** — no Windows machine with TouchDesigner on it was ever involved |
| Linux | not supported: TouchDesigner is not released for it |

What is still unverified on Windows, precisely: the install-discovery layout
follows Derivative's published install tree rather than measurement;
`toeexpand`'s path separators are inferred from its macOS output; the clipboard
copy (`clip`) has never been run. And two things are now *known* to be weaker
there. `~/.td-atlas` and the token file in it are narrowed with `chmod`, which
on Windows sets only the read-only attribute — measured: the directory reports
mode 0o777 where 0o700 was asked for, so other accounts are kept out by
whatever ACL the user profile already carries and by nothing this project does.
And the call journal cannot report a home that refuses writes, because
`os.access` does not see a refusal there.

CI's `windows-latest` leg (`.github/workflows/ci.yml`) ran for the first time
on 2026-09-07, and that is what turned those from inferences into readings. It
found eleven failures. Four were defects in this code: both of the registry's
liveness probes, the project path written into a bridge's registry record, and
the encoding of the generated bundle manifest. One was a test asserting a
POSIX separator about a Windows filesystem path. The remaining six are checks
whose premise Windows does not have — `os.chmod` there can neither narrow a
mode nor make a path refuse access — and they skip with that reason written
out, in `tests/windows_gaps.py`.

All four are fixed, but the fourth took three more runs and two wrong
diagnoses. A port with nothing on it read as "cannot tell" there, and that was
first blamed on WSA error numbers the POSIX `errno` names did not match —
Windows' `errno` *is* the WSA table, so they matched all along. It was then
blamed on the connection not resolving at all, because the next run printed
10035, CPython's way of saying its own timeout elapsed. Both were guesses about
a duration nobody had measured. Instrumented, the runner answered: a closed
loopback port there refuses in about **two seconds** — eight samples across
the four Python versions, 2002 to 2041 ms — against 0.04 ms on macOS. The
0.25 s the probe allowed was simply short, so it is now 3 s on both systems,
and a bridge that is gone reads as absent on Windows too. What that costs is
about two seconds, once, for each dead record `td-atlas instances` prunes
there; the number and the trade are written on `config.py`'s `PROBE_BUDGET`.

One more reading came out of those runs, on Python 3.11 alone: `time.time()`
on Windows stepped in ~15.6 ms jumps until CPython 3.13, so a test that asked
whether one registry write followed another read both as the same instant. The
timestamp is right — the host is a different process and compares it against
its own wall clock — so the test stopped asking a clock for a resolution it
does not have. Its reasoning is in
`tests/test_instances.py::test_the_record_is_refreshed_by_traffic_but_not_by_every_request`.

What the leg does not close: a GitHub runner has no TouchDesigner and cannot
have one, so everything that discovers an installation or shells out to
`toeexpand` skips. A green Windows column means the Windows code paths run and
their unit tests pass — not that TouchDesigner on Windows has ever been driven
by this code.

**What this can change in your project.** Worth reading before pointing an
agent at work you care about.

- **The bridge is a component inside your open project.** Pasting the bootstrap
  line builds `/tdatlas` in the running project — a Web Server DAT, a callbacks
  DAT and a status panel. Re-running the line upgrades those nodes in place. It
  is a node in your network like any other, and it is saved with the project if
  you save the project.
- **Edits are real edits.** `td_build`, `td_set_params`, `td_set_flags`,
  `td_annotate`, `td_extension_add`, `td_palette_load` and `td_exec` change the
  live network. `td_build` wraps a batch in one `ui.undo` block, so it is a
  single **Ctrl+Z**, and a failed batch rolls itself back; `td_exec` is
  arbitrary Python and carries no such guarantee.
- **Nothing saves your project.** No tool calls `project.save()`. `td_snapshot`
  writes a *component* to `~/.td-atlas/snapshots`, deliberately: saving the
  session would be a Save As and would leave you working inside `~/.td-atlas`
  rather than your own file.
- **Writing text beside the `.toe` is off by default** and needs
  `"text_on_save": true` in `~/.td-atlas/config.json`. A file at that name that
  is not one of ours is never overwritten.
- **Reading a project never touches it.** `toeexpand` and `toecollapse` work in
  place, and `toecollapse` renames the original to `<name>.bkp1` — so every
  offline read copies the file into a cache under `~/.td-atlas/cache` first.
  Writing (`td_project_write`, `td_variant_restore`) refuses an output path
  that already exists rather than replacing it.
- **On the host** td-atlas writes only under `~/.td-atlas`: the index, the
  staged bridge, the call journal `calls.jsonl` (capped at 1 MiB), snapshots,
  variants and the expansion cache. The cache is capped by count and evicted
  least-recently-used; `td-atlas doctor` says how many expansions it holds
  and `td-atlas doctor --clear-cache` empties it.

## As an MCP server

Run `td-atlas install` and paste the `claude mcp add …` line it prints — it
points at the current interpreter by absolute path, so it keeps working
regardless of the MCP client's own working directory or whether any
virtualenv is activated. Wiring it in by hand looks like:

```bash
claude mcp add td-atlas -- /path/to/python -m td_atlas.cli mcp
```

From there the tools are the agent's and the plain language is yours. Nothing
below is a command to type at a terminal — it is what a person says to the
agent, with the calls it turns into:

| What you say to the agent | What it calls |
| --- | --- |
| *"Which operator displaces an image with noise? Give me the exact parameter names before you build anything."* | `td_search_operators`, then `td_operator_schema` |
| *"Build a noise into a blur into an out TOP in the project I have open, and check nothing is silently dead."* | `td_build` — one undo block — then `td_health` |
| *"It looks like nothing is happening."* | `td_health`, then `td_flags` on whatever it names |
| *"Show me what that looks like right now, and the motion over a second."* | `td_render`, and a contact sheet for the motion |
| *"What is inside `/project1` of `myproject.toe`? TouchDesigner is closed."* | `td_project_read` — the file is copied to a cache and read there |
| *"What did you change since we started?"* | `td_snapshot` before and after, then `td_project_diff` on the two — components in `~/.td-atlas`, never your own file |
| *"Undo that."* | `td_undo` — a whole `td_build` batch is one step |

41 tools in three groups: **9 index** tools that work offline, **23 live**
tools that act on a running instance, **9 project-file** tools that read and
write `.toe`/`.tox` from disk. Every one of them, with its arguments and what
it is for, is listed in
[`plugin/skills/touchdesigner/references/tools.md`](plugin/skills/touchdesigner/references/tools.md)
— one list, held to the code by a test, rather than a second copy here that
would drift.

`td_build` and `td_set_params` validate parameter names against the index
before sending, so the usual mistakes come back as corrections:

```
- t: is a parameter group, not a settable parameter (try: tx, ty, tz)
- typ: no such parameter (try: type, ty)
- type: 'simplex5d' is not a valid menu entry
        (try: simplex4d, simplex3d, simplex2d, sparse, perlin4d)
- period: -3 is below the clamped minimum 0.0
```

## The skill

`plugin/skills/touchdesigner/` is an agent-facing guide: how to work through the
connector, and a reference of every trap that produced no error while costing
real time — dormant branches, CPU operators hiding in a menu, feedback loops
that converge to grey, an audio codec that reports success and writes no file,
a licence that halves your resolution without saying so.

It installs as a plugin rather than by copying the directory, so that updating
it is one command instead of a second `cp` nobody remembers to run. This
repository is its own marketplace, so in Claude Code:

```
/plugin marketplace add grigabyte/td-atlas
/plugin install touchdesigner@td-atlas
```

Later, `/plugin marketplace update` pulls in whatever the skill has learned. An
agent whose client has no plugins reads the same thing from the checkout —
point it at
[`plugin/skills/touchdesigner/SKILL.md`](plugin/skills/touchdesigner/SKILL.md)
and nothing else is missing but the one-command update.

## As a bundle

`.mcpb` is an MCP Bundle: a zip holding a local MCP server plus a
`manifest.json` describing it, which a supporting client installs when you
open the file. Build one from a clean checkout:

```bash
.venv/bin/python scripts/build_mcpb.py
```

It writes `dist/td-atlas-<version>.mcpb`, regenerates `packaging/manifest.json`
from `pyproject.toml`, and writes `dist/server.json` — the submission for the
official MCP registry, carrying the SHA-256 of the bundle built beside it. The
download URL inside that submission names a GitHub release that does not exist
yet. Publishing is a separate, deliberate step — `scripts/publish.sh`, which
is what creates that release — and until it has been run the bundle is
something you build and open locally, not something to hand out. What that
step checks first, and what it does not,
is in [`docs/publishing.md`](docs/publishing.md).

Two things the bundle does not carry, and cannot: the index, which is built on
your machine from your installation and holds machine-specific values, and the
bridge, which needs `td-atlas install` and one pasted line. So a bundle install
gives you the project-file tools immediately and tells you which command
unlocks the rest.

## Troubleshooting

`td-atlas doctor` is the first move for anything that looks like a setup
problem: it walks the chain — environment, TouchDesigner, index, index build,
probe, bridge, MCP server — and prints the command that repairs each broken
link, exiting non-zero when one is broken. The table below is what the messages
mean.

| Symptom | What it is | What to run |
| --- | --- | --- |
| `doctor` says `bridge : absent` | a state, not a fault: no TouchDesigner has registered a bridge and nothing is listening on the port | open the project and paste the `td-atlas install` bootstrap line into the textport |
| a live tool refuses with *"nothing answered on the bridge port"* | TouchDesigner is not running, or is running without the bridge | `td-atlas doctor`, then the bootstrap line |
| *"The running bridge reports protocol N, below the minimum 7 this client supports"* — and, from an MCP tool, that line plus the hint *"the bridge and this host speak different protocol versions"* | the staged bridge is older (or newer) than this checkout. The oldest bridge accepted is protocol 7; an older one is refused at connect rather than allowed to fail later on the first new method | `td-atlas reload` — it is the one command that talks to a bridge the version check would otherwise reject |
| a call comes back `UnknownMethod` | same cause, seen from the other side: the bridge has no such method because it was staged from an older package | `td-atlas reload`, then repeat the call |
| *"the bridge rejected the token this host sent"* | the bridge's token and `~/.td-atlas/config.json` disagree | `td-atlas doctor` compares them; `td-atlas install` re-stages against the current one |
| *"something answered on that port but not with a bridge reply"* | another program holds the port, or the Web Server DAT is misconfigured | `td-atlas doctor`, then `td-atlas install` |
| a call times out | every request runs on TouchDesigner's main thread during a cook, so a long script blocks it | wait, then retry in smaller pieces rather than one long `td_exec` |
| *"this host has no atom index yet"* | nothing was built | `td-atlas build`, then `td-atlas probe` with TouchDesigner open |
| *"the index names a file that is not on disk"* | TouchDesigner was moved, updated or reinstalled since the index was built | `td-atlas build`, then `td-atlas probe` |
| `doctor` says `index build` disagrees | the index was built from a different TouchDesigner than the one installed now. Nothing else reports this — the tools simply answer with the other build's values | `td-atlas build` |
| a network reply ends `TRUNCATED:`, or a line reads `... N more child(ren) not listed` | the network is larger than one reply carries — the node budget, or the per-component child cap; the cut is named so it is not read as the whole network | ask again with a narrower `path` |
| a line reads `... N direct child(ren), not walked at depth D` | not a cut at all but the depth you asked for, said out loud: a leaf and a component holding thousands of operators would otherwise arrive as the same line. `N` counts direct children only, so what hangs below them is not in it | ask again with a larger `depth`, or `path` at that node |
| `td_errors` says *"the walk stopped after N operator(s) at and under `path`"*, or `td_health` says *"only N operator(s) at and under `path` were sampled"* | the walk hit its node budget: "nothing is wrong" covers the part that was walked and nothing else, and the remainder each names is a floor, not a total | run it again on a subtree, with `path` |
| `td_errors` reports nothing about a component you just built | its `path` defaults to the project. Asking it for `/` is worse, not better: the walk is breadth-first and bounded, and TouchDesigner's own `/ui` and `/sys` swallow the budget before anything of yours is reached | name the component in `path` |
| `td_health` reports script errors it *could not read* | the read failed on this build; unknown, not clean | the reply names the reason; treat that area as unchecked |
| a write refuses because the output already exists | deliberate: `toecollapse` renames what it finds to `<name>.bkp1`, so nothing is written over | choose a path that does not exist |
| `~/.td-atlas/cache` has grown | every offline read unpacks a copy there; it is capped and evicted least-recently-used, but an old cache stays until you say so | `td-atlas doctor` says how many expansions it holds, `td-atlas doctor --clear-cache` empties it |
| the MCP client cannot start the server | it is launching an interpreter that has no `td_atlas` installed | `td-atlas install` prints the exact `claude mcp add` line, absolute interpreter path and all; `doctor`'s `mcp server` link verifies it |

`td-atlas log --failures` shows what the bridge actually refused, with
timestamps, after the fact; `td_log` is the same for an agent.

## Documentation

| Document | For |
| --- | --- |
| [`plugin/skills/touchdesigner/SKILL.md`](plugin/skills/touchdesigner/SKILL.md) | Agents *using* the connector |
| [`plugin/skills/touchdesigner/references/gotchas.md`](plugin/skills/touchdesigner/references/gotchas.md) | Every trap that produced no error |
| [`plugin/skills/touchdesigner/references/tools.md`](plugin/skills/touchdesigner/references/tools.md) | All 41 MCP tools |
| [`AGENTS.md`](AGENTS.md) | Agents *contributing to* this repository |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | How to run the tests and the linter before a pull request |
| [`CHANGELOG.md`](CHANGELOG.md) | What changed per version, and every protocol change without fail |
| [`docs/architecture.md`](docs/architecture.md) | How the three layers fit together, and why |
| [`docs/cli.md`](docs/cli.md) | Every `td-atlas` subcommand and flag, and what each one needs |
| [`docs/decisions.md`](docs/decisions.md) | A dated register of the decisions that shaped the code |
| [`docs/formats.md`](docs/formats.md) | The reverse-engineered `.toe`/`.tox` format, with evidence |
| [`docs/publishing.md`](docs/publishing.md) | What is checked before a release, and by which command |

## Development

```bash
uv pip install -e . pytest
pytest                  # needs no running TouchDesigner
pytest -m live          # the rest: needs one running, with the bridge
uvx ruff check .
td-atlas reload         # re-stage the bridge and reload it through itself
```

The suite is the invariant: **no test fails for want of a running
TouchDesigner.** That is held two different ways, and the difference matters
when you read a summary. Tests that drive a live instance sit behind the `live`
marker, which `pyproject.toml` deselects — a plain run does not collect them,
and says how many it left out rather than counting them as passes. Tests that
need TouchDesigner merely *installed* skip instead, and say so, because the
same checks pass on any machine with the application present. So a plain
`pytest` is green on a machine with neither.

See [AGENTS.md](AGENTS.md) before changing anything — particularly the code
that runs inside TouchDesigner, which is Python 3.11 with no third-party
imports and a hard rule against blocking.

## Layout

```
td-atlas/
├── README.md               this file
├── AGENTS.md               contributor guide, human or agent
├── CONTRIBUTING.md         the short version: how to run the checks
├── CLAUDE.md               entry points for an agent opening this repository
├── CHANGELOG.md            Keep a Changelog; every protocol change is in it
├── LICENSE                 MIT
├── install.sh              the one-line install, POSIX sh, nothing outside
│                           the checkout and ~/.td-atlas
├── pyproject.toml
├── .gitignore
├── .github/
│   ├── workflows/ci.yml    pytest and ruff, macOS and Windows, Python 3.11-3.14
│   └── ISSUE_TEMPLATE/     build, OS and `td-atlas doctor` output
├── docs/
│   ├── architecture.md     the three layers and the reasoning
│   ├── cli.md              every subcommand and flag, and what it needs
│   ├── decisions.md        the dated register of decisions behind the code
│   ├── formats.md          the undocumented .toe format, measured
│   └── publishing.md       the release gates, each with its command
├── .claude-plugin/
│   └── marketplace.json    this repository as a marketplace
├── packaging/
│   ├── manifest.json       the MCPB manifest, generated from pyproject.toml
│   └── .mcpbignore         what stays out of the bundle
├── scripts/
│   ├── build_mcpb.py       build the bundle, the registry submission, build.json
│   └── publish.sh          the one step that sends anything outward
├── plugin/                 the plugin the marketplace above offers, and
│   │                       the only part of this tree it installs
│   ├── .claude-plugin/plugin.json
│   └── skills/
│       └── touchdesigner/  the agent skill
│           ├── SKILL.md
│           └── references/{gotchas,tools}.md
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
│   │   ├── handler.py          the RPC handler
│   │   └── ruff.toml           TouchDesigner's injected globals, declared
│   ├── project/            reading .toe/.tox without TouchDesigner
│   │   ├── expand.py           driving toeexpand/toecollapse on a copy
│   │   ├── formats.py          the undocumented file formats
│   │   ├── model.py            the operator tree and type resolution
│   │   ├── diff.py             semantic comparison
│   │   ├── render.py           tree description and code search
│   │   ├── serialize.py        the whole network as text
│   │   ├── rebuild.py          the return leg: text back into a .toe
│   │   ├── variants.py         saved states, text plus a byte copy
│   │   └── release.py          building the bridge as a .tox
│   └── mcp/
│       ├── server.py           the MCP tools over all three layers
│       └── hints.py            the recovery table every refusal is rendered from
└── tests/
    ├── conftest.py         shared fixtures
    ├── live_network.py     the fixture network the live text diff is measured on
    └── test_*.py           a plain run needs no TouchDesigner running
```

Generated and not in git: `.venv/`, the index and staged bridge under
`~/.td-atlas/`, and `dist/` — the built bundle, `dist/server.json` and
`dist/build.json`, which records the commit the bundle came from so
`publish.sh` can refuse a stale one. `.mcp.json` is written by
`td-atlas install --write-mcp-json` and holds a path specific to your machine,
so it is ignored too.

Ignored and not generated: `memory-bank/`, the owner's working notes. They are
about how this project is worked on rather than what it is, so nothing in the
tree above depends on them; what a reader needs out of them lives in `docs/`.

## Licence

MIT — see [LICENSE](LICENSE). TouchDesigner is a product of Derivative Inc.;
this project is not affiliated with them and redistributes nothing from the
installation, it only reads what is already on your machine.
