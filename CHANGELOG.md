# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Two things get an entry here without fail, because a reader cannot discover
either from the code they are running:

- **A change to the bridge protocol version.** The bridge component runs
  *inside* TouchDesigner and outlives an upgrade of the host package, so a
  protocol change is the one kind of change that requires the reader to do
  something (`td-atlas reload`) rather than just to install.
- **Anything that changes what the connector may touch in the user's project.**

## [Unreleased]

Nothing has been released yet, so everything below is the state of the first
release rather than a change from a previous one.

### Added

- `scripts/publish.sh` gates a release on a clean working tree, a bundle built
  from the current `HEAD`, a free version tag, and a green test run — before
  anything goes outward.
- `README.md` covers installation from a clone, troubleshooting, and what the
  connector may and may not change in a project.
- A `live` pytest marker. `pytest` runs the suite that needs no TouchDesigner;
  `pytest -m live` runs the tests that drive a running instance.
- **`install.sh` is the whole path to a working install in one line** —
  `curl -fsSL https://grigabyte.github.io/td-atlas/i | sh` — on a machine that
  has git and a Python 3.11 or newer. It finds that interpreter by asking each
  one for its `version_info` rather than parsing `--version`, clones or
  fast-forwards the checkout, makes a virtualenv beside it, installs the
  package, builds the offline index and runs `td-atlas install`, then reprints
  the two lines to paste. Every command is printed before it runs. It writes in
  td-atlas's own two places — the directory you choose and `~/.td-atlas`. No
  system directory is touched, nothing is installed with sudo, and no shell
  startup file is edited; besides those two, whichever of `uv` and `pip` does
  the install fills its own package cache — `~/.cache/uv` or
  `~/Library/Caches/pip` — as it would for any package, which is theirs and is
  named here rather than left out of the promise. `uv` is used when it is
  already on `PATH` and never installed, because uv's own installer edits the
  shell rc this script promises to leave alone. Run a
  second time it fast-forwards instead of re-cloning, reuses the virtualenv and
  leaves an existing index alone, since rebuilding it would drop the runtime
  pass only `td-atlas probe` against a live instance can put back.
