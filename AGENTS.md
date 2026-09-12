# Working on td-atlas

Guidance for agents contributing to this repository.
`plugin/skills/touchdesigner/SKILL.md` covers *using* the connector to drive
TouchDesigner.

## What this project is

Three layers, separable:

| Layer | Needs TouchDesigner? | Where |
| --- | --- | --- |
| **Atom index** — operators, parameters, docs, palette | No, once built | `src/td_atlas/atoms/` |
| **Bridge** — live control of a running instance | Yes | `src/td_atlas/bridge/` + `component/` |
| **Project reader** — .toe/.tox from disk | No | `src/td_atlas/project/` |

`mcp/server.py` exposes all three, and `cli.py` is the same functionality for
humans. Keep that parity. A capability added to one appears in the other, or
the gap is declared in the parity section below, which
`tests/test_cli_mcp_parity.py` holds to the code.

**The MCP surface cannot aim at a chosen instance.** That one gap is declared
in prose, since it is about the global flags and not about any one capability.
The CLI's global `--port`/`--project` have no MCP equivalent, and every bridge
tool dials whatever `BridgeClient.discover()` picks. `td_instances` lists every
running bridge and marks the one these tools reach, and `_warn` prefixes the
ambiguity warning onto every bridge result whenever more than one is running,
so an agent cannot edit the wrong project in silence. A tool-side selector
would cost a guard at every `bridge()` call site, since `discover()` raises
`InstanceSelectionError` when a flag names no bridge or several, and an MCP
tool has to answer in text.

## CLI ↔ MCP parity

`tests/test_cli_mcp_parity.py` compares the two surfaces capability by
capability. It reads the four tables below, walks `build_parser()` and the
`@mcp.tool()` definitions, and fails when the code and the tables disagree.

A *capability* is one MCP tool, or one CLI subcommand. `project` and
`project variant` are counted by their actions (`project read`, `project
variant save`, …), which is the level at which an MCP tool corresponds to
anything.

### Paired

Seventeen capabilities exist on both sides. Where the two spell an argument
differently, the rename is declared here, and the test applies it before
comparing the argument sets. The CLI's global `--db`/`--port`/`--project` are
excluded as the declared gap above.

| CLI | MCP | Renames |
| --- | --- | --- |
| `instances` | `td_instances` | |
| `status` | `td_status` | |
| `doctor` | `td_doctor` | |
| `search` | `td_search_operators` | |
| `op` | `td_operator_schema` | `type`→`op_type` |
| `exec` | `td_exec` | |
| `render` | `td_render` | |
| `log` | `td_log` | `number`→`limit` |
| `project read` | `td_project_read` | |
| `project text` | `td_project_text` | |
| `project write` | `td_project_write` | |
| `project grep` | `td_project_grep` | |
| `project diff` | `td_project_diff` | `file`→`before`, `other`→`after`, `moves`→`show_moves`, `no_text`→`include_text` |
| `project variant save` | `td_variant_save` | |
| `project variant list` | `td_variant_list` | |
| `project variant restore` | `td_variant_restore` | |
| `project variant diff` | `td_variant_diff` | `label`→`before`, `other`→`after`, `moves`→`show_moves`, `no_text`→`include_text` |

### CLI only

Nine capabilities. In every row the reason has the same shape: something a
person does to this machine once, or something that writes a directory to disk.

| CLI | Why a person runs it |
| --- | --- |
| `install` | Stages the bridge and prints a line to paste into TouchDesigner. An agent that could install the bridge would need the bridge to do it. |
| `release-tox` | Builds a distributable `.tox` of the bridge. Maintenance of this project, not use of it. |
| `build` | Rebuilds the index from a TouchDesigner installation — 13–16 s of one-off setup, and `td_doctor` already says to run it. |
| `probe` | The same, for the runtime half of the index: it needs a running instance whose cook it will occupy for a minute. |
| `reload` | Replaces the running bridge's own handler. A tool that can restart its own transport reports its outcome to nobody. |
| `mcp` | Starts the MCP server. It is how the tools exist; it cannot be one of them. |
| `project expand` | Unpacks a project into a directory and prints the path. Its whole output is a filesystem location an agent cannot read from. |
| `project collapse` | Repacks such a directory into a `.toe`/`.tox`. The MCP side writes projects with `td_project_write`, which takes text, not a directory. |
| `project scripts` | Writes every DAT's contents out as files. `td_project_text` gives an agent the same content in one string. |

