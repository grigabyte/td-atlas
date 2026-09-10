# What TouchDesigner does not report

`errors` covers what TouchDesigner calls an error. `td_health` covers what it
does not:

```
TouchDesigner (TouchDesigner Non-Commercial) — 61/60 fps, 16/51 operators cooking
[ERROR] 33 operator(s) did not cook once in 91 frames. A branch nothing
        displays or records is never pulled, so it is not running at all
         /project1/AV/fb, /project1/AV/out, /project1/AV/rec
[ERROR] 1 operator(s) had their resolution silently reduced by the licence —
        the output is smaller than asked for
[ERROR] 1 output operator(s) switched off — these produce nothing and report
        no error
         /project1/AV/aout (audio output)
[WARN ] 1 operator(s) cost more than 8 ms per cook (a whole frame at
        60 fps is 16.7 ms)
         /project1/src_b (430 ms)
[note ] Non-Commercial licence: resolution is capped at 1280x1280, realtime
        H.264/H.265 export on Nvidia GPUs is unavailable, and the result may
        not be used in paid work
```

The example is shortened. The real output prints up to six paths under a
finding and then `+N more`, and the clamped-resolution finding names its
operators too.

Every one of those findings was hit while building a real composition, and none
of them raised an error.

`td_health` also tells a genuinely dead network from a paused timeline or a
backgrounded window. In those the frame clock is frozen and there is no evidence
either way.
