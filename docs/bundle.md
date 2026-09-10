# As a bundle

`.mcpb` is an MCP Bundle, a zip holding a local MCP server plus a
`manifest.json` describing it. A client that supports the format installs it
when you open the file. Build one from a clean checkout:

```bash
.venv/bin/python scripts/build_mcpb.py
```

It writes `dist/td-atlas-<version>.mcpb`, regenerates `packaging/manifest.json`
from `pyproject.toml`, and writes `dist/server.json`. That last file is the
submission for the official MCP registry, and it carries the SHA-256 of the
bundle built beside it.

The download URL inside that submission names a GitHub release that does not
exist yet. `scripts/publish.sh` is what creates the release. Until you run it,
build and open the bundle locally only. What that script checks first, and what it does not check, is written at the top of
[`scripts/publish.sh`](../scripts/publish.sh).

The bundle carries neither the index nor the bridge. The index is built on your
machine from your installation and holds machine-specific values, and the
bridge needs `td-atlas install` and one pasted line. So a bundle install gives
you the project-file tools immediately and tells you which command unlocks the
rest.
