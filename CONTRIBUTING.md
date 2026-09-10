# Contributing

The contributor guide is [`AGENTS.md`](AGENTS.md). It holds the conventions,
the rules for the code that runs inside TouchDesigner, and the CLI↔MCP parity
tables a new capability has to appear in. Read it before changing anything.

## Run the tests

```bash
uv pip install -e . pytest
pytest
```

**No test fails for want of a running TouchDesigner.** Tests that need an
installed application skip themselves and say so, so a plain `pytest` is green
on a machine with neither TouchDesigner nor an index.

Tests that need a *live* instance with the bridge installed sit behind the
`live` marker, and a plain run does not collect them:

```bash
pytest -m live          # needs TouchDesigner running with the bridge
```

With nothing listening they skip, and the run stays green.

## Lint

```bash
uvx ruff check .
```

The rule set is narrow, `E4, E7, E9, F, W`. Widening it means changing the
code, not editing a line of config, and the reasoning is in `AGENTS.md`.

## Before opening a pull request

- `pytest` green and `ruff check` clean.
- A new CLI flag or MCP tool is declared in the `AGENTS.md` parity tables.
  `tests/test_cli_mcp_parity.py` fails otherwise.
- Anything that changes `PROTOCOL_VERSION`, or what the connector may change in
  a user's project, gets a `CHANGELOG.md` entry.
- Every number in a comment names how it was measured.

## Reporting a problem

Open an issue with the TouchDesigner build, your OS, and the output of
`td-atlas doctor`. The template asks for exactly that.
