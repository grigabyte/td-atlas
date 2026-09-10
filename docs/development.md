# Development

```bash
uv pip install -e . pytest
pytest                  # needs no running TouchDesigner
pytest -m live          # the rest: needs one running, with the bridge
uvx ruff check .
td-atlas reload         # re-stage the bridge and reload it through itself
```

One invariant holds the suite together. **No test fails for want of a running
TouchDesigner.**

Tests that drive a live instance sit behind the `live` marker, which
`pyproject.toml` deselects. A plain run does not collect them, and the summary
says how many it left out. Tests that need TouchDesigner merely *installed*
skip, and say so. A plain `pytest` is therefore green on a machine with
neither.

Read [AGENTS.md](../AGENTS.md) before changing anything, and especially before
touching the code that runs inside TouchDesigner. That code is Python 3.11,
with no third-party imports and a hard rule against blocking.
