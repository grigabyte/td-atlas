English | [Русский](README.ru.md) | [简体中文](README.zh-CN.md)

<div align="center">
  <p align="center"><img src=".github/cover.png" width="720"
   alt="The td-atlas mark over a field of waveform lines."></p>
  <h1>td-atlas</h1>
  <p>
    An atomised index of TouchDesigner, a live bridge into a running instance,
    and an offline reader for saved projects — exposed to AI agents over MCP.
  </p>
  <p>
    <a href="#install">Install</a> ·
    <a href="#as-an-mcp-server">Use it from an agent</a> ·
    <a href="plugin/skills/touchdesigner/references/tools.md">Tools</a> ·
    <a href="docs/cli.md">Command line</a> ·
    <a href="docs/troubleshooting.md">Troubleshooting</a> ·
    <a href="CHANGELOG.md">Changelog</a>
  </p>
  <p>
    <a href="https://github.com/grigabyte/td-atlas/actions/workflows/ci.yml"><img src="https://github.com/grigabyte/td-atlas/actions/workflows/ci.yml/badge.svg" alt="CI: pytest and ruff on macOS and Windows"></a>
    <img src="https://img.shields.io/badge/python-3.11%2B-blue" alt="Python 3.11 or newer on the host">
    <img src="https://img.shields.io/badge/platform-macOS-lightgrey" alt="Platform: macOS; the Windows code paths run in CI, TouchDesigner on Windows is unverified">
    <a href="LICENSE"><img src="https://img.shields.io/badge/licence-MIT-blue" alt="Licence: MIT"></a>
  </p>
</div>


An agent building in TouchDesigner needs three things at once: exact knowledge
of the operators and parameters on *this* machine, control of a running
instance that can be undone in one step, and a way to read a saved project
without opening it. Miss the first and it guesses parameter names. Miss the
second and a failed step leaves half a network behind. Miss the third and every
question about an existing project needs the application running.

td-atlas is built on two observations:

1. **Almost everything an agent needs to know about TouchDesigner already ships
   inside the application.**
2. **TouchDesigner reports almost nothing when work silently does nothing.**

## What it does

- **[The atom index](docs/atom-index.md)** — two passes produce one SQLite
  index, exact for the build it was made from rather than scraped from a wiki
  describing some other release.
- **[The bridge](docs/bridge.md)** — a Web Server DAT and a callbacks DAT,
  built from a script rather than shipped as a `.tox`, so it is readable,
  diffable and upgraded in place.
- **[What TouchDesigner does not report](docs/health.md)** — `errors` covers
  what TouchDesigner calls an error; `td_health` covers what it does not.
- **[The call journal](docs/journal.md)** — one line per bridge call, written
  by the host, outliving the session.
- **[Reading projects offline](docs/offline-projects.md)** — a project can be
  inspected, searched and compared without TouchDesigner running, and without
  touching the original.
- **[The network text, written on save](docs/network-text.md)** — the bridge
  can write the network out as text beside the `.toe` on every save, so a
  project gets a diffable history in git.

## Install

Three things have to be there first.

- **TouchDesigner.** The index is built from *your* copy of the application and
  holds the values that copy reports, so there is nothing to download.
