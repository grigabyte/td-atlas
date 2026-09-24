# Tool reference

Every `td_` name here is an MCP tool the agent calls on the artist's behalf.
A person at a terminal has a separate surface, `td-atlas`, with its own page.

46 MCP tools in three groups. `tests/test_skill_reference.py` compares every
name, every parameter list and that count against the server.

## Index — offline, no running TouchDesigner

| Tool | Use it for |
| --- | --- |
| `td_search_operators(query, family, limit)` | Find an operator by what it does. Plain language. `family` narrows to TOP/CHOP/SOP/DAT/MAT/COMP/POP. |
| `td_operator_schema(op_type, page, include_hidden)` | Exact parameter names, defaults, menu options, ranges, connector counts, path to a shipped example. Menu options print as `value — Label` where the label says more than the value, which is where costs like "(GPU)" live. |
| `td_search_parameters(query, limit)` | Which operator has a parameter doing X. How `alwayscook` gets found. |
| `td_python_api(name, query)` | Members and methods of a class, with inherited ones resolved. |
| `td_docs(query, page, limit)` | 2,060 wiki pages — concepts, techniques, tutorials, not just operators. |
| `td_glossary(term, limit)` | 185 glossary entries: Cook, Time Slice, Clone, Perform Mode. |
| `td_palette(query, category, limit)` | 277 ready-made components shipped with TouchDesigner. |
| `td_expression_help(query, limit)` | Expression and command syntax. |
| `td_example(op_type, depth)` | A real working network for an operator, from the shipped snippet library. |

## Live — acting on a running instance

