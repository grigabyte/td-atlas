# The bridge

Four nodes, built in place by a script: a Web Server DAT, a callbacks DAT, a
status panel and an Execute DAT that fires on `projectpostsave`. The bridge is
readable, it lives in version control as source, and its edits show up in a
diff. Re-running the bootstrap upgrades it where it stands, and so does
`td-atlas reload` through the bridge itself.

Beyond `exec`:

- **`td_health`**, the silent-failure detector. See
  [what TouchDesigner does not report](health.md).
- **`render`** returns any TOP's pixels as PNG, plus contact sheets for anything
  time-based. One frame of a strobe is a coin toss.
- **`batch`** runs several operations inside one `ui.undo` block. A failed batch
  rolls back and leaves no partial network. A successful one is a single
  **Ctrl+Z** for the artist.
- **`errors`** lists every node reporting an error or warning.
- **`timeline_run`** walks the timeline frame by frame inside TouchDesigner and
  saves a TOP's frames, as a job that `timeline_status` polls and
  `timeline_cancel` stops. **`timeline_profile`** is the same kind of job,
  timing what each operator costs per frame.
- **`trace`** walks up a TOP's inputs and reads each image, so the host can
  name where the picture was lost.

Requests are authenticated with a token by default.
