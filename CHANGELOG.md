# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Two things get an entry here without fail, because a reader cannot discover
either from the code they are running:

- **A change to the bridge protocol version.** The bridge component runs
  *inside* TouchDesigner and outlives an upgrade of the host package, so a
  protocol change is the one kind of change that asks the reader to run
  `td-atlas reload`.
- **Anything that changes what the connector may touch in the user's project.**

## [Unreleased]

### Changed

- **The bridge speaks protocol 8, and protocol 8 is now also the minimum the
  host accepts.** Run `td-atlas reload` once in every project that holds the
  bridge, and restart the MCP server, which in practice means restarting the
  client that launched it. Host and bridge are checked against each other
  only at connect, by the numbers each loaded when it started: a server
  started before the upgrade refuses the reloaded bridge as newer than it
  expects, and the new server refuses every bridge that was not reloaded. A bridge
  laid down by an earlier build is refused at connect with that fix in the
  message. Five methods joined the table (`timeline_run`,
  `timeline_status`, `timeline_cancel`, `timeline_profile`, `trace`), and six
  replies gained fields an older bridge does not send: `ping` (`absFrame`,
  `tick` and a `timeline` block), the error reply of `exec` (`stdout`,
  `stderr`, `result`), `par_set` (`before`), `health_sample`
  (`cookAbsFrame`, `cookFrame`, `feedback`, `negativeFloat`, `clockReads`,
  `clockReadsCount`, `clockScan` with its `scripts` count), and `network` and
  `op_info` (`viewer` on a TOP).
- **The call journal records what a changing call changed, not only that it
  ran.** Parameter writes, builds, creations, deletions, wiring, flags,
  palette loads, extensions, annotations, saved components, the code
  `td_exec` ran and the walk a timeline job was sent now leave their values
  on the journal line in
  `~/.td-atlas`, so "put it back the way it was yesterday" can be answered
  from the log. A parameter write keeps the value it replaced, which the
  bridge reads in the same request before writing, and `td_log` prints it
  as `tx = 0.5 (was 0.2)`. Every line is
  clipped (code and DAT text at 4 KB, 32 KB a line), the token is scrubbed as
  before, and other secrets are hidden by pattern: a quoted value given to a
  name such as `password`, `secret`, `token` or `api_key`, and keys that
  announce themselves (`sk-…`, `ghp_…`, `xoxb-…`, `AKIA…`), become
  `[redacted]`. The file cap rises from 1 MiB to 16 MiB. `td_log` and
  `td-atlas log` print the change under each call. Calls that only read are
  logged as before, and the frame polls `settle_frames` makes are not
  logged at all.
- `td_status` and `td-atlas status` name the timeline's own frame, whether it
  is playing, its start and end, its playback range and rate, apart from the
  application clock. The status used to print the application's frame under
  the word "frame". A playback range narrower than the timeline is marked,
  since a recording stops at `rangeEnd` without a word.
- A bridge timeout reads the TouchDesigner process before blaming a script.
  One that sits idle, asleep, minimised or behind a dialog is reported as
  such, with the advice to bring it to the front. Every timeout used to say a
  long script was blocking the main thread. On Windows the old message
  stands.
- A script that raises in `td_exec` still returns what it printed, and its
  `result`, ahead of the error. `td-atlas exec` writes them where they would
  have gone. The reply is still a failure.
- `td_health` finds five more quiet failures. A cook time is named by the
  frame it was measured on, so one left from long before the check is no
  longer blamed for the current frame rate. Feedback loops, which
  `cook(force=True)` does not advance, are listed. So are bypassed gain
  operators (Level, Math, HSV Adjust), which pass their layer through at full
  strength rather than switching it off, and Level TOPs that can put
  negative floats into an Add. Reads of `absTime` or an unseeded random
  generator, which keep a render from reproducing, are named with where they
  are; script text and parameter expressions share one time budget, and the
  finding says how much of each it read. A bypassed gain operator is listed
  once, under its own note, and no longer also among the plain bypassed
  operators.
