# Tool reference

Thirty-six MCP tools in three groups.

## Index — offline, no running TouchDesigner

| Tool | Use it for |
| --- | --- |
| `td_search_operators(query, family, limit)` | Find an operator by what it does. Plain language. `family` narrows to TOP/CHOP/SOP/DAT/MAT/COMP/POP. |
| `td_operator_schema(op_type, page, include_hidden)` | Exact parameter names, defaults, menu options, ranges, connector counts, path to a shipped example. |
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
| `td_instances()` | Every running TouchDesigner that registered a bridge, and which one these tools reach. Check it before believing an edit landed in the project you meant. |
| `td_doctor()` | Every link — install, index, probe pass, bridge, this server — with the command that fixes each. Catches the index built from another TouchDesigner build, which raises nothing. |
| `td_health(path, interval)` | **The silent-failure detector.** Run after building anything. |
| `td_network(path, depth)` | What is inside a component and how it is wired. |
| `td_op_info(path)` | One operator: type, wiring, live parameter values, errors. |
| `td_build(operations, undo_name, owner)` | Multi-step edits as one atomic, undoable block. Validated first. A created node with no `position` is given a free spot, and one wired inside its own `op_create` lands to the right of its source, so a batch reads left to right. |
| `td_set_params(path, pars, op_type, owner)` | Set parameters on an existing operator. |
| `td_palette_load(name, parent, rename, position, category, owner)` | Install a palette component by the name `td_palette` reports. Checks the .tox is on disk first, and reports the name TouchDesigner actually gave the node. |
| `td_extension_add(class_name, code, path, parent, name, extension_name, promote, index, position, owner)` | Attach a Python class to a COMP as an extension — DAT, three Extensions parameters and the re-init in one call. Parses the code here first, and reads the result back: a class that fails to instantiate leaves the COMP reporting nothing at all. |
| `td_annotate(text, parent, title, name, path, size, color, position, font_size, mode, owner)` | Leave a note in the network saying what you built and why — a coloured box beside the nodes it describes. Pass `path` to rewrite a note instead of adding another. |
| `td_annotations(path, depth)` | Read the notes in a network, including a brief a person left for you, with the nodes each note's box sits over. Invisible to every other tool here. |
| `td_flags(path)` | The flags that decide whether a node runs and what is visible: display, render, bypass, cooking, and which ones this operator does not have. Check it when a correct-looking network produces nothing. |
| `td_set_flags(path, flags, owner)` | Turn those flags on or off. Every write is read back, so a flag the family will not take is a refusal rather than a silence. |
| `td_render(path, width, height)` | A TOP's image, returned to you. |
| `td_errors()` | Operators reporting an error or warning. |
| `td_exec(code)` | Arbitrary Python inside TouchDesigner. Last resort. |
| `td_undo(redo)` | Undo or redo, including whole `td_build` batches. |
| `td_snapshot(label, path)` | Save a component for later diffing. |
| `td_claim_scope(path, owner, ttl_seconds)` | Announce a subtree as yours before a run of edits, when another agent or session may be in the same project. Covers everything below `path`; lapses on its own. |
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
td-atlas build     # offline index, ~12 s
td-atlas install   # stage the bridge, print the bootstrap line
# paste that line into TouchDesigner's textport, once per project
td-atlas probe     # runtime facts: defaults, ranges, menus, type aliases
```
