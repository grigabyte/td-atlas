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
network that never cooks, a flag left off, an output device switched off, a
CPU-bound operator holding the frame rate down, a resolution silently halved by
the licence — none of these raise an error. Three separate hours have been lost
to exactly this.

Run **`td_health`** after building anything, and whenever something "looks fine
but does nothing". It is the tool that catches these. When it says nothing is
cooking, **`td_flags`** says which of display, render, bypass and cooking is the
reason; `td_set_flags` reads every write back, so a flag a family will not take
comes back as a refusal rather than a silence.

## Order of work

1. **Search the index first.** It is offline and free — no round trip to
   TouchDesigner. Never guess a parameter name.
2. **Read the schema** of any operator before creating it.
3. **Build with `td_build`**, not a series of single calls.
4. **Look at the result** with `td_render`, and at motion with a contact sheet.
5. **Run `td_health`.**

When a call refuses and you do not know why, or the artist says something
broke while you were working: **`td_log(failures=True)`** is your own trail —
every bridge call this host made, with the text each refusal came back with.
`td_log(summary=True)` says which method has been failing repeatedly, which is
the signal that the approach is wrong rather than the call. It outlives the
TouchDesigner session, so it also answers "what happened yesterday".

## Finding the right operator

`td_search_operators("displace an image with noise")` — plain language works.

Two things it cannot do, and what to do instead:

- **Artist slang is not TouchDesigner vocabulary.** There is no "strobe" or
  "melt" operator; those are techniques built from several nodes. Search for the
  mechanism instead ("brightness over time", "displace by a texture"), or use
  `td_docs`, which reaches 2,060 wiki pages — concepts and techniques, not just
  operator help.
- **Check the palette before building.** `td_palette("projection mapping")`
  searches 277 finished components that ship with TouchDesigner — mappers,
  corner-pinners, colour pickers, audio analysers. Install one with
  `td_palette_load(name, parent)` rather than rebuilding it; it checks the .tox
  is on disk first and reports the name TouchDesigner actually gave the node.

`td_glossary("cook")` defines the vocabulary the documentation assumes.

## Parameter names

`td_operator_schema("noiseTOP")` gives exact names, defaults, menu options and
ranges. Two traps:

- **The documentation describes parameter *groups*; you must set the members.**
  The docs say `t` (Translate); the settable parameters are `tx`, `ty`, `tz`.
  The schema returns the members. Setting `t` fails.
- **Menu labels carry information the value does not.** The schema prints them
  as `value — Label`: the Noise TOP's `type` menu reads
  `simplex3d — Simplex 3D (GPU)` against a plain `sparse — Sparse`, and the
  second one runs on the CPU at ~96 ms per cook at 1280×720. Read the labels,
  not just the values.

`td_build` validates parameter names against the index before sending anything,
resolving each target's type itself, so a mistake comes back as
`t: is a parameter group, not a settable parameter (try: tx, ty, tz)` rather
than as a traceback. `td_set_params` does the same **only when you pass
`op_type`**; without it the name goes straight to TouchDesigner.

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
`undo` is not one of them — it walks the history the batch is being recorded
into. Call `td_undo` on its own, after.

If another agent or session may be in the same project, claim your subtree with
`td_claim_scope(path, owner)` first — and then **pass that same `owner` into
every `td_build`, `td_set_params`, `td_set_flags` and `td_annotate`**, or your
own claim refuses your own writes. `td_release_scope` hands it back;
`td_scopes` says what is already held.

`td_annotate` leaves the reason for what you built beside the nodes, as a box
the artist can read; `td_annotations` reads notes back — including a brief a
person left for you, which no other tool here shows.

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

No TouchDesigner needed: `td_project_read` for the tree, `td_project_text` for
the whole network as JSON — every parameter, wire, flag and DAT line — and
`td_project_grep`, which reaches the Python and GLSL inside DATs that no file
search can, because that code lives inside the .toe container. A network too
big for one result is refused, not truncated; narrow it with `path` or write it
out with `td-atlas project text FILE -o network.json`.

`td_project_write` is the return leg: an edited dump into a **new** file. It is
a patcher, so it needs the original `file` too, and it lists what it could not
write — read those gaps rather than assuming.

Before trying a direction, `td_variant_save(file, label)` keeps the current
state; `td_variant_list`/`_restore`/`_diff` branch, restore and compare. On a
live instance instead: `td_snapshot("before")` → work → `td_snapshot("after")`
→ `td_project_diff`.

**Never save the artist's project.** `project.save()` is Save As in disguise and
moves the file they have open. Nothing here needs it; TouchDesigner writes the
network text beside the `.toe` when *they* save.

## Things that will bite you

Detail in `references/gotchas.md`. The short list:

- **A branch nothing displays or records never cooks.** Terminal nodes (Movie
  File Out, and any chain not feeding a viewer) are not pulled. Add a `cacheTOP`
  with `alwayscook` on to keep a chain live.
- **A Python class attached the way the wiki shows it fails in total silence.**
  Use `td_extension_add`, which uses the form that works and reads it back.
- **TouchDesigner throttles rendering when its window is in the background.**
- **Colour operations inside a feedback loop accumulate.** A hue rotation in the
  loop drives everything to grey; an additive composite drives it to white.
  Feed back from *before* the grade, and prefer `maximum` over `add`.
- **Non-Commercial caps resolution at 1280×1280**, silently, with only a
  warning: asking for 1920×1080 gives 1280×720.
- `exec` blocks TouchDesigner's main thread. Do not poll during a recording.

## Command line

```bash
td-atlas status                     # install, index and bridge state
td-atlas doctor                     # every link, with the command that fixes it
td-atlas search "blur an image"
td-atlas op noiseTOP --page Noise
td-atlas render /project1/n1 -o out.png
td-atlas project diff a.toe b.toe
td-atlas project variant save a.toe --label try1   # branch, restore, compare
td-atlas log --failures             # the same trail as td_log
td-atlas reload                     # upgrade the bridge in place
```

## If the bridge is unreachable

`td-atlas install` prints a one-line bootstrap to paste into TouchDesigner's
textport (Alt+T). Re-running it upgrades in place. `td_instances` says which
running TouchDesigner these tools actually reach — check it before believing an
edit landed in the project you meant.