- `td_network` marks a TOP whose viewer flag is off, so an empty tile is not
  taken for a broken scene.
- `td-atlas install` prints the MCP line for every directory (`-s user`)
  beside the one for the current directory. It runs neither.
- `td-atlas doctor` says how far over its ceiling the expansion cache is and
  that it is draining, instead of printing the count beside the limit as if
  the limit did not hold.

### Added

- **Timeline jobs: `td_timeline_run`, `td_timeline_status` and
  `td_timeline_cancel`.** A job walks the timeline frame by frame inside
  TouchDesigner and saves a TOP's frames, for anything that depends on
  history (live audio, Feedback TOPs, trails), where a `td_exec` loop hits
  the 30 s limit. It returns a job id at once. Without an output it only
  advances the timeline and holds it paused where it stopped. What it
  touches in the project: it pauses the timeline and gives the play mode
  back, and with `tiles` it crops the named Render TOPs and puts the crop back
  to 0..1 however the job ends. The files it writes stay. MCP only, declared
  in `AGENTS.md`.
- `td_render` takes `save_to`, which writes the PNG to that path and answers
  in text, and `settle_frames` (`--settle-frames` on the CLI), which waits
  that many TouchDesigner frames first. A render right after an edit could
  show the frame before it. The frames are counted on
  `op.TDResources.time.frame`, which keeps counting while the timeline is
  paused; `absTime.frame` stands still then, and a wait on it reported a
  drawing TouchDesigner as stalled. `save_to` refuses a file that already
  exists unless `overwrite=True`, like every other write td-atlas makes;
  `render -o` on the CLI overwrites as a shell redirect does.
- The index records the tuple members a size menu adds (`amp2` on a
  noisePOP, with the menu value that shows it), after `td-atlas probe`. The
  validator refuses a member the size in force does not show, and a size
  menu the probe cannot step is reported.
- **`td_trace`: why the output is black.** It walks up a TOP's inputs,
  reads each image's minimum, mean and maximum without forcing a cook, and
  marks where the picture dropped, went negative, lost its alpha or turned
  NaN, with the cause the parameters show (bypass, opacity 0, a black level,
  an empty input, a Level TOP in float with no clamp and contrast above 1,
  inlow above 0 or outlow below 0). It stops at
  40 operators and half a second of reading. MCP only.
- **`td_timeline_profile`: who spends the frame.** A job in the same slot as
  `td_timeline_run` walks real timeline frames and, on each, cooks every
  TOP, CHOP, SOP and POP under a path upstream first and times it with the
  GPU waited for — a `td_exec` loop of `cook(force=True)` shows about 0 ms
  for a TOP that costs 80, since the cook only queues GPU work.
  `td_timeline_status` shows mean and maximum per operator, costliest first,
  and marks the ones that did not cook on their own. Forced cooks run side
  effects (scripts, file and network outputs) once more; the tool says so.
- `td_snapshot` ends its reply with the next step of a before-and-after
  comparison of a branch, and says when a label it reused replaced an
  earlier snapshot. The skill gives the whole recipe: two snapshots of the
  branch, `td_project_diff` between them, and the roll-back.
- The skill notes sequence parameters, the Execute DAT's callbacks,
  one-shot Movie File Out, deferred runs on a paused timeline, bypassed
  Level TOPs and the undoable route through `td_exec` for an edit `td_build`
  cannot express, next to the traps the new `td_health` findings describe.

### Fixed

- `td_build` refuses a step whose keys the bridge would ignore, and names
  the right one. `op_connect` with `input_index` used to wire input 0.
- A StrMenu takes free text; its entries are suggestions. A plain Menu stays
  closed.
- An operator-reference parameter written as `../name` is refused only when
  the path TouchDesigner will look it up at is missing, and the refusal
  gives the sibling and absolute spellings.
- `td_set_params` checks names and `../` operator references even without
  `op_type`: it asks the bridge for the node's type in the same call it
  already makes for references.
