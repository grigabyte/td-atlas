# Working on td-atlas

Guidance for agents contributing to this repository. For *using* the connector
to drive TouchDesigner, read `plugin/skills/touchdesigner/SKILL.md` instead.

## What this project is

Three layers, deliberately separable:

| Layer | Needs TouchDesigner? | Where |
| --- | --- | --- |
| **Atom index** — operators, parameters, docs, palette | No, once built | `src/td_atlas/atoms/` |
| **Bridge** — live control of a running instance | Yes | `src/td_atlas/bridge/` + `component/` |
| **Project reader** — .toe/.tox from disk | No | `src/td_atlas/project/` |

`mcp/server.py` exposes all three; `cli.py` is the same functionality for
humans. Keep that parity — a capability added to one should appear in the
other, or the omission should be deliberate and noted.

One omission is deliberate: **the MCP surface cannot aim at a chosen
instance.** The CLI's global `--port`/`--project` have no MCP equivalent;
every bridge tool dials whatever `BridgeClient.discover()` picks. The reason
is the failure mode, not the plumbing: `discover()` raises
`InstanceSelectionError` when a flag names no bridge or several, and an MCP
tool must return that as text rather than an exception, which would mean a
guard at every one of the twelve `bridge()` call sites. What the registry
was built to prevent is covered without it — `td_instances` lists every
running bridge and marks the one these tools reach, and `_warn` prefixes the
ambiguity warning onto every bridge result whenever more than one is running,
so an agent cannot edit the wrong project in silence.

## The rule that matters most here

**Measure, do not guess.** Almost none of what this project depends on is
documented by Derivative: the `.toe` container format, the parameter flag bits,
the contracted type names, which noise types run on the CPU. Every one of those
was settled by running an experiment against the shipped example libraries or
a live instance, and each of those findings carries a comment saying what was
measured and how many cases it covers.

Two of these were nearly shipped as plausible guesses, and both would have been
wrong:

- Scanning for the first newline to find a DAT payload looked correct on the
  first file tried. It silently truncates any payload whose length byte is not
  `0x0A` — about a third of the shaders.
- Expanding contracted type names by subsequence match reports 100% success and
  gets `parexecDAT` wrong, because it matches both `parameterexecuteDAT` and
  `pargroupexecuteDAT`.

If a heuristic can be confidently wrong, prefer leaving the answer unresolved,
or derive the real mapping from TouchDesigner.

## Running things

```bash
uv pip install -e . pytest
pytest                       # the default run: no TouchDesigner needed
pytest -m live               # the rest: needs one running, with the bridge
```

**A plain `pytest` does not need TouchDesigner running** — keep it that way.
That is now enforced rather than hoped for: `[tool.pytest.ini_options]` in
`pyproject.toml` deselects the `live` marker by default, so a test that dials
a live instance is not collected at all unless it is asked for by name. A new
test that reaches a running instance gets `@pytest.mark.live` (or a module
`pytestmark`); `--strict-markers` rejects a misspelling rather than silently
running it in the default set.

The reason for the split is what the old arrangement read like: the live tests
skipped, and every summary said two dozen checks had run when none of them
had. Deselecting says which set you are looking at.

Some tests need TouchDesigner *installed* — they shell out to `toeexpand` and
`toecollapse`, or read a shipped example — and skip without one. Those stay in
the default set: skipping is honest there, because the same checks pass on any
machine with the application present.

Rebuilding the index after changing an extractor:

```bash
td-atlas build               # offline, ~12 s
td-atlas probe               # needs TouchDesigner open with the bridge
td-atlas reload              # push handler changes into a running instance
```

`td-atlas reload` is the fast loop for `component/handler.py`: it re-stages the
sources and has the running bridge replace its own handler, so there is no need
to return to the textport.

## Packaging

```bash
.venv/bin/python scripts/build_mcpb.py     # bundle + registry submission
```

Two rules it enforces, both of which a hand-maintained manifest breaks inside
one release:

- **`packaging/manifest.json` is generated, not edited.** Everything it shares
  with the package is read from `pyproject.toml`, including the platform list —
  which is why the classifiers are the one place to state that Windows is
  unverified. `tests/test_packaging.py` fails when the committed copy drifts.
- **Only tracked files are packed.** The staging tree comes from `git archive`,
  so the index and the bridge token — both untracked, both machine-specific —
  are absent by construction rather than by an exclusion list that would have
  to be kept current. The build checks the packed listing against an allowlist
  regardless.

