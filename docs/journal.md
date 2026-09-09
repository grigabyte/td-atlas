# The call journal

The status panel inside TouchDesigner holds the *last* call and the next one
overwrites it. That answers "is the bridge alive"; it does not answer "the
agent broke something yesterday, what was it". `~/.td-atlas/calls.jsonl` does:
one line per bridge call, written by the host, outliving the session.

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
most recent refusal — a `cause:` and a `fix:` line; `--summary` also names the
refusal types on a `reasons:` line and lists the five slowest calls.

`td_log` is the same thing for an agent, so it can read its own trail rather
than repeat a call that already refused.

Written by the host and not by the bridge, deliberately. A Table DAT inside
TouchDesigner dies with the process, and keeping it would mean saving the
project — which is a Save As that moves the artist's file. A file written from
inside a frame was measured at 124–200 us, three to five times the whole panel
repaint. On the host the append costs 41.6 us of nobody's frame, next to a
round trip that already cost 16 ms.

Bounded at 1 MiB — about 5,300 calls, measured — with the oldest lines dropped
first. Parameters are not logged: only the method, the outcome, the duration,
the path, the caller and a batch's step count. A DAT's text and a whole
network stay out of it, and the bridge token is scrubbed from every line
before it is written.
