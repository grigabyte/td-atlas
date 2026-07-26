# Tool reference

Twenty-three MCP tools in three groups.

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
| `td_health(path, interval)` | **The silent-failure detector.** Run after building anything. |
| `td_network(path, depth)` | What is inside a component and how it is wired. |
| `td_op_info(path)` | One operator: type, wiring, live parameter values, errors. |
| `td_build(operations, undo_name)` | Multi-step edits as one atomic, undoable block. Validated first. |
| `td_set_params(path, pars, op_type)` | Set parameters on an existing operator. |
| `td_render(path, width, height)` | A TOP's image, returned to you. |
| `td_errors()` | Operators reporting an error or warning. |
| `td_exec(code)` | Arbitrary Python inside TouchDesigner. Last resort. |
| `td_undo(redo)` | Undo or redo, including whole `td_build` batches. |
| `td_snapshot(label, path)` | Save a component for later diffing. |

## Project files — on disk

| Tool | Use it for |
| --- | --- |
| `td_project_read(file, path, depth, params)` | Operator tree of a .toe/.tox without TouchDesigner. |
| `td_project_grep(file, pattern, limit)` | Search the Python and GLSL inside DATs. |
| `td_project_diff(before, after, show_moves, include_text)` | Semantic comparison of two files. |

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
