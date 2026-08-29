---
name: touchdesigner
description: Build, inspect and debug TouchDesigner projects through the td-atlas connector. Use whenever the task involves TouchDesigner, .toe or .tox files, TOPs/CHOPs/SOPs/DATs/MATs/COMPs/POPs, audio-reactive or generative visuals, projection mapping, or a running TouchDesigner instance. Covers finding the right operator, exact parameter names, building networks transactionally, seeing rendered output, and diagnosing the silent failures TouchDesigner does not report.
---

# TouchDesigner via td-atlas

TouchDesigner is a node-based visual programming environment. td-atlas gives you
an offline index of the installed build (operators, parameters, documentation,
palette) plus a live bridge into a running instance.

## The one thing that will waste your time

**TouchDesigner reports almost nothing when work silently does nothing.** A
network that never cooks, an output device switched off, a CPU-bound operator
holding the frame rate at 3, a resolution silently halved by the licence — none
of these raise an error. Three separate hours have been lost to exactly this.

Run **`td_health`** after building anything, and whenever something "looks fine
but does nothing". It is the only tool that catches these.

## Order of work

1. **Search the index first.** It is offline and free — no round trip to
   TouchDesigner. Never guess a parameter name.
2. **Read the schema** of any operator before creating it.
3. **Build with `td_build`**, not a series of single calls.
4. **Look at the result** with `td_render`, and at motion with a contact sheet.
5. **Run `td_health`.**

## Finding the right operator

`td_search_operators("displace an image with noise")` — plain language works.

Two things it cannot do, and what to do instead:

- **Artist slang is not TouchDesigner vocabulary.** There is no "strobe" or
  "melt" operator; those are techniques built from several nodes. Search for the
  mechanism instead ("brightness over time", "displace by a texture"), or use
  `td_docs` which covers 720 concept and technique articles.
- **Check the palette before building.** `td_palette("projection mapping")`
  searches 277 finished components that ship with TouchDesigner — mappers,
  corner-pinners, colour pickers, audio analysers. Load one with
  `op('/project1').loadTox(path)` rather than rebuilding it.

`td_glossary("cook")` defines the vocabulary the documentation assumes.

## Parameter names

`td_operator_schema("noiseTOP")` gives exact names, defaults, menu options and
ranges. Two traps:

- **The documentation describes parameter *groups*; you must set the members.**
  The docs say `t` (Translate); the settable parameters are `tx`, `ty`, `tz`.
  The schema returns the members. Setting `t` fails.
- **Menu labels carry information the name does not.** The Noise TOP's `type`
  menu labels read "Simplex 3D (GPU)" versus plain "Sparse" — the latter runs on
  the CPU and costs ~400 ms per cook at 720p, collapsing the frame rate to 3.
  Read the labels, not just the values.

`td_build` and `td_set_params` validate names against the index before sending,
so a mistake comes back as `t: is a parameter group (try: tx, ty, tz)` rather
than as a traceback.

## Building

Use `td_build` for anything multi-step:

```json
[
  {"method": "op_create", "params": {
     "parent": "/project1", "type": "noiseTOP", "name": "n1",
     "position": [-600, 0],
     "pars": {"type": "simplex3d", "period": 3.5,
              "tx": {"expr": "absTime.seconds * 0.1"}}}},
  {"method": "op_create", "params": {
     "parent": "/project1", "type": "blurTOP", "name": "b1",
     "pars": {"size": 6},
     "connect": [{"from": "/project1/n1", "index": 0}]}}
]
```

The whole batch lands in one `ui.undo` block: if any step fails everything rolls
back, and a successful batch is a single Ctrl+Z for the person using
TouchDesigner. Parameter values may be a constant, `{"expr": "..."}` for an
expression, `{"bind": "..."}`, or `{"pulse": true}`.

Methods: `op_create`, `op_delete`, `op_connect`, `op_disconnect`, `par_set`.

## Seeing what you made

`td_render("/project1/b1")` returns the image. **Use it** — inferring appearance
from parameter values does not work.

For anything time-based — a strobe, a feedback trail, an animation — a single
frame is a coin toss. Use a contact sheet:

```python
from td_atlas.bridge.client import BridgeClient
from td_atlas.bridge.filmstrip import contact_sheet
contact_sheet(BridgeClient.discover(), "/project1/b1", "sheet.png",
              frames=9, interval=0.13, columns=3)
```

## Reading projects from disk

No TouchDesigner needed: `td_project_read`, `td_project_grep`,
`td_project_diff`. `td_project_grep` reaches the Python and GLSL inside DATs,
which no file search can — that code lives inside the .toe container.

For an audit trail: `td_snapshot("before")` → work → `td_snapshot("after")` →
`td_project_diff`.

## Things that will bite you

Detail in `references/gotchas.md`. The short list:

- **A branch nothing displays or records never cooks.** Terminal nodes (Movie
  File Out, and any chain not feeding a viewer) are not pulled. Add a `cacheTOP`
  with `alwayscook` on to keep a chain live.
- **TouchDesigner throttles rendering when its window is in the background.**
- **Colour operations inside a feedback loop accumulate.** A hue rotation in the
  loop drives everything to grey; an additive composite drives it to white.
  Feed back from *before* the grade, and prefer `maximum` over `add`.
- **Non-Commercial caps resolution at 1280×1280**, silently, with only a
  warning.
- `exec` blocks TouchDesigner's main thread. Do not poll during a recording.

## Command line

```bash
td-atlas status                     # install, index and bridge state
td-atlas search "blur an image"
td-atlas op noiseTOP --page Noise
td-atlas render /project1/n1 -o out.png
td-atlas project diff a.toe b.toe
td-atlas project variant save a.toe --label try1   # branch, restore, compare
td-atlas reload                     # upgrade the bridge in place
```

## If the bridge is unreachable

`td-atlas install` prints a one-line bootstrap to paste into TouchDesigner's
textport (Dialogs → Textport and DATs). Re-running it upgrades in place.
