# Troubleshooting

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