- The test suite stays out of the real `~/.td-atlas`. It had been appending
  a journal line there.

## [0.1.0] - 2026-09-15

The first release. Everything below is what it contains.

Measured against TouchDesigner 2025.32460 on macOS. The Windows code paths run
in CI; TouchDesigner on Windows is unverified. See `docs/compatibility.md`.

### Added

- `scripts/publish.sh` gates a release on a clean working tree, a bundle built
  from the current `HEAD`, a free version tag, and a green test run. Each gate
  runs before anything goes outward.
- `README.md` covers installation from a clone, troubleshooting, and what the
  connector may and may not change in a project.
- A `live` pytest marker. `pytest` runs the suite that needs no TouchDesigner;
  `pytest -m live` runs the tests that drive a running instance.
- **`install.sh` is the whole path to a working install in one line.** Run
  `curl -fsSL https://grigabyte.github.io/td-atlas/i | sh` on a machine that
  has git and a Python 3.11 or newer. It finds that interpreter by asking each
  one for its `version_info`, clones or fast-forwards the checkout, makes a
  virtualenv beside it, installs the package, builds the offline index and runs
  `td-atlas install`, then reprints the two lines to paste. Every command is
  printed before it runs. It writes in td-atlas's own two places, the directory
  you choose and `~/.td-atlas`. No system directory is touched, nothing is
  installed with sudo, and no shell startup file is edited. Whichever of `uv`
  and `pip` does the install also fills its own package cache, `~/.cache/uv` or
  `~/Library/Caches/pip`, as it would for any package. `uv` is used when it is
  already on `PATH` and never installed, because uv's own installer edits the
  shell rc this script promises to leave alone. Run a second time it
  fast-forwards the existing checkout, reuses the virtualenv and leaves an
  existing index alone, since rebuilding it would drop the runtime pass only
  `td-atlas probe` against a live instance can put back.