- **Python 3.11 or newer**, on the host.
- **An AI agent that speaks MCP**, because that is who calls these tools. This
  was built and measured against
  [Claude Code](https://docs.claude.com/en/docs/claude-code/overview) — the
  `claude mcp add` line below is its command — and any client that speaks
  [the Model Context Protocol](https://modelcontextprotocol.io) reaches the
  same tools. You talk to the agent; the agent talks to TouchDesigner.

See [Compatibility](docs/compatibility.md) for platforms and for what the
connector can change in your project.

One line, from a terminal:

```bash
curl -fsSL https://grigabyte.github.io/td-atlas/i | sh
```

`/i` is this repository's `install.sh` under a shorter name: GitHub Pages
republishes the file from `main` on every push that changes it, so there is no
second copy to fall behind. The same bytes come straight out of the repository when Pages is
not answering:

```bash
curl -fsSL https://raw.githubusercontent.com/grigabyte/td-atlas/main/install.sh | sh
```

[`install.sh`](install.sh) finds a Python, clones the repository, makes a
virtualenv beside it, installs the package, builds the index and stages the
bridge — printing every command before it runs it. It asks two questions,
where to clone and whether to build the index now, and takes the default for
both when there is no terminal to ask. td-atlas itself lands in two places,
the checkout you name and `~/.td-atlas`; besides those, `uv` or `pip` fills
its own package cache as it would for any package. No sudo, no system
directory, and your shell startup files are left alone. Run it again on an existing checkout
and it updates that checkout rather than starting over. It is a POSIX shell
script, so Windows takes the sequence below instead.

<details>
<summary>By hand, or on Windows, the same sequence step by step</summary>

```bash
git clone https://github.com/grigabyte/td-atlas
cd td-atlas
uv venv                     # or: python3 -m venv .venv
uv pip install -e .         # or: .venv/bin/pip install -e .
```

There is no package on PyPI, so `pip install td-atlas` and `uvx td-atlas` will
not find anything — the checkout *is* the install. Then, from the checkout:

```bash
.venv/bin/td-atlas build      # offline index, 23–30 s, no TouchDesigner process
.venv/bin/td-atlas install    # stage the bridge, print the bootstrap and MCP lines
```

</details>

Everything below writes `td-atlas` for short. Unless the virtualenv is
activated, call it by path — `.venv/bin/td-atlas`, or
`.venv\Scripts\td-atlas` on Windows — because a system Python will not see
the package.

`td-atlas install` prints two things to paste. First, into TouchDesigner's
textport (Dialogs → Textport and DATs), once per project:

```python
exec(open('/Users/you/.td-atlas/bootstrap.py').read())
```

Second, a `claude mcp add` line for your MCP client — see below. Pass
`--write-mcp-json DIR` to additionally write (or merge into) `DIR/.mcp.json`
with that same entry.

Then, with TouchDesigner open and the bridge staged, complete the index with
the runtime facts only a live instance knows:

```bash
td-atlas probe
```

Then `td-atlas doctor`, which is the only thing here that says whether the
install actually took. It names the repair on every link that is not `ok` and
exits non-zero when one is broken, so it also tells you where you are if you
came in halfway: on a host with nothing built it reports
`index : FAIL … fix: td-atlas build` and
`bridge : warn … fix: td-atlas install`.

## As an MCP server

Run `td-atlas install` and paste the `claude mcp add …` line it prints — it
points at the current interpreter by absolute path, so it keeps working
regardless of the MCP client's own working directory or whether any
virtualenv is activated. Wiring it in by hand looks like:

```bash
claude mcp add td-atlas -- /path/to/python -m td_atlas.cli mcp
```

From there the tools are the agent's and the plain language is yours. Nothing
below is a command to type at a terminal — it is what a person says to the
agent, with the calls it turns into:

| What you say to the agent | What it calls |
| --- | --- |
| *"Which operator displaces an image with noise? Give me the exact parameter names before you build anything."* | `td_search_operators`, then `td_operator_schema` |
| *"Build a noise into a blur into an out TOP in the project I have open, and check nothing is silently dead."* | `td_build` — one undo block — then `td_health` |
| *"It looks like nothing is happening."* | `td_health`, then `td_flags` on whatever it names |
| *"Show me what that looks like right now, and the motion over a second."* | `td_render`, and a contact sheet for the motion |
| *"What is inside `/project1` of `myproject.toe`? TouchDesigner is closed."* | `td_project_read` — the file is copied to a cache and read there |
| *"What did you change since we started?"* | `td_snapshot` before and after, then `td_project_diff` on the two — components in `~/.td-atlas`, never your own file |
| *"Undo that."* | `td_undo` — a whole `td_build` batch is one step |

41 tools in three groups: **9 index** tools that work offline, **23 live**
tools that act on a running instance, **9 project-file** tools that read and
write `.toe`/`.tox` from disk. Every one of them, with its arguments and what
it is for, is listed in
[`plugin/skills/touchdesigner/references/tools.md`](plugin/skills/touchdesigner/references/tools.md)
— one list, held to the code by a test, rather than a second copy here that
would drift.

`td_build` and `td_set_params` validate parameter names against the index
before sending, so the usual mistakes come back as corrections:

```
- t: is a parameter group, not a settable parameter (try: tx, ty, tz)
- typ: no such parameter (try: type, ty)
- type: 'simplex5d' is not a valid menu entry
        (try: simplex4d, simplex3d, simplex2d, sparse, perlin4d)
- period: -3 is below the clamped minimum 0.0
```

## Documentation

| Document | For |
| --- | --- |
| [`plugin/skills/touchdesigner/SKILL.md`](plugin/skills/touchdesigner/SKILL.md) | Agents *using* the connector |
| [`plugin/skills/touchdesigner/references/gotchas.md`](plugin/skills/touchdesigner/references/gotchas.md) | Every trap that produced no error |
| [`plugin/skills/touchdesigner/references/tools.md`](plugin/skills/touchdesigner/references/tools.md) | All 41 MCP tools |
| [`AGENTS.md`](AGENTS.md) | Agents *contributing to* this repository |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | How to run the tests and the linter before a pull request |
| [`CHANGELOG.md`](CHANGELOG.md) | What changed per version, and every protocol change without fail |
| [`docs/architecture.md`](docs/architecture.md) | How the three layers fit together, and why |
| [`docs/cli.md`](docs/cli.md) | Every `td-atlas` subcommand and flag, and what each one needs |
| [`docs/formats.md`](docs/formats.md) | The reverse-engineered `.toe`/`.tox` format, with evidence |
| [`docs/atom-index.md`](docs/atom-index.md) | The two passes that build the index, and what each source yields |
| [`docs/bridge.md`](docs/bridge.md) | The component that runs inside TouchDesigner, and what it adds beyond `exec` |
| [`docs/health.md`](docs/health.md) | What TouchDesigner does not report, and what `td_health` prints instead |
| [`docs/journal.md`](docs/journal.md) | The call journal: what is written, by whom, and what is kept out |
| [`docs/offline-projects.md`](docs/offline-projects.md) | Reading, searching and comparing a `.toe` with TouchDesigner closed |
| [`docs/network-text.md`](docs/network-text.md) | The diffable text written beside the `.toe`, and the seven known differences |
| [`docs/compatibility.md`](docs/compatibility.md) | Builds, Python, operating systems, and what this can change in your project |
| [`docs/skill.md`](docs/skill.md) | The agent skill, and installing it as a plugin |
| [`docs/bundle.md`](docs/bundle.md) | Building the `.mcpb`, and what publishing it would mean |
| [`docs/troubleshooting.md`](docs/troubleshooting.md) | Every symptom, what it is, and what to run |
| [`docs/development.md`](docs/development.md) | The test suite and the invariant it holds |
| [`docs/layout.md`](docs/layout.md) | Every directory in the repository and what lives there |

## Licence

MIT — see [LICENSE](LICENSE). TouchDesigner is a product of Derivative Inc.;
this project is not affiliated with them and redistributes nothing from the
installation, it only reads what is already on your machine.
