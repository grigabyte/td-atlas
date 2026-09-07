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

### Changed

- **The bridge speaks protocol 6, and protocol 6 is now also the minimum the
  host accepts.** A bridge component laid into a project by an older build is
  refused at connection time with the fix in the message, instead of being
  accepted and then answering `UnknownMethod` to every newer call. Re-lay the
  component with `td-atlas reload`, which is exempt from the check so that the
  one command able to replace an old handler can still reach one.
- The Claude Code plugin now lives in `plugin/` rather than at the repository
  root. Installing it brings the TouchDesigner skill and nothing else;
  previously it pulled in every tracked file in the repository.
- Reading a `.toe`/`.tox` unpacks it into a cache that now has a ceiling of 200
  expansions and evicts by least recent use. Before this the cache only ever
  grew — on one machine to 2209 expansions and 4.9 GB. `td-atlas doctor` prints
  its size, and `td-atlas doctor --clear-cache` empties it.
- Refusing to overwrite an existing output file moved out of the MCP tool and
  into the layer both surfaces share, so `td-atlas project write` is now
  covered by it too. It was not, and `toecollapse` renames whatever it finds in
  the way to `.bkp`.
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
  method rather than a new method, so a bridge staged before this change still
  reports protocol 6 and still walks `/`; the host detects that by the absent
  field and says so instead of trusting the answer.
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

### Removed

- Three bridge methods no longer exist: `perf`, `par_get` and `save`. Nothing
  on the host called any of them and no test covered them, and `save` called
  `project.save()` — a Save As that moves the artist's working file. Anything
  reaching the bridge directly (this package is not the only thing that can)
  loses them; `health_sample` and `op_info` carry what the first two returned.
  This removal is why `PROTOCOL_VERSION` is 6: the method table is the wire,
  so which methods exist is part of the protocol even though nothing this
  package's own two halves exchange changed shape. Leaving the number at 5
  would have let one version name two different method sets.

### Fixed

- `.mcp.json` is no longer tracked: it carried an absolute path to one
  machine's virtual environment.
- `td_log` and `td-atlas log` no longer report "No calls recorded yet" when
  the journal is failing to be written; the reason is printed under the
  report. A write error used to be swallowed whole.
- The bridge token is scrubbed from journal lines using the *current* token.
  The cache holding it never expired, so a token rotated by `td-atlas install`
  went into the log in clear while the previous one was removed.