### MCP only

Twenty-four, in three groups. The CLI's live surface is the few commands a
person types at a terminal, which are `exec`, `render`, `status`, `instances`
and `log`. A bridge capability gets a subcommand when someone wants to type it,
not for symmetry.

| MCP | Group | Why an agent calls it |
| --- | --- | --- |
| `td_docs`, `td_glossary`, `td_example`, `td_expression_help`, `td_python_api`, `td_palette`, `td_search_parameters`, `td_op_info` | index lookups | A person reads the wiki, the palette browser and the operator's own help; `search` and `op` cover what a terminal is actually better at. |
| `td_build`, `td_set_params`, `td_flags`, `td_set_flags`, `td_network`, `td_errors`, `td_annotate`, `td_annotations`, `td_undo`, `td_snapshot`, `td_extension_add`, `td_palette_load`, `td_health` | live editing | A person editing a network does it in TouchDesigner, where the result is visible. These exist because an agent cannot see the network. |
| `td_claim_scope`, `td_release_scope`, `td_scopes` | scope claims | An agreement between agents about which subtree each may touch. A person at a terminal is the party the claims protect, not one of the claimants. |

### Declared argument divergences

Everything else must match. These do not, and each row says why; anything not
listed here fails the test.

| Pair | Divergence | Why |
| --- | --- | --- |
| `op` / `td_operator_schema` | `--groups` is CLI only | The tool returns parameter *members* (`tx`, `ty`) and never the documented *groups* (`t`), because an agent that sets `t` gets a refusal it cannot read. The flag exists for a person cross-reading Derivative's own docs, which name the groups. |
| `render` / `td_render` | `-o/--output` is CLI only; `width` defaults to 512 in the tool and to the TOP's own resolution in the CLI | The tool hands the image back inline, so it has nowhere to write and every pixel costs context; the CLI writes a file, where the artist's own resolution is the right answer. |
| `project read` / `td_project_read` | `--refresh` is CLI only | The expansion cache is keyed on the file's path, size and mtime, so a changed project is re-expanded without asking. The flag is a repair for a cache damaged by something outside this program — a person's problem, diagnosed at a terminal. |
| `project text` / `td_project_text` | `--refresh` and `-o/--output` are CLI only; `max_bytes` is MCP only | `--refresh` as above. The tool must fit its answer in a context window, so it refuses a network over `max_bytes`; the CLI writes to a file or a pipe, where there is no such ceiling and `-o` is the whole point. |
| `project grep` / `td_project_grep` | `--fixed` is CLI only | An agent composing a pattern can escape it; a person typing `v1.2.3` at a prompt cannot be asked to. |
| `doctor` / `td_doctor` | `--install-path` and `--clear-cache` are CLI only | Both change this machine — one points the index at another installation, the other deletes every cached expansion. Repairs belong to whoever owns the machine. |

## The rule that matters most here

**Measure, do not guess.** Almost none of what this project depends on is
documented by Derivative. That covers the `.toe` container format, the
parameter flag bits, the contracted type names, and which noise types run on
the CPU. Every one of those was settled by running an experiment against the
shipped example libraries or a live instance, and each finding carries a
comment saying what was measured and how many cases it covers.

Two of these were nearly shipped as plausible guesses, and both would have been
wrong:

- Scanning for the first newline to find a DAT payload looked correct on the
  first file tried. It silently truncates any payload whose length byte is not
  `0x0A`, which is about a third of the shaders.
- Expanding contracted type names by subsequence match reports 100% success and
  gets `parexecDAT` wrong, because it matches both `parameterexecuteDAT` and
  `pargroupexecuteDAT`.

If a heuristic can be confidently wrong, prefer leaving the answer unresolved,
or derive the real mapping from TouchDesigner.

## Invariants a change has to keep

Two gates are not obvious from the code that has to satisfy them, and a named
test holds each. `project/rebuild.py` and `tests/test_health.py` point here for
the rule they serve.

