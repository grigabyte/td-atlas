# Compatibility

**TouchDesigner.** Everything here was measured against build **2025.32460**.
The index is read out of the application directory you point at, so another
build gives another index. Move, update or replace TouchDesigner and you
rebuild with `td-atlas build`; `td-atlas doctor` notices when you have not. The
bridge and the host agree on a protocol version and refuse each other when they
disagree, naming the side that is behind.

**Python.** 3.11 or newer on the host. The code that runs *inside*
TouchDesigner is held to 3.11 with no third-party imports, because that is the
interpreter the application ships.

**Operating systems.**

| | |
| --- | --- |
| macOS | developed and measured here; every number was taken on it |
| Windows | the code paths run in CI (`windows-latest`, Python 3.11-3.14, first run 2026-09-07); **TouchDesigner on Windows is unverified** — no Windows machine with TouchDesigner on it was ever involved |
| Linux | not supported: TouchDesigner is not released for it |

Three things on Windows are still unverified. The install-discovery layout
follows Derivative's published install tree, and nobody has measured it there.
`toeexpand`'s path separators are inferred from its macOS output. The clipboard
copy (`clip`) has never been run.

Two things are now *known* to be weaker there. `~/.td-atlas` and the token file
in it are narrowed with `chmod`, and Windows `chmod` sets only the read-only
attribute. Measured, the directory reports mode 0o777 where 0o700 was asked
for, so other accounts are kept out by whatever ACL the user profile already
carries. And the call journal cannot report a home that refuses writes, because
`os.access` does not see a refusal there.

A closed loopback port on Windows refuses in about **two seconds**, against
0.04 ms on macOS, so the probe now allows 3 s on both systems. That is roughly what each dead record costs when
`td-atlas instances` prunes it. The number and the trade are written on
`config.py`'s `PROBE_BUDGET`.

Checks whose premise Windows does not have skip with that reason written out,
in `tests/windows_gaps.py`. `os.chmod` there can neither narrow a mode nor make
a path refuse access.

A green Windows column in CI (`.github/workflows/ci.yml`) means the Windows
code paths run and their unit tests pass. It says nothing about TouchDesigner
on Windows. A GitHub runner has no TouchDesigner and cannot have one, so
everything that discovers an installation or shells out to `toeexpand` skips
there.

**What this can change in your project.** Worth reading before pointing an
agent at work you care about.

- **The bridge is a component inside your open project.** Pasting the bootstrap
  line builds `/tdatlas` in the running project, as a Web Server DAT, a
  callbacks DAT and a status panel. Re-running the line upgrades those nodes in
  place. It is a node in your network like any other, and it is saved with the
  project if you save the project.
- **Edits are real edits.** `td_build`, `td_set_params`, `td_set_flags`,
  `td_annotate`, `td_extension_add`, `td_palette_load` and `td_exec` change the
  live network. `td_build` wraps a batch in one `ui.undo` block, so it is a
  single **Ctrl+Z**, and a failed batch rolls itself back. `td_exec` is
  arbitrary Python and carries no such guarantee.
- **Nothing saves your project.** No tool calls `project.save()`. `td_snapshot`
  writes a *component* to `~/.td-atlas/snapshots`. Saving the session would be
  a Save As, and it would leave you working inside `~/.td-atlas`.
- **Writing text beside the `.toe` is off by default** and needs
  `"text_on_save": true` in `~/.td-atlas/config.json`. A file at that name that
  is not one of ours is never overwritten.
- **Reading a project never touches it.** `toeexpand` and `toecollapse` work in
  place, and `toecollapse` renames the original to `<name>.bkp1`, so every
  offline read copies the file into a cache under `~/.td-atlas/cache` first.
  Writing (`td_project_write`, `td_variant_restore`) refuses an output path
  that already exists.
- **On the host** td-atlas writes only under `~/.td-atlas`. That is the index,
  the staged bridge, the call journal `calls.jsonl` (capped at 1 MiB),
  snapshots, variants and the expansion cache. The cache is capped by count and
  evicted least-recently-used. `td-atlas doctor` says how many expansions it
  holds and `td-atlas doctor --clear-cache` empties it.