The bundle uses the `uv` server type, so it ships source and `pyproject.toml`
and lets the host resolve dependencies. A `python`-type bundle would have to
carry `mcp`'s compiled `pydantic-core` wheel, which is built for one CPU and
one Python minor and cannot be produced reproducibly from a clean checkout.
The launch string stays the one `cli.mcp_command()` settled on — `-m
td_atlas.cli mcp`; only the interpreter differs, because a bundle has no
`sys.executable` of the user's to point at.

`scripts/publish.sh` is the only thing here that sends anything outward, and
nothing calls it. Before the first `gh` it gates on five things, cheapest
first: a clean working tree (untracked files included — `git archive HEAD`
packs neither), a bundle whose `dist/build.json` names the current HEAD, a
`v<version>` tag that does not exist yet, a green `pytest`, and — last,
because it is the only gate that needs the network — a `git ls-remote` saying
the tag is not on `origin` either. That last one exists because the local
check passes for a tag deleted here but still published there, which is what
a half-finished release leaves behind; a `ls-remote` that fails to answer
fails the gate rather than reading as "absent". The SHA-256 in
`dist/server.json` must match the bundle too, since clients verify that hash
before installing.

## Code that runs inside TouchDesigner

`src/td_atlas/component/handler.py` and `bootstrap.py` execute in
TouchDesigner's embedded **Python 3.11**, not the host interpreter. Constraints:

- No third-party imports. Standard library only, plus the `td` globals.
- No 3.12+ syntax.
- Every request runs on TouchDesigner's main thread during a cook. A handler
  that blocks freezes the application; keep work bounded.
- `component/__init__.py` still deliberately imports nothing, and
  `bootstrap.py` is useless outside TouchDesigner. `handler.py` is the one
  exception: the host imports it on purpose, to read `PROTOCOL_VERSION`
  without keeping a second copy of it in sync. That only works because its
  module-level code stays plain stdlib, with every touch of TouchDesigner's
  injected globals (`op`, `app`, `me`, ...) pushed inside function bodies —
  keep it that way, since the host import depends on it, not just habit.

Probe snippets in `atoms/probe.py` are `%`-formatted templates. A literal `%`
inside one must be written `%%`.

## Adding an MCP tool

1. Write the function in `mcp/server.py` with a docstring aimed at an agent —
   the docstring *is* the interface, so say when to reach for it and what trap
   it avoids, not just what it returns.
2. Catch `BridgeUnavailable` and `BridgeError` and return the message as text.
   An exception surfaces as an opaque `ToolError`.
3. Return types feed a generated output schema. A union of `Image | str` fails
   to generate; leave the annotation off when a tool can return either.
4. Add it to `plugin/skills/touchdesigner/references/tools.md`.

## Search ranking

Full-text search has been wrong twice in the same way, so check any new search
surface against real queries before believing it.

FTS5 ANDs bare terms, which makes plain-language questions match nothing;
`AtomStore.fts_query` ORs them instead. But ORing lets an operator matching one
common word outrank the one that fits — `td_search_operators` and `td_palette`
both had to search names and labels first and only widen to body text when the
result set is thin. `AtomStore.match` additionally re-parses any query that
looks like FTS5 syntax, because prose can accidentally contain `NEAR(` or an
unbalanced quote and SQLite raises rather than returning nothing.

## Conventions

- Comments explain *why*, and cite the measurement when a value was derived
  experimentally. Do not narrate what the next line does.
- Docstrings on public functions state the failure mode they exist to prevent.
- Prefer an honest gap over a confident wrong answer, in code and in output.
- British spelling in prose is fine; identifiers stay ASCII.

## Things to be careful with

- **Never write beside a user's file.** `toeexpand`/`toecollapse` work in place
  and rename originals to `.bkp`; `project/expand.py` always copies into a
  cache first. Preserve that.
- **`td_snapshot` must not repoint the session.** `project.save(path)` is a
  Save As and moves the artist's working file; the tool saves a component
  instead.
- The index contains machine-specific values — device menus include real audio
  hardware names. It is a local cache, not something to publish.

## Verified facts worth not re-deriving

Recorded in `docs/formats.md` with the evidence. Briefly: the payload prologue
is 27 bytes ending in a length field (686/686 files); parameter flag bit `0x10`
marks expression mode (14,835/14,835 lines across 2,861 files); there are 71
contracted type names; the wiki mirror puts pages whose title contains a slash
into subdirectories.
