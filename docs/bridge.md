# The bridge

A Web Server DAT and a callbacks DAT, built **from a script rather than shipped
as a `.tox`** — so the bridge is readable, diffable, version-controlled, and
re-running the bootstrap upgrades it in place (or `td-atlas reload`, through
the bridge itself).

Beyond `exec`:

- **`td_health`** — the silent-failure detector. See
  [what TouchDesigner does not report](health.md).
- **`render`** — any TOP's pixels back as PNG, plus contact sheets for anything
  time-based, because a strobe judged from one frame is a coin toss.
- **`batch`** — several operations inside one `ui.undo` block. A failed batch
  rolls back and leaves no partial network. A successful one is a single
  **Ctrl+Z** for the artist.
- **`errors`** — every node reporting an error or warning.

Requests are authenticated with a token by default.
