# Why the code is shaped like this

A dated register of the decisions that explain the shipped product: one line
each, with a pointer to the code, comment or document that carries the
reasoning and the evidence. It is a register, not a second narration — where a
decision is already argued somewhere in this repository, the line here says
where rather than repeating the argument.

**Numbers are stable and not consecutive.** Several comments in `src/` cite a
decision by number ("for the reason decision 22 records"), so the numbering is
kept as it was assigned, gaps and all. A gap is a decision that shaped how
this project is *worked on* rather than how it is built — the owner's working
notes, positioning, method — and those are not a reader's business. Forty-eight
of sixty are here; the twelve left out are the project's stance on its
neighbours, one decision cancelled the day it was taken, the owner's machine
and working method, the genre rules of the owner's own working notes, and the
decision to keep those notes out of the repository — which is about process
too, and which `.gitignore` explains where a reader actually meets it.

Nothing here is a promise about the future. A line that stops being true gets
struck rather than deleted, so a reader who found it in an old comment can see
what happened to it.

## The bridge and the protocol

| # | Date | Decision |
| --- | --- | --- |
| 7 | 2026-08-28 | The bridge stays a **script** as the source of truth, and the drag-and-drop `.tox` is generated from it at release time by `collapse()`. It is readable and diffable, it lives in version control as source, and re-running the bootstrap upgrades it in place — see [architecture.md](architecture.md#the-bridge). |
| 8 | 2026-08-28 | The expected protocol version is **imported** from `component/handler.py`, not copied into the host. That is why `handler.py`'s module level must stay plain standard library, with every touch of TouchDesigner's injected globals inside a function body — the rule is in [AGENTS.md](../AGENTS.md#code-that-runs-inside-touchdesigner). |
| 11 | 2026-08-28 | A protocol mismatch is reported as `BridgeUnavailable`, not a new exception type: from a caller's side an incompatible bridge and an absent one need the same handling. |
| 12 | 2026-08-28 | **No token is baked into the released `.tox`.** One file goes to every machine; the handler reads the token from `~/.td-atlas/config.json` when its server starts, re-reading lazily on the first request. Both install paths ship the same handler text, held by a test. |
| 13 | 2026-08-28 | An instance-registry entry is **live** if the port is listening and the process exists — the two things host and bridge can check identically. The process *name* is deliberately not recorded: from inside TouchDesigner you see its embedded interpreter, from outside you see the application. |
| 14 | 2026-08-28 | When the bridge is installed by pasting the bootstrap line, `bootstrap.py` registers the instance itself. Measured: `onServerStart` does not fire when `active` is toggled from a script, though it does fire through the `.tox` path. |
| 19 | 2026-08-28 | `~/.td-atlas` is resolved by one rule in three places (`config.py`, `component/handler.py`, `component/bootstrap.py`): the `TD_ATLAS_HOME` environment variable, where an empty string counts as unset. |
| 21 | 2026-08-28 | Both error surfaces are read through the supported API — a shader's `compileResult` property and `scriptErrors(recurse=True)` — never through a temporary Info DAT. Errors that were already there are **not cleared**: clearing is an edit to someone's state, not an observation. |
| 22 | 2026-08-28 | Scope claims live in a **Table DAT** inside the bridge component, not in a module variable: TouchDesigner re-executes the module body between requests and a claim vanished silently. The carrier was chosen by measuring three of them; what decided it was that a table is visible to a person looking at the network. Rows are tied to a pid. See the comments at `handler.py`'s claims table. |
| 23 | 2026-08-28 | An extension is attached with `op('./<DAT>').module.<Class>(me)`, not the `<Class>(me)` form the offline wiki gives: the wiki form silently leaves `extensions[0]` as `None`. The bridge method reads the result back and, on `None`, digs out the real exception. |
| 24 | 2026-08-28 | A failed batch rolls back exactly as many undo entries as the stack grew during it: `len(ui.undo.undoStack)` is read before `startBlock` and after `endBlock`. The count is read, never predicted. Three residual risks are named in the code and not closed. |
| 41 | 2026-09-06 | The **minimum** accepted protocol version equals the expected one. An older bridge is refused at connect, with instructions to re-stage it, rather than warned about and then allowed to fail on the first new method. There is no backwards compatibility with any version — a deliberate choice, since host and bridge ride in one bundle. |
| 42 | 2026-09-06 | `td-atlas reload` is the one command that goes around the version check (`BridgeClient.enforce_protocol`): replacing an outdated handler is precisely its job, and decision 41 would otherwise stop the command that repairs the problem. A mismatch is printed as a warning. |
| 49 | 2026-09-06 | The bridge methods `perf`, `par_get` and `save` were **removed**, not covered by tests: the first two duplicated `health_sample` and `op_info`, and `save` called `project.save()` — a Save As that moves the artist's working file, which nothing in this project may do. |
| 54 | 2026-09-07 | Protocol raised to 6: the set of methods *is* the wire, so removing three of them (49) is a protocol change and not internal tidying. Superseded the same day by 57. |
| 57 | 2026-09-07 | Protocol at **7**, and the rule written down: any change to the `METHODS` table — a method's name, the parameters it reads, the shape of the reply — raises `PROTOCOL_VERSION`, with the minimum following it. The reason is the delivery, not the wire format; the rule and its two past failures are in [AGENTS.md](../AGENTS.md#code-that-runs-inside-touchdesigner), and `tests/test_protocol_fingerprint.py` holds the half a test can see. |

## Telling the truth about what happened

| # | Date | Decision |
| --- | --- | --- |
| 30 | 2026-08-29 | The status panel inside TouchDesigner repaints **on request, not on a timer**, and every time on it is absolute. A stale absolute time reads as stale; a stale relative one lies. The panel never enters the artist's undo stack, and its state lives in a Table DAT beside the claims (22). |
| 31 | 2026-08-29 | The panel does not compute the health verdict, it **receives** one: the rules live on the host, which sends the verdict through a bridge method, best-effort. |
| 33 | 2026-08-29 | The panel uses a monospaced font and wraps long values itself — the inherited Verdana is proportional and silently ruined both the columns and the long lines. The wrapping is checked by a pure function on the host, with no TouchDesigner running. |
| 32 | 2026-08-29 | The differences between the network text printed from a live instance and the text read from a saved file are reduced to **seven classes, each explained by a mechanism**, with nothing left over. The table is in [the README](../README.md#the-network-text-written-on-save) and reproduced by a test. Two of our own defects that the same measurement exposed were fixed. |
| 34 | 2026-08-30 | The call journal is written by the **host** to `~/.td-atlas/calls.jsonl`, not by the bridge into a Table DAT, which would die with the process. The payload is taken by allow-list and the token is never written — held by a test. Offline tools do not log, and that gap is named. See [the README](../README.md#the-call-journal). |
| 44 | 2026-09-06 | **A partial answer must say it is partial.** Every cut — child count, depth, node budget, frame buffer — returns a marker and a count of what was not shown. A silent truncation reads as a whole answer, which makes "nothing found" a lie rather than a result. |
| 50 | 2026-09-06 | The journal names its own failures: a write error is remembered and printed under the report, so an unreadable file is distinguishable from an absent one. The token-scrubbing cache is keyed on the path **and** the mtime of `config.json`; the previous key never expired, and a live token reached the log in clear text. One race in `_trim` is deliberately not fixed, for the reason `journal.py` gives. |
| 55 | 2026-09-07 | `errors` walks from `/project1`, not from `/`: a breadth-first walk spent its 5000-node budget on TouchDesigner's own `/ui` and `/sys` and never reached the project. A `path` argument was added, the budget kept (the cost is linear and nothing bounds a project's size), and the reply names the subtree it covered. TouchDesigner's own interface errors are now behind `path=/`. |
| 56 | 2026-09-07 | A node at the depth limit prints **its own child count** rather than getting a truncation marker: raising `truncated` there would have named the wrong limit. |

## Reading and writing project files

| # | Date | Decision |
| --- | --- | --- |
| 25 | 2026-08-28 | The network is serialised as **standard JSON with a non-standard printer** — DAT text as an array of lines, vectors and short lists on one line. Measured against three alternatives; what decided it is that the parser costs nothing and loses nothing. See [formats.md](formats.md#the-network-text-and-why-it-is-json). |
| 26 | 2026-08-28 | The network text is written **beside the `.toe`**, in the project directory: visibility to git was chosen over a clean working folder. Node positions are not split into a separate section and defaults are not stripped from the text — both settled by measurement, both in [formats.md](formats.md#the-network-text-and-why-it-is-json). |
| 27 | 2026-08-28 | A `.parm` line is read **positionally**, and the number of values on it is decided by two flag bits and nothing else. Anything unrecognised is carried through verbatim in a separate field with mode `unknown`. The bits, and the evidence for them, are in [formats.md](formats.md#parm--parameters). |
| 29 | 2026-08-28 | A saved variant holds the network text **plus a byte copy** of the original `.toe`/`.tox`, under `~/.td-atlas/variants/`. Correctness decides this, not size: the rebuild is a patcher, so text alone cannot produce a container. A changed source is no longer a reason to refuse a restore — the drift is reported. |
| 43 | 2026-09-06 | The expansion cache evicts by **counting entries and never weighing them**: weighing a real cache costs three orders of magnitude more than counting it, measured. A ceiling in entries is coarser than one in bytes and is the only one that can be checked on every expansion. The measurement is in the comment at `project/expand.py`. |
| 45 | 2026-09-06 | The refusal to overwrite an existing output file lives in the **shared layer** (`project/rebuild.py`), not on the MCP surface where it used to be alone while the CLI walked past it. "Never write beside a user's file" cannot be held by one of two surfaces. |

## The surfaces, packaging and the checks

| # | Date | Decision |
| --- | --- | --- |
| 9 | 2026-08-28 | The MCP launch string uses `sys.executable` **without resolving the symlink**: resolving it loses an editable install. Measured in both directions. |
| 10 | 2026-08-28 | The package classifiers list only what has actually been run: Windows and Linux are absent — Windows was never verified, and TouchDesigner does not exist for Linux. |
| 6 | 2026-08-28 | Windows is supported **blind, now** rather than later: path and separator handling is written for it and tested against `PureWindowsPath`. It is marked unverified in the README until it runs on a real machine. |
| 20 | 2026-08-28 | No claim about Windows is made anywhere in the code without a note that it is unverified. |
| 18 | 2026-08-28 | Linux installation discovery was **deleted**, not kept with a caveat: TouchDesigner is not released for Linux. An unknown system gets an error naming `--install-path` and `TD_ATLAS_INSTALL`. |
| 15 | 2026-08-28 | The global `--port`/`--project` and the same-named flag on `install` are separated by what they address, and a selector handed to a command that does not reach a bridge is an **error, not silence**. See [cli.md](cli.md#global-flags-go-before-the-subcommand). |
| 16 | 2026-08-28 | The MCP surface deliberately **cannot** aim at a chosen instance: `discover()` raises, and a tool must return text rather than an exception. In exchange there is a `td_instances` tool and an ambiguity warning on every reply. The gap is declared in [AGENTS.md](../AGENTS.md#cli--mcp-parity). |
| 48 | 2026-09-06 | CLI↔MCP parity is declared in **four tables** in [AGENTS.md](../AGENTS.md#cli--mcp-parity) and checked against the code by a test. The previous rule was prose, and by the time anyone counted, five gaps were declared and eleven were not. |
| 28 | 2026-08-28 | The server is distributed as an `.mcpb` bundle from a GitHub release, which removes the dependency on PyPI — the MCP registry accepts `registryType: "mcpb"`. The bundle's server type is **`uv`**, so it ships source and lets the host resolve; a `python` bundle would have to carry a compiled wheel built for one CPU and one Python minor. The tool list is not written into the manifest (`tools_generated: true`), and the skill installs as a plugin. |
| 39 | 2026-09-06 | The **plugin root is a subdirectory**, `plugin/`, and `marketplace.json` points at it. `"source": "./"` pulled every tracked file in the repository into an install — tests, sources and working notes alike. The contents of `plugin/` are fixed by set equality in `tests/test_packaging.py`, not by a handful of absence checks. |
| 40 | 2026-09-06 | `scripts/publish.sh` gates on five things before anything goes outward, cheapest first; they are listed in [AGENTS.md](../AGENTS.md#packaging). The previous check compared a SHA-256 instead of reading `dist/build.json`, and would have published a stale `dist/` in silence. |
| 35 | 2026-08-30 | The agent skill is held to the same rule as the code — every claim checked against the code and against a live TouchDesigner — and the drift no longer accumulates in silence: `tests/test_skill_reference.py` compares the reference with the source as text, touching neither the index nor the bridge. |
| 46 | 2026-09-06 | Live tests sit behind a **marker**, `live`, deselected by default in `pyproject.toml` — not behind a skip. The old arrangement said two dozen checks had run when none of them had. |
| 47 | 2026-09-06 | The ruff rule set is `E4, E7, E9, F, W`. The rest were dropped case by case rather than by the size of a counter, with the reason for each in `pyproject.toml`. Restoring any one of them is work on the code, not a line in a config. |
| 51 | 2026-09-06 | The version lives in `pyproject.toml` and a test holds the other three copies to it; a `CHANGELOG.md` entry is obligatory for a `PROTOCOL_VERSION` change and for anything that alters what the connector may do inside a user's project. CI runs `pytest` and `ruff` on macOS and Windows, Python 3.11–3.14. |
| 59 | 2026-09-07 | Two gates a change has to keep are written down in [AGENTS.md](../AGENTS.md#invariants-a-change-has-to-keep) rather than left in the owner's notes, because `project/rebuild.py` and `tests/test_health.py` point at them: the network text's empty round trip, and every new `td_health` section stating its own cost in a test. `CONTRIBUTING.md` was the alternative and lost — that page is the short version of how to run the checks, these are rules about what a change may not break. |
| 60 | 2026-09-07 | The three addresses that resolve to nothing before the first release — the clone URL, the plugin marketplace, and the release `dist/server.json` points at — are **marked where they appear** rather than removed or moved into an "after publication" section, which would have cut the install path in two. A paragraph at the head of [Install](../README.md#install) names all three. Struck at the first release, when they stop being 404s. |