| Tool | Use it for |
| --- | --- |
| `td_status()` | Is the bridge up, what project is open, and is the timeline playing: `timeline: frame 851 (playing) \| start 1 end 994 \| range 1–994 \| rate 60 \| abs 1284807`. Check it before trusting a live CHOP between two of your calls. `abs` is `absTime.frame`, an application count that does not follow the timeline's frame and stands still while the root timeline is paused; waits count `tick` (`op.TDResources.time.frame`) instead. `(!)` on the range means playback stops short of `end`. |
| `td_log(limit, failures, summary, method)` | Your own trail: every bridge call this host made, how long it took and what it refused with. A call that changed the project shows what it changed — `tx = 0.5 (was 0.2)`, `ty = expr ...`, each td_build step, parameters and flags with their old value, the head of a td_exec's code — so "put it back the way it was" is `method="par_set"` (or `"batch"`, `"exec"`) and reading, not reconstructing. `td_status` reports no call history at all; this survives the session and answers "what did I break yesterday". `failures=True` for the refusals alone with the repair for the latest; `summary=True` for which methods refuse and which are slow. Offline tools never dial the bridge and leave no trace here. |
| `td_instances()` | Every running TouchDesigner that registered a bridge, and which one these tools reach. Check it before believing an edit landed in the project you meant. |
| `td_doctor()` | Every link — install, index, probe pass, bridge, this server — with the command that fixes each. Catches the index built from another TouchDesigner build, which raises nothing. The read cache is a CLI-only concern: `td-atlas doctor` on a terminal also says how many unpacked projects it holds and `td-atlas doctor --clear-cache` empties it, neither of which this tool reports or does — deleting a user's cache is not something a tool call should do on its own. |
| `td_health(path, interval)` | **The silent-failure detector.** Run after building anything. Besides dead branches, disabled outputs, shader and callback errors and slow cooks, it flags a cook time left from long before the check (named by the frame it was measured on, so it is not blamed for the frame rate); feedback loops that `cook(force=True)` does not advance; bypassed gain operators (Level, Math, HSV Adjust), which pass their input through instead of switching it off; Level TOPs that can output negative floats into an Add; and reads of `absTime` or an unseeded random generator, which keep a render from reproducing. `interval` is clamped, so a long wait comes back sooner than asked and says so. Read the reply for the two ways it can be partial — a truncated walk and script errors it could not read; see *When a reply is cut short* below. |
| `td_network(path, depth)` | What is inside a component and how it is wired. A network too large for one reply comes back cut, and every cut is named; see *When a reply is cut short* below. A TOP whose viewer flag is off is marked `[viewer off]` — its tile is empty on purpose, not broken. |
| `td_op_info(path)` | One operator: type, wiring, live parameter values, errors. |
| `td_build(operations, undo_name, owner)` | Multi-step edits as one atomic, undoable block. Validated first. A created node with no `position` is given a free spot, and one wired inside its own `op_create` lands to the right of its source, so a batch reads left to right. `op_create` takes a `text` key for a DAT's contents, so shader and script source needs no separate `td_exec`. A step key the bridge would not read (`input_index` for `index`) is refused with the right one, not ignored. |
| `td_set_params(path, pars, op_type, owner)` | Set parameters on an existing operator. Names and `../` OP references are checked against the index first; without `op_type` the operator's type is asked of the bridge in the same call that resolves the references. With no index built, or a node the bridge cannot type, the write goes out unchecked. |
| `td_palette_load(name, parent, rename, position, category, owner)` | Install a palette component by the name `td_palette` reports. Checks the .tox is on disk first, and reports the name TouchDesigner actually gave the node. |
| `td_extension_add(class_name, code, path, parent, name, extension_name, promote, index, position, owner)` | Attach a Python class to a COMP as an extension — DAT, three Extensions parameters and the re-init in one call. Parses the code here first, and reads the result back: a class that fails to instantiate leaves the COMP reporting nothing at all. |
| `td_annotate(text, parent, title, name, path, size, color, position, font_size, mode, owner)` | Leave a note in the network saying what you built and why — a coloured box beside the nodes it describes. Pass `path` to rewrite a note instead of adding another. |
| `td_annotations(path, depth)` | Read the notes in a network, including a brief a person left for you, with the nodes each note's box sits over. Invisible to every other tool here. |
| `td_flags(path)` | The flags that decide whether a node runs and what is visible: `display`, `render`, `bypass`, `lock`, `expose`, `viewer`, `activeViewer`, `cloneImmune`, `allowCooking`, `selected`, `pickable`, and which of them this operator does not have. Cooking is `allowCooking`; there is no flag named `cooking`. Check it when a correct-looking network produces nothing. |
| `td_set_flags(path, flags, owner)` | Turn those flags on or off. Every write is read back, so a flag the family will not take comes back as a refusal. |
| `td_render(path, width, height, save_to, settle_frames, overwrite)` | A TOP's image, returned to you. `save_to` writes the PNG to a file and answers in text instead, and refuses a file already there unless `overwrite=True`; `settle_frames` waits that many TouchDesigner frames first, since a render right after an edit can show the frame from before it. |
| `td_timeline_run(path, frames, output, save, tiles, from_start, settle, render, hold)` | Walk the timeline frame by frame and save a TOP's frames, as a job that returns its id at once — for anything with history (live audio, Feedback TOPs, trails), where a `td_exec` loop hits the 30 s limit. `frames="1..994"` is the walk, `save="92..217,459..541"` the frames written (default: all), `output="/renders/f{frame:04d}.png"` the template. It pauses the timeline and gives the play mode back, walks every frame consecutively (from the start of `frames` when `from_start`, else from the first frame to save), and waits `settle` application frames per step; 2 was measured repeatable for live audio. Without `output` it only advances — warm history up to frame N, then the timeline is held paused at N for `td_render`. `tiles=2` renders 2x2 quarters past the 1280 cap by cropping the Render TOP(s) in `render`: put `{tile}` in `output` (0 top left, 1 top right, 2 bottom left, 3 bottom right); each quarter is a whole walk of its own, since a Feedback TOP builds a quarter's history only under its crop. Nothing resets a Feedback TOP between passes, so a quarter's first frames carry the previous quarter's tail: a decaying trail forgets it within the walk, an accumulator does not — reset it yourself or start the walk early enough. The crop goes back to 0..1 however the job ends. |
| `td_timeline_status(job)` | Progress of that job: frame, pass, saved N of M (or a profile's table so far), errors, and `stalled` when no step has run for seconds. Without `job`, the latest. |
| `td_timeline_cancel(job)` | Stop the walk now; the crop and play mode go back, written files stay. |
| `td_timeline_profile(path, frames, limit, settle)` | Who spends the frame: walks `frames` (e.g. `"3000..3009"`) as a job in the same slot as `td_timeline_run`, and on every frame forces each TOP/CHOP/SOP/POP under `path` (up to `limit`) to cook, upstream first, timed with the GPU waited for — a `td_exec` loop of `cook(force=True)` reads ~0 ms for a TOP that costs 80, because the cook only queues GPU work. `td_timeline_status` shows mean and max ms per frame, costliest first; a row marked "did not cook on its own" is not part of the frame (the stale-`cookTime` trap), and one cooked inside an earlier measurement shares its cost with that operator. The play mode goes back at the end. |
| `td_errors(path)` | Operators reporting an error or warning, at and under `path` — the project by default. Do not ask for `/`: the walk is breadth-first and bounded, and TouchDesigner's own `/ui` and `/sys` are thousands of operators wide near the top, so the budget runs out before anything of yours is reached (measured: 5000 nodes from `/` covered 3,979 of `/ui` and 954 of `/sys`, and missed a warning planted inside the project). The reply names the subtree it walked and says when the walk stopped early; see *When a reply is cut short* below. |
| `td_trace(path, depth)` | **Why is this TOP black?** Walks up every wired input from `path` (`depth` hops, at most 40 nodes), reads each image's min / mean / max over R, G, B and the alpha mean, and marks where the signal was lost: `dropped here` (the image goes black while an input still carried something, or an Add is fed a negative input and subtracts it), `negative values start here` (a float Level with no clamp and with contrast above 1, inlow above 0 or outlow below 0), `alpha goes to 0 here` (colour left under an alpha of 0) or `NaN/Inf start here`, with the reason when the node's settings show it — bypass, opacity 0, a black level (which cuts to 0, not below), an empty input, an error. `?` on a mark means an input could not be read. It forces no cook, but reading an image that is out of date makes TouchDesigner cook that node once — on a Feedback TOP, one step of the loop. A node that never cooked is not read; it, a non-TOP, or one past the time or download budget says so instead of reporting zeros. Wires only — an image a Select or Render TOP reaches through a parameter is not followed. |
| `td_exec(code)` | Arbitrary Python inside TouchDesigner. Last resort. An edit `td_build` cannot express still belongs in one undo step: wrap it in `ui.undo.startBlock('name')` … `ui.undo.endBlock()`, which works from here, and the artist's Ctrl+Z takes it back whole. |
| `td_undo(redo)` | Undo or redo, including whole `td_build` batches. Cannot be a step inside `td_build` — that is refused, because two of them in a row reach past the batch into the artist's own history. |
| `td_snapshot(label, path)` | Keep every parameter of a branch before changing it: `path` is the branch's COMP. Snapshot again after the edits under another label (a reused label replaces the file), and `td_project_diff` of the two files lists each changed parameter, expression and custom parameter included. The reply names both next calls with the file path. |
| `td_claim_scope(path, owner, ttl_seconds)` | Announce a subtree as yours before a run of edits, when another agent or session may be in the same project. Covers everything below `path`; lapses on its own. It guards against *every* unnamed caller including you, so carry the same `owner` into each write that follows. |
| `td_release_scope(path, owner)` | Hand a claimed subtree back as soon as you are done. Until you do, the next agent waits out the claim. |
| `td_scopes()` | Which subtrees are claimed, by whom, until when. Check before editing a project someone else may be in. |

## Project files — on disk

| Tool | Use it for |
| --- | --- |
| `td_project_read(file, path, depth, params)` | Operator tree of a .toe/.tox without TouchDesigner. |
| `td_project_text(file, path, max_bytes)` | The whole network as JSON — every parameter, wire, flag and DAT line. Reach for it when the tree is not enough; narrow with `path`, since a big network is refused. |
| `td_project_write(file, text, output)` | The return leg of `td_project_text`: write an edited dump into a new `.toe`/`.tox`, no instance running. `file` must still be the original — the dump covers five of the forty-odd kinds of file a `.toe` holds and the rest are copied from it — and `output` must not exist. Read the gaps in the reply: anything that could not be written is listed, not approximated. |
| `td_project_grep(file, pattern, limit)` | Search the Python and GLSL inside DATs. |
| `td_project_diff(before, after, show_moves, include_text)` | Semantic comparison of two files, two `td_snapshot` files of one branch included. A parameter line shows its constant or its expression text with no mark saying which, so take values to put back from `td_project_text` of the before file, which keeps `{"expr": ...}`. |
| `td_variant_save(file, label, note, path)` | Keep the current state of a `.toe`/`.tox` before trying a direction. A variant is the network text plus a byte copy of the file, under `~/.td-atlas/variants` — the text alone cannot rebuild a `.toe`, and the copy costs a median 19% on top of the text. A label already in use is refused, never overwritten. |
| `td_variant_list(file)` | What has been saved — of one project, or of every project that has any. Says whether the original has changed since each save; that is information, not a warning, because a restore reads the variant's own copy. |
| `td_variant_restore(file, label, output)` | Write a saved state back out. A copy, not a repack: `toecollapse` never runs, so nothing of the user's is moved aside to a
`.bkp1` file. `output` must not exist; a directory keeps the saved file name. |
| `td_variant_diff(file, before, after, show_moves, include_text)` | Compare two saved states with the same semantic diff `td_project_diff` runs. |

## When a reply is cut short

Every call below is bounded in the work it may do. It runs on TouchDesigner's
main thread during a cook, and a reply that reached a bound names the bound it
reached.

`td_network` marks four separate cuts. `childrenHidden` is printed under the
component whose listing was shortened, gives the number left out, and is printed
even where nothing was listed at all. Over the whole reply, `truncated` with
`hidden` says how many operators were dropped against the overall budget.
`limit` and `maxChildren` say which two bounds applied. `depthLimited` carries
the depth you asked for, when it was reduced to the deepest this tool walks.
A component sitting at the depth you asked for is listed with the number of
*direct* children it has that were not walked. That count is a floor, since what
hangs below those children was never looked at. The repair is the same for all
four cuts, and it is to ask again with a narrower `path` or a larger `depth`.

`td_errors` and `td_health` bound their walk by node count, and both take the
subtree to walk as `path`. `scanned` is how many operators were actually looked
at, the named subtree's own root included, so it never exceeds `limit`.
`notScanned` is how many were not, `truncated` marks that it happened, and
`limit` is the bound. `notScanned` is a floor, counting operators the walk had
already found and not visited, and never what hangs below them. Read "nothing is
wrong" as covering `scanned` operators at and under the subtree the reply names,
and no others.

`td_health` also reports `scriptErrorsUnread`. It names the places where reading
TouchDesigner's own script errors raised, and gives the reason. Read those places
as *unknown*, and note that they arrive as a finding in the report.

Frame capture, used by the contact sheet below, has a byte budget as well as a
frame count. A capture call answers `captured: true` while it is collecting. It
answers `full: true` once the buffer is at its limit, and `limit` and
`maxFrames` name the bounds. The sheet stops sampling on that flag.

## When a tool refuses

Every refusal comes back with the state it observed and then three lines. A dead
bridge, a path that does not exist, a parameter name the index rejects and an
index that was never built all arrive in this shape:

```
cause: no operator exists at that path in the running project
fix: list what is actually there before retrying — paths are case-sensitive ...
continue with: td_network, td_op_info
```

`continue with:` names tools and `td-atlas` subcommands that exist, and acting on
that line beats repeating the call. Where the connector has no mapped recovery
for the exception, it says `fix: none known` and names nothing. The message above
it is then the whole answer, and something has to change before the retry.

## Not exposed over MCP

A contact sheet needs repeated sampling over wall-clock time, which one tool call
cannot do. It shows what the composition does while it plays; frames that must
be the same on every run come from `td_timeline_run`, which walks timeline
frames. Use the Python helper:

```python
from td_atlas.bridge.client import BridgeClient
from td_atlas.bridge.filmstrip import contact_sheet
contact_sheet(BridgeClient.discover(), "/project1/out", "sheet.png",
              frames=12, interval=0.11, columns=4, width=280)
```

## Setup

```bash
td-atlas build     # offline index, 13-16 s
td-atlas install   # stage the bridge, print the bootstrap line
# paste that line into TouchDesigner's textport, once per project
td-atlas probe     # runtime facts: defaults, ranges, menus, type aliases
```
