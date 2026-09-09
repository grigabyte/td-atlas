# Development

```bash
uv pip install -e . pytest
pytest                  # needs no running TouchDesigner
pytest -m live          # the rest: needs one running, with the bridge
uvx ruff check .
td-atlas reload         # re-stage the bridge and reload it through itself
```

The suite is the invariant: **no test fails for want of a running
TouchDesigner.** That is held two different ways, and the difference matters
when you read a summary. Tests that drive a live instance sit behind the `live`
marker, which `pyproject.toml` deselects — a plain run does not collect them,
and says how many it left out rather than counting them as passes. Tests that
need TouchDesigner merely *installed* skip instead, and say so, because the
same checks pass on any machine with the application present. So a plain
`pytest` is green on a machine with neither.

See [AGENTS.md](../AGENTS.md) before changing anything — particularly the code
that runs inside TouchDesigner, which is Python 3.11 with no third-party
imports and a hard rule against blocking.
