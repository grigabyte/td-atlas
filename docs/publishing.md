# Releasing

What is actually checked before a release, and by what. This is not a process
somebody would like to have — it is the one already written into
`scripts/build_mcpb.py`, `scripts/publish.sh` and the test suite, plus the
things those do **not** check, named at the bottom so they are not mistaken
for covered.

Nothing has been released yet. `scripts/publish.sh` has never run to
completion, so every gate below has been read and exercised only up to the
point where the script would go outward.

## The order

```bash
uvx ruff check .                               # 1
.venv/bin/python -m pytest -q                  # 2
.venv/bin/python -m pytest -q -m live          # 3, needs TouchDesigner + bridge
#  edit CHANGELOG.md, bump pyproject.toml if the version moves   # 4
.venv/bin/python -m pytest -q tests/test_packaging.py            # 5
git commit …                                   # 6, the tree must be clean
.venv/bin/python scripts/build_mcpb.py         # 7, packs from HEAD
./scripts/publish.sh                           # 8, gates then goes outward
```

Steps 7 and 8 belong together. The submission in `dist/server.json` carries
the SHA-256 of the bundle built beside it, and clients verify that hash before
installing — so a rebuild without a re-publish, or a publish without a
rebuild, ships a bundle nobody can install.

## Each item, and the command that checks it

| What has to hold | Checked by |
| --- | --- |
| The linter is clean over the whole tree, `src/td_atlas/component/` included with its own globals declared | `uvx ruff check .` |
| The offline suite is green | `.venv/bin/python -m pytest -q` — also gate 4 of `publish.sh`, so a red suite stops the release even if this step is skipped |
| The live suite is green against a running instance | `.venv/bin/python -m pytest -q -m live`. **`publish.sh` does not run this**, and cannot: a GitHub runner has no TouchDesigner. It is a hand step |
| The version is the same string in `pyproject.toml`, `src/td_atlas/__init__.py`, `packaging/manifest.json` and `plugin/.claude-plugin/plugin.json` | `tests/test_packaging.py::test_the_version_is_the_same_string_in_every_file_that_states_it` — inside a plain `pytest`, so gate 4 covers it |
| `packaging/manifest.json` is what the builder would write, not something edited by hand | `tests/test_packaging.py::test_the_committed_manifest_is_what_the_builder_would_write` |
| `plugin/` holds exactly what an install should carry | `tests/test_packaging.py`, by set equality rather than a handful of absence checks |
| `CHANGELOG.md` has an entry — obligatory for a `PROTOCOL_VERSION` change and for anything that alters what the connector may do inside a user's project | nothing automated. The obligation is stated in `CONTRIBUTING.md` and in `CHANGELOG.md`'s own header; the reviewer is the check |
| The bundle validates and packs, and holds only allowlisted files | `.venv/bin/python scripts/build_mcpb.py` — it stages from `git archive HEAD`, validates and packs through `npx @anthropic-ai/mcpb@2`, and checks the packed listing against `ALLOWED_IN_BUNDLE` / `src/td_atlas/` |
| The bundle was packed from the commit being released | gate 2 of `publish.sh`: `dist/build.json` records the HEAD that was archived, compared with `git rev-parse HEAD` |
| The working tree is clean, untracked files included | gate 1 of `publish.sh`. Untracked counts as dirt on purpose: `git archive HEAD` packs neither, so a file only in the working tree is the shape of a forgotten `git add` |
| The `v<version>` tag is free here | gate 3 of `publish.sh`, `git rev-parse -q --verify refs/tags/v<version>` |
| The SHA-256 in `dist/server.json` matches the bundle | an unnumbered check in `publish.sh` between gates 3 and 4, `shasum -a 256` against the declared `fileSha256` |
| `gh` and `mcp-publisher` are installed | an unnumbered check in `publish.sh`, after gate 4 |
| The tag is free on `origin` too | gate 5 of `publish.sh`, the only gate that touches the network. `git ls-remote --exit-code`; anything but 0 or 2 fails the gate rather than reading as "absent" |

Five of those are the numbered gates `AGENTS.md` counts; the SHA-256 match and
the tool check sit between and after them and are checks all the same.

At release, `CHANGELOG.md`'s `[Unreleased]` heading becomes
`[<version>] - <date>`. It says "nothing has been released yet" today, and
that sentence goes with the heading.

## What none of this checks

- **A live Windows machine.** No Windows machine with TouchDesigner on it has
  ever run this code. CI has a `windows-latest` leg, and it does not close the
  gap: a GitHub runner has no TouchDesigner and cannot have one, so the leg
  exercises the unit tests and the linter while everything that discovers an
  installation or shells out to `toeexpand` skips. The install-discovery
  layout, `toeexpand`'s path separators and the clipboard copy (`clip`) stay
  unverified, and the `chmod` narrowing of the token file is known to be
  weaker there. `README.md` says so under Compatibility, and the package
  classifiers deliberately omit Windows.
- **That the release channel exists.** `dist/server.json`'s download URL names
  `…/releases/download/v<version>/<bundle>`, and `publish.sh` step 1 is what
  creates it. Nothing verifies the URL before it is published, because before
  it is published there is nothing there.
- **That the repository is public.** A marketplace install
  (`/plugin marketplace add grigabyte/td-atlas`) and a clone both need that,
  and neither is a gate. It is a setting nobody here can read.
- **The shape of a bridge reply.** `tests/test_protocol_fingerprint.py` holds
  method names and their parameter keys against the current
  `PROTOCOL_VERSION`; the shape of what comes back it cannot see. That half is
  held by the rule in `AGENTS.md`, the review, and the `CHANGELOG.md`
  obligation.
- **Anything about the index.** It is built on the releasing machine from that
  machine's TouchDesigner and holds machine-specific values, so it is
  deliberately not in the bundle and there is nothing about it to check.