- The short install address has **no second copy of the script behind it**:
  `.github/workflows/pages.yml` republishes the tracked `install.sh` under the
  name `i` on every push that changes it, so the file served is the file in the
  repository. A stale copy under a friendlier address is worse than a long URL,
  because the person running it cannot see that it is stale. The
  `raw.githubusercontent.com` URL stays in [the README](README.md#install) as
  the second line, for when Pages is not answering.

### Changed

- **The bridge speaks protocol 7, and protocol 7 is now also the minimum the
  host accepts.** A bridge component laid into a project by an older build is
  refused at connection time with the fix in the message, instead of being
  accepted and then answering `UnknownMethod` to every newer call — or, worse,
  answering a different question in silence. Re-lay the component with
  `td-atlas reload`, which is exempt from the check so that the one command
  able to replace an old handler can still reach one. What counts as a
  protocol change is now written down in `AGENTS.md` and held by
  `tests/test_protocol_fingerprint.py`: the method table, each method's
  parameters and the reply shape the host reads are all the wire, and the
  number moves with any of them.
- The Claude Code plugin now lives in `plugin/` rather than at the repository
  root. Installing it brings the TouchDesigner skill and nothing else;
  previously it pulled in every tracked file in the repository.
- Reading a `.toe`/`.tox` unpacks it into a cache that now has a ceiling of 200
  expansions and evicts by least recent use. Before this the cache only ever
  grew — on one machine to 2209 expansions and 4.9 GB. `td-atlas doctor` prints
  its size, and `td-atlas doctor --clear-cache` empties it.
- Refusing to overwrite an existing output file moved out of the MCP tool and
  into the layer both surfaces share, so `td-atlas project write` is now
  covered by it too. It was not, and `toecollapse` moves whatever it finds in
  the way aside to `<file>.bkp1`.
- Answers that had to be cut short now say so. `td_network` reports
  `truncated`, `childrenHidden` and `depthLimited`; `td_errors` and `td_health`
  report how much of the network went unvisited. A partial read used to be
  indistinguishable from a complete one — including a "nothing wrong found"
  that had only looked at a corner of the network.
- **`td_errors` takes a `path` and walks the project by default, not `/`.** It
  used to sweep from the root, where its 5000-node budget is spent on
  TouchDesigner's own `/ui` and `/sys` before the walk reaches the project — so
  on an open session it answered "no operators are reporting errors" about a
  project it had never looked at. The reply now also names the subtree it
  walked, in every branch. `path` is a new argument to an existing bridge
  method rather than a new method, and it is the second reason the protocol
  number is 7: a bridge staged before this change accepts the argument,
  ignores it and walks `/` anyway, and nothing in its reply distinguishes that
  from a correct answer. Such a bridge is refused at connect on its version.
- `td_network` says how many direct children a component at the edge of the
  requested depth has. The walk stops there without recursing and without a marker, so a
  leaf and a component holding thousands of operators came back as the same
  line — `/perform` and `/ui` on an open session. The count was in the reply
  all along and only the printing was missing.
- `td_health` reports its own blind spots: a script-error read that fails is a
  finding in the report, not silence. Its sampling interval is bounded, so a
  large `interval` can no longer block the server process.
- Network walks and the frame capture buffer are bounded (5000 nodes, 512 MiB).
  Every request runs on TouchDesigner's main thread, so an unbounded walk is a
  freeze of the application.
- The CLI and the MCP surface no longer disagree about how many results a
  search or a project-wide grep returns by default: `td-atlas search --limit`
  and `td-atlas project grep --limit` now match `td_search_operators` and
  `td_project_grep`. `td-atlas search` gained `--family`, `td-atlas op` gained
  `--include-hidden` and `td-atlas project grep` gained `--limit`, none of
  which the terminal had. Every remaining difference between the two surfaces
  is written down in `AGENTS.md` and held there by a test.
- **`README.md` closes four holes a first reader fell into.** An MCP-capable
  agent is named in the requirements, where the reader meets it, instead of
  surfacing six sections later as a bare `claude mcp add` line. *As an MCP
  server* now carries seven sentences in the language a person actually uses
  beside the calls each turns into, so "you say, it does" rests on something.
  The skill's two reference files say at their head who the reader is — the
  `td_` names are calls an agent makes, not commands to type. And the one-line
  installer takes the top of *Install*, with the manual clone kept below as the
  same steps by hand. The paragraph that declared three addresses to be 404s
  until the first release is gone with the repository going public: two of them
  now resolve, and the third — the release that `dist/server.json` points at —
  is named where it appears instead.

### Removed

- Three bridge methods no longer exist: `perf`, `par_get` and `save`. Nothing
  on the host called any of them and no test covered them, and `save` called
  `project.save()` — a Save As that moves the artist's working file. Anything
  reaching the bridge directly (this package is not the only thing that can)
  loses them; `health_sample` and `op_info` carry what the first two returned.
  This removal is the first of the two reasons `PROTOCOL_VERSION` is 7: the
  method table is the wire, so which methods exist is part of the protocol
  even though nothing this package's own two halves exchange changed shape.
  Leaving the number at 5 would have let one version name two different method
  sets.
- `docs/decisions.md` and `docs/publishing.md` are no longer in the
  repository. Both were written for a reader who does not exist: the register
  of decisions explains the shape of the code to the people who chose it, and
  the release gates are for whoever runs `scripts/publish.sh`, which is the
  owner alone. Those five gates are written out in that script's own header,
  where the README now sends a reader. Comments in `src/` that cited the
  register by number carry the reason in a few words instead, so nothing in
  the code points at a document a reader cannot open.

### Fixed

- `.mcp.json` is no longer tracked: it carried an absolute path to one
  machine's virtual environment.
- `td_log` and `td-atlas log` no longer report "No calls recorded yet" when
  the journal is failing to be written; the reason is printed under the
  report. A write error used to be swallowed whole.
- The bridge token is scrubbed from journal lines using the *current* token.
  The cache holding it never expired, so a token rotated by `td-atlas install`
  went into the log in clear while the previous one was removed.
