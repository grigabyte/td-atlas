# Gotchas

Every entry here was hit while building a real composition. Each one produced
no error, no warning, and no visible sign that anything was wrong.

## A branch nothing consumes never runs

TouchDesigner cooks on demand. An operator is only pulled if something needs
it: a viewer, a render chain, an output device, an export. A network you build
programmatically has no viewer on it, so **it does not run at all**.

Symptom: renders work (they force a cook), but feedback trails never
accumulate, animation is frozen between calls, and a Movie File Out writes
nothing while reporting no error.

Diagnosis: `td_health` — it compares each operator's cook count against the
frame clock and names the dormant ones. A freshly built `noise → blur` pair
reports `0/2 operators cooking`.

Fix: put a `cacheTOP` with `alwayscook` on at the end of the chain, pulling
whatever must stay live. `nullTOP` has no such parameter; `cacheTOP`,
`touchoutTOP` and `webrenderTOP` do, as do many CHOPs under the name
`cookalways` — `td_search_parameters("cook every frame")` lists them.

```json
{"method": "op_create", "params": {
   "parent": "/project1/AV", "type": "cacheTOP", "name": "keepalive",
   "pars": {"alwayscook": true, "cachesize": 1},
   "connect": [{"from": "/project1/AV/out", "index": 0}]}}
```

Adding that turns the same network into `3/3 operators cooking`.

A recorder must be *inside* what the keep-alive pulls, not beside it:
`out → rec → keepalive`, not `out → keepalive` with `rec` hanging off `out`.

## A Python extension attached the wiki's way is silently absent

The offline wiki's `Extensions` page shows the extension expression written as
`MyClass(me)`. On this build that leaves `comp.extensions[0]` equal to `None`
— and `errors()` is empty, `warnings()` is empty, and `extensionsReady`
reports `True`. Nothing anywhere says the extension is not there; every call
into it fails later as a plain attribute error on the COMP.

The form that works, and the one TouchDesigner's own components use:

```
op('./<DAT>').module.<Class>(me)
```

Use `td_extension_add`, which writes that form, parses the code first, and
reads the instance back — a class that fails to instantiate comes back as the
real exception instead of as nothing at all.

## A GLSL shader that does not compile is a *warning*

A GLSL TOP whose shader fails to compile outputs a checkerboard or black and
reports through `warnings()`, not `errors()` — and the warning text is only
`The GLSL Shader has compile errors (Use Info DAT to see details)`. It names
no line, no file, and anything scanning for errors misses it entirely.

The compiler's own output is a property, `compileResult`, on `glslTOP`,
`glslmultiTOP` and `glslMAT` (`glslPOP` has no documented equivalent).
`td_health` reads it and prints the failing line and source DAT directly.

## Background throttling

TouchDesigner all but stops rendering when its window is not visible. Cook
counts across the whole application drop to a few per second, including the
default project's own output. Audio keeps running.

This is easy to mistake for a broken network. Bring the window to the front
before measuring performance or recording in realtime.

## CPU operators hiding in a menu

Some operator options run on the CPU and cost orders of magnitude more. The
Noise TOP is the trap: its `type` menu labels distinguish "Simplex 3D (GPU)"
from plain "Sparse", "Hermite", "Harmomic Summation" (TouchDesigner's own
spelling; the value is `harmonic`), "Random" and "Alligator", which are
CPU-side.

Measured on 2025.32460 at 1280×720, Apple Silicon, `td_health` over a 2 s
interval, repeated: `sparse` 96 ms per cook, `alligator` 104 ms, `harmonic`
104 ms, `hermite` 38 ms; `simplex3d` and `perlin3d` never crossed the 8 ms
threshold at all. Whether that shows up as dropped frames depends on the rest
of the network — do not wait for the frame rate to tell you.

Nothing warns you. `td_health` flags any operator costing more than 8 ms.

The operator summary says so in prose too — *"The ones that are calculated on
the GPU will have GPU in their name"* — which is easy to read past. Prefer
`perlin*`, `simplex*` and `randomgpu`.

## Feedback loops amplify whatever is inside them

A feedback loop re-processes its own output every frame, so any operation
placed inside it compounds:

- A **hue rotation** inside the loop rotates a little more each pass; within a
  second everything converges to grey.