- **The round trip is empty.** Dump a project to network text, build it back,
  dump it again, and the two texts are identical.
  `tests/test_rebuild.py::test_the_round_trip_invariant_holds_on_a_shipped_component`
  runs it on a shipped palette component, and the edited variant beside it
  keeps an identity copy from passing for free. The gate holds everything the
  text *describes*. Panel layouts, custom parameter definitions and CHOP caches
  are not described, and what holds those is the rebuild being a patcher, which
  no dump can test. The argument for the patcher is in `project/rebuild.py`.
- **Every new `td_health` section states its own cost in a test.** The cost of
  parsing a section is measurable on the host with no TouchDesigner running, so
  a new section arrives with the measurement.
  `tests/test_health.py::test_prints_what_each_new_section_costs` takes the
  measurement and prints it.
  `tests/test_health.py::test_every_health_section_states_its_cost` makes the
  arrival compulsory, deriving every finding kind `health.py` builds and
  failing on one absent from the listing of costs. A section that adds no new
  kind escapes it, since an existing finding taught to read a new and expensive
  field keeps its name, and that half is the review's, the way the reply shape
  is in `test_protocol_fingerprint.py`.

## Running things

```bash
uv pip install -e . pytest
pytest                       # the default run: no TouchDesigner needed
pytest -m live               # the rest: needs one running, with the bridge
```

**A plain `pytest` does not need TouchDesigner running.** Keep it that way.
`[tool.pytest.ini_options]` in `pyproject.toml` deselects the `live` marker by
default, so a test that dials a live instance is not collected at all unless it
is asked for by name. A new test that reaches a running instance gets
`@pytest.mark.live`, or a module `pytestmark`. `--strict-markers` rejects a
misspelling.

Some tests need TouchDesigner *installed*, since they shell out to `toeexpand`
and `toecollapse`, or read a shipped example, and they skip without one. Those
stay in the default set, and the same checks pass on any machine with the
application present.

Rebuilding the index after changing an extractor:

```bash
td-atlas build               # offline, 13-16 s
td-atlas probe               # needs TouchDesigner open with the bridge
td-atlas reload              # push handler changes into a running instance
```

`td-atlas reload` is the fast loop for `component/handler.py`. It re-stages the
sources and has the running bridge replace its own handler, so there is no need
to return to the textport.

## Packaging

```bash
.venv/bin/python scripts/build_mcpb.py     # bundle + registry submission
```

Two rules it enforces, both of which a hand-maintained manifest breaks inside
one release:

- **`packaging/manifest.json` is generated, not edited.** Everything it shares
  with the package is read from `pyproject.toml`, including the platform list,
  so the classifiers are the one place to state that Windows is unverified.
  Unverified means TouchDesigner on Windows. The OS itself has run in CI since
  2026-09-07, and `docs/compatibility.md` keeps the two apart.
  `tests/test_packaging.py` fails when the committed copy drifts, encoding
  included, so every file the builder writes names UTF-8.
- **Only tracked files are packed.** The staging tree comes from `git archive`,
  so the index and the bridge token, both untracked and both machine-specific,
  are absent by construction. The build checks the packed listing against an
  allowlist regardless.

The bundle uses the `uv` server type, so it ships source and `pyproject.toml`
and lets the host resolve dependencies. A `python`-type bundle would have to
carry `mcp`'s compiled `pydantic-core` wheel, which is built for one CPU and
one Python minor and cannot be produced reproducibly from a clean checkout. The
launch string stays the one `cli.mcp_command()` settled on, which is
`-m td_atlas.cli mcp`. Only the interpreter differs, since a bundle has no
`sys.executable` of the user's to point at.

`scripts/publish.sh` is the only thing here that sends anything outward, and
nothing calls it. Before the first `gh` it gates on five things, cheapest
first. A clean working tree, untracked files included, since `git archive HEAD`
packs neither. A bundle whose `dist/build.json` names the current HEAD. A
`v<version>` tag that does not exist yet. A green `pytest`. Last, and the only
gate that needs the network, a `git ls-remote` saying the tag is not on
`origin` either. That last one catches a tag deleted here but still published
there, which is what a half-finished release leaves behind. A `ls-remote` that
fails to answer fails the gate. The SHA-256 in `dist/server.json` must match
the bundle too, since clients verify that hash before installing.

## Code that runs inside TouchDesigner

`src/td_atlas/component/handler.py` and `bootstrap.py` execute in
TouchDesigner's embedded **Python 3.11**, not the host interpreter. Constraints:

