# Compatibility

**TouchDesigner.** Everything here was measured against build **2025.32460**.
The index is not a copy of a wiki — it is read out of the application directory
you point at, so another build gives another index; `td-atlas doctor` catches
an index built from a TouchDesigner that has since been moved, updated or
replaced, which nothing else reports. The bridge and the host agree on a
protocol version and refuse each other when they disagree, naming the side that
is behind.

**Python.** 3.11 or newer on the host. The code that runs *inside*
TouchDesigner is held to 3.11 with no third-party imports, because that is the
interpreter the application ships.

**Operating systems.**

| | |
| --- | --- |
| macOS | developed and measured here; every number in this README comes from it |
| Windows | the code paths run in CI (`windows-latest`, Python 3.11-3.14, first run 2026-09-07); **TouchDesigner on Windows is unverified** — no Windows machine with TouchDesigner on it was ever involved |
| Linux | not supported: TouchDesigner is not released for it |

What is still unverified on Windows, precisely: the install-discovery layout
follows Derivative's published install tree rather than measurement;
`toeexpand`'s path separators are inferred from its macOS output; the clipboard
copy (`clip`) has never been run. And two things are now *known* to be weaker
there. `~/.td-atlas` and the token file in it are narrowed with `chmod`, which
on Windows sets only the read-only attribute — measured: the directory reports
mode 0o777 where 0o700 was asked for, so other accounts are kept out by
whatever ACL the user profile already carries and by nothing this project does.
And the call journal cannot report a home that refuses writes, because
`os.access` does not see a refusal there.

CI's `windows-latest` leg (`.github/workflows/ci.yml`) ran for the first time
on 2026-09-07, and that is what turned those from inferences into readings. It
found eleven failures. Four were defects in this code: both of the registry's
liveness probes, the project path written into a bridge's registry record, and
the encoding of the generated bundle manifest. One was a test asserting a
POSIX separator about a Windows filesystem path. The remaining six are checks
whose premise Windows does not have — `os.chmod` there can neither narrow a
mode nor make a path refuse access — and they skip with that reason written
out, in `tests/windows_gaps.py`.

All four are fixed, but the fourth took three more runs and two wrong
diagnoses. A port with nothing on it read as "cannot tell" there, and that was
first blamed on WSA error numbers the POSIX `errno` names did not match —
Windows' `errno` *is* the WSA table, so they matched all along. It was then
blamed on the connection not resolving at all, because the next run printed
10035, CPython's way of saying its own timeout elapsed. Both were guesses about
a duration nobody had measured. Instrumented, the runner answered: a closed
loopback port there refuses in about **two seconds** — eight samples across
the four Python versions, 2002 to 2041 ms — against 0.04 ms on macOS. The
0.25 s the probe allowed was simply short, so it is now 3 s on both systems,
and a bridge that is gone reads as absent on Windows too. What that costs is
about two seconds, once, for each dead record `td-atlas instances` prunes
there; the number and the trade are written on `config.py`'s `PROBE_BUDGET`.

One more reading came out of those runs, on Python 3.11 alone: `time.time()`
on Windows stepped in ~15.6 ms jumps until CPython 3.13, so a test that asked
whether one registry write followed another read both as the same instant. The
timestamp is right — the host is a different process and compares it against
its own wall clock — so the test stopped asking a clock for a resolution it
does not have. Its reasoning is in
`tests/test_instances.py::test_the_record_is_refreshed_by_traffic_but_not_by_every_request`.

What the leg does not close: a GitHub runner has no TouchDesigner and cannot
have one, so everything that discovers an installation or shells out to
`toeexpand` skips. A green Windows column means the Windows code paths run and
their unit tests pass — not that TouchDesigner on Windows has ever been driven
by this code.

**What this can change in your project.** Worth reading before pointing an
agent at work you care about.

- **The bridge is a component inside your open project.** Pasting the bootstrap
  line builds `/tdatlas` in the running project — a Web Server DAT, a callbacks
  DAT and a status panel. Re-running the line upgrades those nodes in place. It
  is a node in your network like any other, and it is saved with the project if
  you save the project.
- **Edits are real edits.** `td_build`, `td_set_params`, `td_set_flags`,
  `td_annotate`, `td_extension_add`, `td_palette_load` and `td_exec` change the
  live network. `td_build` wraps a batch in one `ui.undo` block, so it is a
  single **Ctrl+Z**, and a failed batch rolls itself back; `td_exec` is
  arbitrary Python and carries no such guarantee.
- **Nothing saves your project.** No tool calls `project.save()`. `td_snapshot`
  writes a *component* to `~/.td-atlas/snapshots`, deliberately: saving the
  session would be a Save As and would leave you working inside `~/.td-atlas`
  rather than your own file.
- **Writing text beside the `.toe` is off by default** and needs
  `"text_on_save": true` in `~/.td-atlas/config.json`. A file at that name that
  is not one of ours is never overwritten.
- **Reading a project never touches it.** `toeexpand` and `toecollapse` work in
  place, and `toecollapse` renames the original to `<name>.bkp1` — so every
  offline read copies the file into a cache under `~/.td-atlas/cache` first.
  Writing (`td_project_write`, `td_variant_restore`) refuses an output path
  that already exists rather than replacing it.
- **On the host** td-atlas writes only under `~/.td-atlas`: the index, the
  staged bridge, the call journal `calls.jsonl` (capped at 1 MiB), snapshots,
  variants and the expansion cache. The cache is capped by count and evicted
  least-recently-used; `td-atlas doctor` says how many expansions it holds
  and `td-atlas doctor --clear-cache` empties it.
