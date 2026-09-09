# As a bundle

`.mcpb` is an MCP Bundle: a zip holding a local MCP server plus a
`manifest.json` describing it, which a supporting client installs when you
open the file. Build one from a clean checkout:

```bash
.venv/bin/python scripts/build_mcpb.py
```

It writes `dist/td-atlas-<version>.mcpb`, regenerates `packaging/manifest.json`
from `pyproject.toml`, and writes `dist/server.json` — the submission for the
official MCP registry, carrying the SHA-256 of the bundle built beside it. The
download URL inside that submission names a GitHub release that does not exist
yet. Publishing is a separate, deliberate step — `scripts/publish.sh`, which
is what creates that release — and until it has been run the bundle is
something you build and open locally, not something to hand out. What that
step checks first, and what it does not, is
written at the top of [`scripts/publish.sh`](../scripts/publish.sh).

Two things the bundle does not carry, and cannot: the index, which is built on
your machine from your installation and holds machine-specific values, and the
bridge, which needs `td-atlas install` and one pasted line. So a bundle install
gives you the project-file tools immediately and tells you which command
unlocks the rest.