- No third-party imports. Standard library only, plus the `td` globals.
- No 3.12+ syntax.
- Every request runs on TouchDesigner's main thread during a cook. A handler
  that blocks freezes the application; keep work bounded.
- `component/__init__.py` imports nothing, and `bootstrap.py` is useless
  outside TouchDesigner. `handler.py` is the one exception. The host imports it
  to read `PROTOCOL_VERSION` without keeping a second copy of it in sync. That
  works only while its module-level code stays plain stdlib, with every touch
  of TouchDesigner's injected globals (`op`, `app`, `me`, ...) pushed inside
  function bodies. Keep it that way, since the host import depends on it.

**Any change to the `METHODS` table raises `PROTOCOL_VERSION`, and
`MIN_PROTOCOL_VERSION` follows it.** Adding or removing a method counts. So
does a change to a method's name, to the set of parameters it reads, or to the
shape of the reply the host reads back. The case this guards is a bridge
component left inside a project across an upgrade, since host and bridge ship
together in one `.mcpb` bundle. Such a bridge answers with
the number it was laid down with, and if that number did not move, the host
cannot tell it from a current one, and the failure it produces is a
plausible-looking answer to a different question.
`tests/test_protocol_fingerprint.py` derives the method names and their
parameter keys from the source and compares them with a listing recorded
against the current number. Reply shape it cannot see, and that half is held by
this rule, the review, and the `CHANGELOG.md` obligation in `CONTRIBUTING.md`.

Probe snippets in `atoms/probe.py` are `%`-formatted templates. A literal `%`
inside one must be written `%%`.

## Adding an MCP tool

1. Write the function in `mcp/server.py` with a docstring aimed at an agent.
   The docstring *is* the interface, so say when to reach for it and what trap
   it avoids, not just what it returns.
2. Put `@guarded` under `@mcp.tool()`. It catches `BridgeUnavailable` and
   `BridgeError` and returns the message with a repair hint attached; an
   exception that escapes surfaces to the agent as an opaque `ToolError`.
   `tests/test_recovery_hints.py` asserts that every bridge tool carries the
   decorator, so a new tool without it fails.
3. Return types feed a generated output schema. A union of `Image | str` fails
   to generate; leave the annotation off when a tool can return either.
4. Add it to `plugin/skills/touchdesigner/references/tools.md`.

## Search ranking

Full-text search has been wrong twice in the same way, so check any new search
surface against real queries before believing it.

FTS5 ANDs bare terms, which makes plain-language questions match nothing, and
`AtomStore.fts_query` ORs them. But ORing lets an operator matching one common
word outrank the one that fits, so `td_search_operators` and `td_palette` both
search names and labels first and widen to body text only when the result set
is thin. `AtomStore.match` additionally re-parses any query that looks like
FTS5 syntax, since prose can accidentally contain `NEAR(` or an unbalanced
quote and SQLite raises.

## Conventions

- Comments explain *why*, and cite the measurement when a value was derived
  experimentally. Do not narrate what the next line does.
- Docstrings on public functions state the failure mode they exist to prevent.
- Prefer an honest gap over a confident wrong answer, in code and in output.
- British spelling in prose is fine; identifiers stay ASCII.

## Things to be careful with

- **Never write beside a user's file.** Both helpers work in place, and not in
  the same way. `toeexpand` writes `<file>.dir` and `<file>.toc` beside its
  input and leaves the input itself alone. `toecollapse` moves whatever already
  sits at its destination aside to `<file>.bkp1`, or to `<file>.bkp2` on a
  second run (measured 2026-09-07, `docs/formats.md`). `project/expand.py`
  always copies into a cache first. Preserve that.
- **`td_snapshot` must not repoint the session.** `project.save(path)` is a
  Save As and moves the artist's working file; the tool saves a component.
- The index contains machine-specific values, and device menus include real
  audio hardware names. It is a local cache, not something to publish.

## Verified facts worth not re-deriving

Recorded in `docs/formats.md` with the evidence. The payload prologue is 27
bytes ending in a length field (686/686 files). Parameter flag bit `0x10` marks
expression mode (14,835/14,835 lines across 2,861 files). There are 71
contracted type names. The wiki mirror puts pages whose title contains a slash
into subdirectories.
