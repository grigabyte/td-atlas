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

Five more came out of agent sessions in September 2026. Each has its own kind,
and each gets a section in `plugin/skills/touchdesigner/references/gotchas.md`:

- `expensive-stale` (note) sets apart a cook time left over from a cook that
  happened before the check began. A cook time is the length of the *last*
  cook, whenever that was, and a Movie File In last cooked 374,144 frames
  earlier was once blamed for the frame rate. `expensive` keeps only the
  operators that cooked while the check ran, and each time is printed with the
  frame it was measured on. On a paused timeline `absTime.frame` stands
  still, so the split is not drawn and the times are listed as last measured.
- `feedback-loops` (note) lists every feedback operator not held in `reset`.
  `cook(force=True)` does not advance a loop, so a frame made by forced cooks
  carries a stale trail. Play the timeline instead.
- `bypass-passes-through` (note) names a bypassed `levelTOP`, `mathTOP`,
  `hsvadjustTOP`, `mathCHOP` or `mathPOP`. Bypass hands the input through at
  full strength, so the layer it was dimming comes back instead of going away.
  Such an operator is named here and not again under `bypassed`.
- `negative-float` (warning with an Add below, note without) names a Level TOP
  in a float pixel format with a black level above 0 and no clamp. It outputs
  values below zero, and an Add TOP or a Composite set to `add` within four
  operators downstream subtracts them.
- `nondeterministic` (warning) names each read of `absTime`, `time.time()` or
  an unseeded `random` in a parameter expression or a callback. The frame then
  does not reproduce between runs. When the bridge's time budget ends the
  parameter scan early, `nondeterminism-unscanned` (note) says how many
  operators were checked.

`td_health` also tells a genuinely dead network from a paused timeline or a
backgrounded window. In those the frame clock is frozen and there is no evidence
either way.
