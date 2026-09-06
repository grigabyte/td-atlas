# Contributing

The contributor guide is [`AGENTS.md`](AGENTS.md) — conventions, the rules for
the code that runs inside TouchDesigner, and the CLI↔MCP parity tables a new
capability has to appear in. Read it before changing anything. This page is
only the short version of how to run the checks.

## Run the tests

```bash
uv pip install -e . pytest
pytest
```

**No test fails for want of a running TouchDesigner.** Tests that need an
installed application skip themselves and say so, so a plain `pytest` is green
on a machine with neither TouchDesigner nor an index.

Tests that need a *live* instance with the bridge installed sit behind the
`live` marker and are not collected by a plain run:

```bash
pytest -m live          # needs TouchDesigner running with the bridge
```

They skip, rather than fail, when nothing is listening.

## Lint

```bash
uvx ruff check .
```

The rule set is deliberate and narrow (`E4, E7, E9, F, W`); widening it is a
change to the code, not a line in the config. The reasoning is in `AGENTS.md`.

## Before opening a pull request

- `pytest` green and `ruff check` clean.
- A new CLI flag or MCP tool is declared in the `AGENTS.md` parity tables —
  `tests/test_cli_mcp_parity.py` fails otherwise.
- Anything that changes `PROTOCOL_VERSION`, or what the connector may change in
  a user's project, gets a `CHANGELOG.md` entry.
- Measurements, not guesses: a number in a comment names how it was measured.

## Reporting a problem

Open an issue with the TouchDesigner build, your OS, and the output of
`td-atlas doctor`. The template asks for exactly that.
