# Tool reference

41 MCP tools in three groups. This list is held to the code by
`tests/test_skill_reference.py`: every name and parameter list here is compared
against the server, and so is that count.

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
| `td_status()` | Is the bridge up, what project is open. |
| `td_log(limit, failures, summary, method)` | Your own trail: every bridge call this host made, how long it took and what it refused with. `td_status` reports no call history at all; this survives the session and answers "what did I break yesterday". `failures=True` for the refusals alone with the repair for the latest; `summary=True` for which methods refuse and which are slow. Offline tools never dial the bridge and leave no trace here. |
| `td_instances()` | Every running TouchDesigner that registered a bridge, and which one these tools reach. Check it before believing an edit landed in the project you meant. |
| `td_doctor()` | Every link — install, index, probe pass, bridge, this server — with the command that fixes each. Catches the index built from another TouchDesigner build, which raises nothing. The read cache is a CLI-only concern: `td-atlas doctor` on a terminal also says how many unpacked projects it holds and `td-atlas doctor --clear-cache` empties it, neither of which this tool reports or does — deleting a user's cache is not something a tool call should do on its own. |
| `td_health(path, interval)` | **The silent-failure detector.** Run after building anything. `interval` is clamped, so a long wait comes back sooner than asked and says so. Read the reply for the two ways it can be partial — a truncated walk and script errors it could not read; see *When a reply is cut short* below. |
| `td_network(path, depth)` | What is inside a component and how it is wired. A network too large for one reply comes back cut, and every cut is named rather than silent; see *When a reply is cut short* below. |
| `td_op_info(path)` | One operator: type, wiring, live parameter values, errors. |
| `td_build(operations, undo_name, owner)` | Multi-step edits as one atomic, undoable block. Validated first. A created node with no `position` is given a free spot, and one wired inside its own `op_create` lands to the right of its source, so a batch reads left to right. `op_create` takes a `text` key for a DAT's contents, so shader and script source needs no separate `td_exec`. |
| `td_set_params(path, pars, op_type, owner)` | Set parameters on an existing operator. Names are checked against the index **only if you pass `op_type`**; without it the name goes straight to TouchDesigner and comes back as an `AttributeError`. |
| `td_palette_load(name, parent, rename, position, category, owner)` | Install a palette component by the name `td_palette` reports. Checks the .tox is on disk first, and reports the name TouchDesigner actually gave the node. |
| `td_extension_add(class_name, code, path, parent, name, extension_name, promote, index, position, owner)` | Attach a Python class to a COMP as an extension — DAT, three Extensions parameters and the re-init in one call. Parses the code here first, and reads the result back: a class that fails to instantiate leaves the COMP reporting nothing at all. |
| `td_annotate(text, parent, title, name, path, size, color, position, font_size, mode, owner)` | Leave a note in the network saying what you built and why — a coloured box beside the nodes it describes. Pass `path` to rewrite a note instead of adding another. |
| `td_annotations(path, depth)` | Read the notes in a network, including a brief a person left for you, with the nodes each note's box sits over. Invisible to every other tool here. |
| `td_flags(path)` | The flags that decide whether a node runs and what is visible: display, render, bypass, cooking, and which ones this operator does not have. Check it when a correct-looking network produces nothing. |
| `td_set_flags(path, flags, owner)` | Turn those flags on or off. Every write is read back, so a flag the family will not take is a refusal rather than a silence. |
| `td_render(path, width, height)` | A TOP's image, returned to you. |
| `td_errors(path)` | Operators reporting an error or warning, at and under `path` — the project by default. Do not ask for `/`: the walk is breadth-first and bounded, and TouchDesigner's own `/ui` and `/sys` are thousands of operators wide near the top, so the budget runs out before anything of yours is reached (measured: 5000 nodes from `/` covered 3,979 of `/ui` and 954 of `/sys`, and missed a warning planted inside the project). The reply names the subtree it walked and says when the walk stopped early; see *When a reply is cut short* below. |
| `td_exec(code)` | Arbitrary Python inside TouchDesigner. Last resort. |
| `td_undo(redo)` | Undo or redo, including whole `td_build` batches. Cannot be a step inside `td_build` — that is refused, because two of them in a row reach past the batch into the artist's own history. |
| `td_snapshot(label, path)` | Save a component for later diffing. |
| `td_claim_scope(path, owner, ttl_seconds)` | Announce a subtree as yours before a run of edits, when another agent or session may be in the same project. Covers everything below `path`; lapses on its own. It guards against *every* unnamed caller including you, so carry the same `owner` into each write that follows. |
| `td_release_scope(path, owner)` | Hand a claimed subtree back as soon as you are done, instead of leaving the next agent to wait out the claim. |
| `td_scopes()` | Which subtrees are claimed, by whom, until when. Check before editing a project someone else may be in. |

