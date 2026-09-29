# What TouchDesigner does not report

`errors` covers what TouchDesigner calls an error. `td_health` covers what it
does not:

```
TouchDesigner (TouchDesigner Non-Commercial) — 61/60 fps, 16/51 operators cooking
[WARN ] 9 operator(s) change from frame to frame but nothing displays or
        records them, so they are not running: none cooked in 61 frames, and
        asked to cook again 0.2s after a first ask, each had something new
        to compute. If one should be live, view it, record it, or feed it to
        a Cache TOP with alwayscook on
         /project1/cubes/field, /project1/cubes/comp, /project1/cubes/out
[ERROR] 1 operator(s) had their resolution silently reduced by the licence —
        the output is smaller than asked for
[ERROR] 1 output operator(s) switched off — these produce nothing and report
        no error
         /project1/AV/aout (audio output)
[WARN ] 1 operator(s) cost more than 8 ms per cook (a whole frame at
        60 fps is 16.7 ms)
         /project1/src_b (430 ms)
[note ] Non-Commercial licence: resolution is capped at 1280x1280, Movie File
        Out refuses H.264/H.265 on any GPU, macOS included (ProRes with PCM
        audio records), and the result may not be used in paid work
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
- `bypass-passes-through` (warning) names a bypassed `levelTOP`, `mathTOP`,
  `hsvadjustTOP`, `mathCHOP` or `mathPOP`. Bypass hands the input through at
  full strength, so the layer it was dimming comes back instead of going away.
  Such an operator is named here and not again under `bypassed`.
- `negative-float` (warning with an Add below, note without) names a Level TOP
  in a float pixel format with no clamp and with `contrast` above 1, `inlow`
  above 0 or `outlow` below 0, and says which. It outputs values below zero,
  and an Add TOP or a Composite set to `add` within four operators downstream
  subtracts them. A black level above 0 is not on the list: it cuts to 0 and
  stops there (measured on 2025.32460, 0.2 in and 0.0 out at black level 0.5).
- `nondeterministic` (warning) names each read of `absTime`, `time.time()` or
  an unseeded `random` in a parameter expression or a callback. The frame then
  does not reproduce between runs. When the bridge's time budget ends the
  scan early, `nondeterminism-unscanned` (note) says how many operators'
  parameters were checked, and how many scripts when not all were. Scripts
  are read first, under the same budget.

An operator that did not cook is asked to cook, twice, 0.2 s apart: one that
computes something new at the second ask changes but is pulled by nothing
(`not-pulled`, warning); one that computes nothing is `static` (note), which
is normal for fixed settings, a DAT or a material. Operators that never cooked,
outputs, and those past the half-second cooking budget are not asked, and are
named as `never-cooked`, `quiet-outputs` and `not-cooking-unprobed`. The
probe costs 20-63 ms per call (measured over 12 and 21 quiet operators) plus
the 0.2 s gap, and it cooks what it asks, as a viewer would.

Three more kinds came with the same release:

- `script-errors-stale` (note). TouchDesigner keeps a traceback's text after
  the code is fixed, so each kept one is cleared and read again 0.2 s later.
  Back — `script-errors`, an error. Not back while the operator that runs it
  cooked, and the traceback came from `onCook` — stale, left cleared. Anything
  else — `script-errors` as a warning, and the text is put back.
- `glsl-array-length` (warning when longer, note when shorter) compares a
  uniform array's literal length in the shader with its CHOP's sample count.
  Past the data the elements are undefined: NaN at one, 1.0 at another.
- `history` (note) lists every operator with a reset pulse — Trigger, Speed,
  Count, Lag, Feedback, Trail — whose state a timeline jump does not rebuild.

`td_health` also tells a genuinely dead network from a paused timeline or a
backgrounded window. In those the frame clock is frozen and there is no evidence
either way.
