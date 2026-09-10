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

The file is bounded at 1 MiB, about 5,300 calls measured, and the oldest lines
are dropped first. Parameters are not logged. A line carries the method, the
outcome, the duration, the path, the caller and a batch's step count. A DAT's
text and a whole network stay out of it, and the bridge token is scrubbed from
every line before it is written.