- An **additive composite** inside the loop settles at `source / (1 - decay)`,
  which clips to white.

Fix: feed back from *before* any colour grading — grade only on the way out.
Prefer a `maximum` composite over `add` for trails: it keeps the brightest
history without summing. Decay by scaling RGB (`brightness1`) rather than alpha
(`opacity`), because a maximum composite ignores alpha.

Working shape:

```
melt ─┬─────────────► comp(maximum) ──► posterize ──► grade ──► strobe ──► out
      │                  ▲
      └── fb ── drift ── decay        fb.top = comp   (not out)
```

## Movie File Out

- It is terminal: nothing consumes it, so it needs a keep-alive (above).
- Set `fps` explicitly — the shipped examples all bind it to `me.time.rate`.
- Add an `infoCHOP` pointed at it. `total_frames_written` and
  `total_audio_samples_written` are the only way to know recording is
  happening; the node itself reports nothing. Both channels exist on this
  build and both read `0` while nothing is being written.
- Measured once on this build: selecting the **AAC** audio codec made it report
  frames and samples written while producing **no file at all**. `pcm16`
  worked, and so did recording with no audio. Transcode afterwards if you need
  AAC. (Not reproduced since — treat it as a thing to check, not a law.)
- Realtime recording drops frames under load; the docs suggest turning Realtime
  off for a clean capture, and TouchDesigner repeats video frames to hold A/V
  sync when it cannot keep up.

## Output devices switched off

An `audiodeviceoutCHOP` with `active` off produces silence and reports nothing.
The same applies to MIDI Out, OSC Out and DMX Out. `td_health` checks all of
them.

## Non-Commercial licence

- **Resolution is capped at 1280×1280.** Asking for 1920×1080 silently gives
  1280×720 — TouchDesigner emits a *warning*, not an error, and carries on.
  Measured: `resolutionw 1920, resolutionh 1080` on a Noise TOP yields
  `width 1280, height 720`. `td_health` raises this to an error because it
  changes the deliverable.
- Realtime H.264/H.265 export accelerated by an Nvidia GPU is unavailable.
- Blob Track TOP is limited to 2 blobs.
- Output may not be used in paid work.

## Parameter groups are not settable

The shipped help documents `t` (Translate), `r` (Rotate), `s` (Scale) — these
are *groups*. The settable parameters are `tx`, `ty`, `tz`. `td_operator_schema`
returns the members and marks the groups; `td_build` rejects a group name with
the members as a suggestion. `td_set_params` only does the same when you pass
`op_type`; otherwise the name reaches TouchDesigner and comes back as an
`AttributeError`.

Not every operator has the transform parameters you expect: `circleTOP` has no
`tx`, it has `centerx`/`centery`.

## A claimed subtree refuses you too

`td_claim_scope(path, owner)` guards a subtree against *every* caller that does
not name itself, including the one that made the claim. After claiming, pass
the same `owner` into `td_build`, `td_set_params`, `td_set_flags`,
`td_annotate` and `td_palette_load`, or the refusal you get back names you as
the owner of the claim that is blocking you.

## Undo is not a batch step

`td_build` refuses a step whose method is `undo` or `redo`. Two of them in a
row reach past the batch's own entries into the artist's history, and the
rollback cannot give that back. Call `td_undo` on its own, after the batch has
returned.

## Saved files use contracted type names

A `.toe` on disk records `geoCOMP`, `evalDAT`, `parexecDAT` — not
`geometryCOMP`, `evaluateDAT`, `parameterexecuteDAT`. td-atlas carries a
measured alias table (71 entries) and expands them when reading a project, so
this only matters if you parse `.n` files yourself.

## exec blocks the main thread

Every bridge call runs inside a cook. A long `exec` freezes TouchDesigner, and
polling during a realtime recording causes dropped frames. Start a recording,
stay off the bridge, then stop it. This also ruins timing measurements taken
from inside `exec`: a loop that samples `cookTime` is holding up the very frame
it is measuring. Use `td_health`, which samples across an interval from the
host side.

## Stale errors

`errors()` keeps its last string until the operator cooks again, so a fixed
problem can be reported for another moment. If a fix looks ineffective, wait a
beat and check again.
