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

td-atlas gives an agent three things. The exact operator and parameter names
from *this* machine. Hands inside a running instance, with one step of undo.
And a way to read a saved project without opening it.

## What it does

- **[The atom index](docs/atom-index.md)** builds one SQLite index in two
  passes, from your own copy of the application. The values in it are the
  values that copy reports.
- **[The bridge](docs/bridge.md)** is a Web Server DAT and a callbacks DAT
  that a script builds in your project. You can read it, see its changes in
  git, and upgrade it in place.
- **[What TouchDesigner does not report](docs/health.md)**. `errors` covers
  what TouchDesigner itself calls an error, and `td_health` covers the
  breakage it stays quiet about.
- **[The call journal](docs/journal.md)** writes one line per bridge call, on
  the host, and it outlives the session.
- **[Reading projects offline](docs/offline-projects.md)** inspects, searches
  and compares a project with TouchDesigner closed, and the original file is
  left alone.
- **[The network text, written on save](docs/network-text.md)** is what the
  bridge can put beside the `.toe` on every save, so a project gets a diffable
  history in git.

## Install

Three things have to be there first.

- **TouchDesigner**, already installed. The [index](docs/atom-index.md) is
  built from *your* copy of the application and holds the values that copy
  reports. There is nothing to download.
- **Python 3.11 or newer**, on the host.
- **An AI agent that speaks [MCP](https://modelcontextprotocol.io)**. The
  agent is what calls these tools. td-atlas was built and measured against
  [Claude Code](https://docs.claude.com/en/docs/claude-code/overview), whose
  command is the `claude mcp add` line further down. Any MCP client reaches
  the same tools.

[Compatibility](docs/compatibility.md) lists the platforms and what the
connector can change in your project.

One line, from a terminal:

```bash
curl -fsSL https://grigabyte.github.io/td-atlas/i | sh
```

If that address does not answer, the same script comes out of the repository:

```bash
curl -fsSL https://raw.githubusercontent.com/grigabyte/td-atlas/main/install.sh | sh
```

[`install.sh`](install.sh) finds a Python, clones the repository, makes a
virtualenv beside it, installs the package, builds the index and stages the
[bridge](docs/bridge.md). Every command is printed before it runs.

It asks two questions: where to clone, and whether to build the index now.
With no terminal to ask, it takes the default for both.

Two directories end up on disk: the checkout you name and `~/.td-atlas`.
Besides those, `uv` or `pip` fills its own package cache, as it does for any
package. No sudo. System directories and your shell startup files are left
alone. Run it again on an existing checkout and it updates that checkout.

It is a POSIX shell script. Windows takes the sequence below.

<details>
<summary>By hand, or on Windows, the same sequence step by step</summary>

```bash
git clone https://github.com/grigabyte/td-atlas
cd td-atlas
uv venv                     # or: python3 -m venv .venv
uv pip install -e .         # or: .venv/bin/pip install -e .
```

There is no package on PyPI. `pip install td-atlas` and `uvx td-atlas` will
find nothing. Then, from the checkout:

```bash
.venv/bin/td-atlas build      # offline index, 23–30 s, no TouchDesigner process
.venv/bin/td-atlas install    # stage the bridge, print the bootstrap and MCP lines
```

</details>

Commands from here on are written `td-atlas` for short. Unless the virtualenv is
activated, call it by path. That is `.venv/bin/td-atlas`, or
`.venv\Scripts\td-atlas` on Windows. A system Python does not see the package.

`td-atlas install` prints two things to paste. First, into TouchDesigner's
textport (Dialogs → Textport and DATs), once per project:

```python
exec(open('/Users/you/.td-atlas/bootstrap.py').read())
```

Second, a `claude mcp add` line for your MCP client. See
[As an MCP server](#as-an-mcp-server). To also write that same entry into
`DIR/.mcp.json`, or merge it into one that is already there, pass
`--write-mcp-json DIR`.

With TouchDesigner open and the bridge staged, finish the index:

```bash
td-atlas probe
```

This pass adds the facts only a running instance knows.

Last, check the install. If you came in halfway, start here.

```bash
td-atlas doctor
```

Every link that is not `ok` comes with its repair, and a broken one makes the
command exit non-zero. On a host with nothing built it reports
`index : FAIL … fix: td-atlas build` and `bridge : warn … fix: td-atlas install`.

## As an MCP server

Run `td-atlas install` and paste the `claude mcp add …` line it prints. That
line names the interpreter by absolute path, so it keeps working from any
working directory, with or without an activated virtualenv. Wiring it in by
hand looks like this:

```bash
claude mcp add td-atlas -- /path/to/python -m td_atlas.cli mcp
```

From here you say what you want in plain language. Here is what the agent calls
on it:

| What you say to the agent | What it calls |
| --- | --- |
| *"Which operator displaces an image with noise? Give me the exact parameter names before you build anything."* | `td_search_operators`, then `td_operator_schema` |
| *"Build a noise into a blur into an out TOP in the project I have open, and check nothing is silently dead."* | `td_build` — one undo block — then `td_health` |
| *"It looks like nothing is happening."* | `td_health`, then `td_flags` on whatever it names |
| *"Show me what that looks like right now, and the motion over a second."* | `td_render`, and a contact sheet for the motion |
| *"What is inside `/project1` of `myproject.toe`? TouchDesigner is closed."* | `td_project_read` — the file is copied to a cache and read there |
| *"What did you change since we started?"* | `td_snapshot` before and after, then `td_project_diff` on the two — components in `~/.td-atlas`, never your own file |
| *"Undo that."* | `td_undo` — a whole `td_build` batch is one step |

41 tools in three groups. **9 index** tools work offline, **23 live** tools act
on a running instance, and **9 project-file** tools read and write
`.toe`/`.tox` from disk. Each one, with its arguments and what it is for, is in
[`plugin/skills/touchdesigner/references/tools.md`](plugin/skills/touchdesigner/references/tools.md),
and a test holds that list to the code.

`td_build` and `td_set_params` check parameter names against the index before
sending, so the usual mistakes come back as corrections:

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

MIT, and the full text is in [LICENSE](LICENSE). TouchDesigner is a product of Derivative Inc.
This project is not affiliated with them and redistributes nothing from the
installation. It only reads what is already on your machine.
