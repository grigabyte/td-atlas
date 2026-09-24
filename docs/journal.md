# The call journal

Every bridge call lands in `~/.td-atlas/calls.jsonl`, one line each. The file
outlives the session, so yesterday's failures are still there today.

```
$ td-atlas log --failures
08-29 22:23:51  FAIL  op_info             16.6ms  /project1/does_not_exist
        LookupError: no operator at path '/project1/does_not_exist'
08-29 22:23:51  FAIL  par_set             16.5ms  /tdatlas/jrn_noise
        AttributeError: /tdatlas/jrn_noise (noiseTOP) has no parameter 'nosuchpar'

$ td-atlas log --summary
11 calls, 5 failed (45%)   08-29 22:23 to 08-29 22:23

where it fails
  op_create          1 of 2
  par_set            1 of 2
  exec               1 of 1
```

Both samples are cut short. `--failures` ends with the mapped repair for the
most recent refusal, a `cause:` line and a `fix:` line. `--summary` also names
the refusal types on a `reasons:` line and lists the five slowest calls.

`td_log` gives an agent the same trail, so it can read what already refused
before it calls again.

The status panel inside TouchDesigner holds the *last* call and the next one
overwrites it. It answers "is the bridge alive". The journal answers "the agent
broke something yesterday, what was it".

The host writes the journal. A Table DAT inside TouchDesigner dies with the
process, and keeping one would mean saving the project, which is a Save As that
moves the artist's file.

A line carries the method, the outcome, the duration, the path, the caller and
a batch's step count. A call that changed the project also carries what it
changed, and the listing prints it under the call: `tx = 0.5 (was 0.2)`, `ty =
expr absTime.seconds (reads 12.25)`, `viewer = False (was True)`, every step of
a batch, and the first lines of the code an `exec` ran. So "what did I set
yesterday at 19:00" is `td-atlas log --method par_set -n 500`, or `batch`, or
`exec`, read rather than reconstructed. The old value comes from the bridge,
which reads each parameter in the request that writes it: the constant, the
expression or the bind expression it held, with its mode. `flags_set` reads
each flag the same way. A bridge from before that read sends no old value, and
then the journal records none: the old value is the earlier line that set it.
A timeline job
keeps the walk it was sent: the frames, the ones saved, the output template,
the tiles and the Render TOPs it crops; a profile keeps its frames, its
limit and its settle; a cancel keeps which job it stopped.

Calls that only read keep their path and nothing else, so a whole network
never lands in the file. The `ping`s a settle (`settle_frames`) polls with, one
a frame until TouchDesigner has drawn, are not written at all; a poll that
fails is. Code and DAT text are clipped at 4 KB and other
values at 1,000 characters, with the full length noted; a batch keeps its
first 64 steps and counts the rest; one line never passes 32 KB. The file is
bounded at 16 MiB and the oldest lines are dropped first. The bridge token is
scrubbed from every line before it is written, code included. Other secrets
are hidden by pattern: a quoted value assigned or passed to a name such as
`password`, `secret`, `token`, `api_key`, `access_key` or `auth`, and keys
that announce themselves (`sk-…`, `ghp_…`, `xoxb-…`, `AKIA…`), become
`[redacted]`. A pattern is a net, not a guarantee: a key in no known format
under a name that says nothing is written as sent.