## Project files — on disk

| Tool | Use it for |
| --- | --- |
| `td_project_read(file, path, depth, params)` | Operator tree of a .toe/.tox without TouchDesigner. |
| `td_project_text(file, path, max_bytes)` | The whole network as JSON — every parameter, wire, flag and DAT line. Reach for it when the tree is not enough; narrow with `path`, since a big network is refused rather than cut. |
| `td_project_write(file, text, output)` | The return leg of `td_project_text`: write an edited dump into a new `.toe`/`.tox`, no instance running. `file` must still be the original — the dump covers five of the forty-odd kinds of file a `.toe` holds and the rest are copied from it — and `output` must not exist. Read the gaps in the reply: anything that could not be written is listed, not approximated. |
| `td_project_grep(file, pattern, limit)` | Search the Python and GLSL inside DATs. |
| `td_project_diff(before, after, show_moves, include_text)` | Semantic comparison of two files. |
| `td_variant_save(file, label, note, path)` | Keep the current state of a `.toe`/`.tox` before trying a direction. A variant is the network text plus a byte copy of the file, under `~/.td-atlas/variants` — the text alone cannot rebuild a `.toe`, and the copy costs a median 19% on top of the text. A label already in use is refused, never overwritten. |
| `td_variant_list(file)` | What has been saved — of one project, or of every project that has any. Says whether the original has changed since each save; that is information, not a warning, because a restore reads the variant's own copy. |
| `td_variant_restore(file, label, output)` | Write a saved state back out. A copy, not a repack: `toecollapse` never runs, so nothing of the user's is renamed to `.bkp`. `output` must not exist; a directory keeps the saved file name. |
| `td_variant_diff(file, before, after, show_moves, include_text)` | Compare two saved states with the same semantic diff `td_project_diff` runs. |

## When a reply is cut short

Everything below runs on TouchDesigner's main thread during a cook, so the work
one call may do is bounded. When a bound is reached the reply says so instead of
looking complete — a partial answer read as a whole one is the expensive
mistake here.

`td_network` marks three separate cuts. `childrenHidden` is printed under the
component whose listing was shortened, and gives the number left out — printed
even where nothing was listed at all, since the invisible cut is the dangerous
one. Over the whole reply, `truncated` with `hidden` says how many operators
were dropped against the overall budget, `limit` and `maxChildren` say which
two bounds applied, and `depthLimited` carries the depth you asked for when it
was reduced to the deepest this tool walks. The repair is always the same: ask
again with a narrower `path`.

`td_errors` and `td_health` bound their walk by node count, and both take the
subtree to walk as `path`. `scanned` is how many operators were actually looked
at, `notScanned` how many were not, and `truncated` marks that it happened;
`limit` is the bound. `notScanned` is a floor, not a total: it counts operators
the walk had already found and not visited, and never what hangs below them.
Read "nothing is wrong" as covering `scanned` operators under the subtree the
reply names, and no others.

`td_health` also reports `scriptErrorsUnread` — the places where reading
TouchDesigner's own script errors raised, with the reason. That is *unknown*,
not clean, and it is a finding in the report rather than a silence.

Frame capture, used by the contact sheet below, has a byte budget as well as a
frame count. A capture call answers `captured: true` while it is collecting and
`full: true` once the buffer is at its limit, with `limit` and `maxFrames`
naming the bounds; the sheet stops sampling on that flag rather than growing
storage inside the component without end.

## When a tool refuses

Any refusal — a dead bridge, a path that does not exist, a parameter name the
index rejects, an index that was never built — comes back with the state it
observed and then three lines:

```
cause: no operator exists at that path in the running project
fix: list what is actually there before retrying — paths are case-sensitive ...
continue with: td_network, td_op_info
```

`continue with:` names tools and `td-atlas` subcommands that exist; act on it
rather than repeating the call. Where the connector has no mapped recovery —
an exception type nobody has classified — it says `fix: none known` and names
nothing, which means the message above it is the whole answer: change
something before retrying.

## Not exposed over MCP

A contact sheet needs repeated sampling over wall-clock time, which a single
tool call cannot do. Use the Python helper:

```python
from td_atlas.bridge.client import BridgeClient
from td_atlas.bridge.filmstrip import contact_sheet
contact_sheet(BridgeClient.discover(), "/project1/out", "sheet.png",
              frames=12, interval=0.11, columns=4, width=280)
```

## Setup

```bash
td-atlas build     # offline index, 23-30 s
td-atlas install   # stage the bridge, print the bootstrap line
# paste that line into TouchDesigner's textport, once per project
td-atlas probe     # runtime facts: defaults, ranges, menus, type aliases
```