- The short install address serves the tracked script itself.
  `.github/workflows/pages.yml` republishes `install.sh` under the name `i` on
  every push that changes it, so the file served is the file in the
  repository. A stale copy under a friendlier address is worse than a long URL,
  because the person running it cannot see that it is stale. The
  `raw.githubusercontent.com` URL stays in [the README](README.md#install) as
  the second line, for when Pages is not answering.

### Changed

- **The bridge speaks protocol 7, and protocol 7 is now also the minimum the
  host accepts.** A bridge component laid into a project by an older build is
  refused at connection time with the fix in the message. Such a build used to
  be accepted, and then answered `UnknownMethod` to every newer call, or
  answered a different question in silence. Re-lay the component with
  `td-atlas reload`, which is exempt from the check so that the one command
  able to replace an old handler can still reach one. What counts as a
  protocol change is now written down in `AGENTS.md` and held by
  `tests/test_protocol_fingerprint.py`. The method table, each method's
  parameters and the reply shape the host reads are all the wire, and the
  number moves with any of them.
- The Claude Code plugin moved from the repository root to `plugin/`.
  Installing it brings the TouchDesigner skill and nothing else;
  previously it pulled in every tracked file in the repository.
- Reading a `.toe`/`.tox` unpacks it into a cache that now has a ceiling of 200
  expansions and evicts by least recent use. Before this the cache only ever
  grew, on one machine to 2209 expansions and 4.9 GB. `td-atlas doctor` prints
  its size, and `td-atlas doctor --clear-cache` empties it.
- Refusing to overwrite an existing output file moved out of the MCP tool and
  into the layer both surfaces share, so `td-atlas project write` is now
  covered by it too. It was not, and `toecollapse` moves whatever it finds in
  the way aside to `<file>.bkp1`.
- Answers that had to be cut short now say so. `td_network` reports
  `truncated`, `childrenHidden` and `depthLimited`; `td_errors` and `td_health`
  report how much of the network went unvisited. A partial read used to be
  indistinguishable from a complete one, including a "nothing wrong found"
  that had only looked at a corner of the network.
- **`td_errors` takes a `path` and walks the project by default.** It used to
  sweep from `/`, where its 5000-node budget is spent on TouchDesigner's own
  `/ui` and `/sys` before the walk reaches the project. On an open session it
  answered "no operators are reporting errors" about a project it had never
  looked at. The reply now also names the subtree it walked, in every branch.
  `path` is a new argument on an existing bridge method, and it is the second
  reason the protocol number is 7. A bridge staged before this change accepts
  the argument, ignores it and walks `/` anyway, and nothing in its reply
  distinguishes that from a correct answer. Such a bridge is refused at connect on its version.
- `td_network` says how many direct children a component at the edge of the
  requested depth has. The walk stops there without recursing and without a marker, so a
  leaf and a component holding thousands of operators came back as the same
  line. On an open session `/perform` and `/ui` read alike. The count was in the reply
  all along and only the printing was missing.
- `td_health` reports its own blind spots. A script-error read that fails is a
  finding in the report. Its sampling interval is bounded, so a
  large `interval` can no longer block the server process.
- Network walks and the frame capture buffer are bounded (5000 nodes, 512 MiB).
  Every request runs on TouchDesigner's main thread, so an unbounded walk is a
  freeze of the application.
- The CLI and the MCP surface now return the same number of results by
  default. `td-atlas search --limit` and `td-atlas project grep --limit` match
  `td_search_operators` and `td_project_grep`. `td-atlas search` gained `--family`, `td-atlas op` gained
  `--include-hidden` and `td-atlas project grep` gained `--limit`, none of
  which the terminal had. Every remaining difference between the two surfaces
  is written down in `AGENTS.md` and held there by a test.
- **`README.md` closes four holes a first reader fell into.** An MCP-capable
  agent is named in the requirements, where the reader meets it; it used to
  surface six sections later as a bare `claude mcp add` line. *As an MCP
  server* now carries seven sentences in the language a person actually uses
  beside the calls each turns into, so "you say, it does" rests on something.
  The skill's two reference files say at their head who the reader is, since
  the `td_` names are calls an agent makes. And the one-line installer takes
  the top of *Install*, with the manual clone kept below as the same steps by
  hand. The paragraph that declared three addresses to be 404s until the first
  release is gone with the repository going public. Two of them now resolve,
  and the third, the release that `dist/server.json` points at, is named where
  it appears.

### Removed

- Three bridge methods are gone. `perf`, `par_get` and `save` had no caller on
  the host and no test covering them, and `save` called `project.save()`, a
  Save As that moves the artist's working file. Anything
  reaching the bridge directly (this package is not the only thing that can)
  loses them; `health_sample` and `op_info` carry what the first two returned.
  This removal is the first of the two reasons `PROTOCOL_VERSION` is 7. The
  method table is the wire, so which methods exist is part of the protocol
  even though nothing this package's own two halves exchange changed shape.
  Leaving the number at 5 would have let one version name two different method
  sets.
- `docs/decisions.md` and `docs/publishing.md` are no longer in the
  repository. Both were written for a reader who does not exist. The register
  of decisions explains the shape of the code to the people who chose it, and
  the release gates are for whoever runs `scripts/publish.sh`, which is the
  owner alone. Those five gates are written out in that script's own header,
  where the README now sends a reader. Comments in `src/` that cited the
  register by number carry the reason in a few words instead, so nothing in
  the code points at a document a reader cannot open.

### Fixed

- `.mcp.json` is no longer tracked. It carried an absolute path to one
  machine's virtual environment.
- `td_log` and `td-atlas log` no longer report "No calls recorded yet" when
  the journal is failing to be written; the reason is printed under the
  report. A write error used to be swallowed whole.
- The bridge token is scrubbed from journal lines using the *current* token.
  The cache holding it never expired, so a token rotated by `td-atlas install`
  went into the log in clear while the previous one was removed.

[Unreleased]: https://github.com/grigabyte/td-atlas/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/grigabyte/td-atlas/releases/tag/v0.1.0
