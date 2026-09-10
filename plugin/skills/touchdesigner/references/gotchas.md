# Gotchas

Every `td_` name here is an MCP tool the agent calls. Every fix below is
something the agent does itself.

Every entry here was hit while building a real composition. Each one produced
no error, no warning, and no visible sign that anything was wrong.

`td_health` prints each finding with a short kind in brackets, such as
`[WARN ] stalled` or `[ERROR] not-cooking`. Every kind it can print is named
below, so search for the kind directly. A test holds that, and a new detector
cannot arrive without an entry.

## A branch nothing consumes never runs

TouchDesigner cooks on demand. An operator is only pulled if a viewer, a render
chain, an output device or an export needs it. A network you build
programmatically has no viewer on it, so **it does not run at all**.
`td_health` calls this `not-cooking`, and reports it when an operator did not
cook once across the two samples.

**Symptom.** Renders work (they force a cook), but feedback trails never
accumulate, animation is frozen between calls, and a Movie File Out writes
nothing while reporting no error.

**Diagnosis.** `td_health` compares each operator's cook count against the frame
clock and names the dormant ones. A freshly built `noise → blur` pair reports
`0/2 operators cooking`.

**Fix.** Put a `cacheTOP` with `alwayscook` on at the end of the chain, pulling
whatever must stay live. `nullTOP` has no such parameter. `cacheTOP`,
`touchoutTOP` and `webrenderTOP` do, as do many CHOPs under the name
`cookalways`, and `td_search_parameters("cook every frame")` lists them.

```json
{"method": "op_create", "params": {
   "parent": "/project1/AV", "type": "cacheTOP", "name": "keepalive",
   "pars": {"alwayscook": true, "cachesize": 1},
   "connect": [{"from": "/project1/AV/out", "index": 0}]}}
```

Adding that turns the same network into `3/3 operators cooking`.

A recorder must be *inside* what the keep-alive pulls. Wire
`out → rec → keepalive`, not `out → keepalive` with `rec` hanging off `out`.

## A Python extension attached the wiki's way is silently absent

The offline wiki's `Extensions` page shows the extension expression written as
`MyClass(me)`. On this build that leaves `comp.extensions[0]` equal to `None`.
`errors()` is empty, `warnings()` is empty, and `extensionsReady` reports
`True`. Nothing says the extension is missing, and every call into it fails
later as a plain attribute error on the COMP.

The form that works, and the one TouchDesigner's own components use:

```
op('./<DAT>').module.<Class>(me)
```

Use `td_extension_add`, which writes that form, parses the code first, and reads
the instance back. A class that fails to instantiate comes back as the real
exception.

## A GLSL shader that does not compile is a *warning*

A GLSL TOP whose shader fails to compile outputs a checkerboard or black and
reports through `warnings()`, not `errors()`. The warning text is only
`The GLSL Shader has compile errors (Use Info DAT to see details)`. It names no
line and no file, so anything scanning for errors misses it entirely.

The compiler's own output is a property, `compileResult`, on `glslTOP`,
`glslmultiTOP` and `glslMAT` (`glslPOP` has no documented equivalent).
`td_health` reads it and prints the failing line and source DAT directly, as
`shader-compile`.

## Background throttling

TouchDesigner all but stops rendering when its window is not visible. Cook
counts across the whole application drop to a few per second, including the
default project's own output. Audio keeps running.

This is easy to mistake for a broken network. Bring the window to the front
before measuring performance or recording in realtime.

`td_health` calls this `stalled`. The kind means fewer than five frames advanced
between its two samples while the timeline is playing. It is a warning about the
*report*, not about the network. Without frames there is no evidence either way,
so `td_health` says so and stops short of naming operators as dormant. A paused
timeline gives the same silence and is reported as `paused`, which is a note and
not a warning.

## CPU operators hiding in a menu

Some operator options run on the CPU and cost orders of magnitude more. The
Noise TOP is the trap. Its `type` menu labels distinguish "Simplex 3D (GPU)"
from plain "Sparse", "Hermite", "Harmomic Summation" (TouchDesigner's own
spelling; the value is `harmonic`), "Random" and "Alligator", which are
CPU-side.

Measured on 2025.32460 at 1280×720, Apple Silicon, sampled by `td_health` over
a 2 s interval. `sparse` cost 96 ms per cook (reproduced twice), `alligator`
104 ms, `harmonic` 104 ms and `hermite` 38 ms (each measured once).
`simplex3d` and `perlin3d` never crossed the 8 ms threshold at all. Whether
that shows up as dropped frames depends on the rest of the network, so do not
wait for the frame rate to tell you.

Nothing warns you. `td_health` flags any operator costing more than 8 ms as
`expensive`, which is per operator. The separate `slow` finding is about the
whole application, and means the measured frame rate came in under 60% of the
project's target. Tell the two apart. `expensive` with no `slow` means the rest
of the network is absorbing it, and `slow` with no `expensive` means the cost is
spread across many operators or is not in cooking at all.

The operator summary says so in prose too, *"The ones that are calculated on the
GPU will have GPU in their name"*, which is easy to read past. Prefer `perlin*`,
`simplex*` and `randomgpu`.

## Feedback loops amplify whatever is inside them

A feedback loop re-processes its own output every frame, so any operation
placed inside it compounds:

- A **hue rotation** inside the loop rotates a little more each pass; within a
  second everything converges to grey.
- An **additive composite** inside the loop settles at `source / (1 - decay)`,
  which clips to white.

**Fix.** Feed back from *before* any colour grading, and grade only on the way
out. Prefer a `maximum` composite over `add` for trails, which keeps the
brightest history without summing. Decay by scaling RGB (`brightness1`) and not
alpha (`opacity`), since a maximum composite ignores alpha.

Working shape:

```
melt ─┬─────────────► comp(maximum) ──► posterize ──► grade ──► strobe ──► out
      │                  ▲
      └── fb ── drift ── decay        fb.top = comp   (not out)
```

### A Feedback TOP's *wire* input is not its `top` parameter

A Feedback TOP takes the frame it replays from its `top` parameter. Its wired
input is a different thing, and wiring that input to a node that is itself
downstream of the loop makes a genuine cook dependency loop. TouchDesigner
notices and writes `Cook dependency loop detected` into the node's warning.

A session reported that only `td_network` showed the warning and that
`td_errors` stayed silent. **Measured on the live instance (2026-08-30, build
2025.32460), the tools do not differ.** A loop built from
`constant → composite(maximum) → level → feedback`, with the Feedback TOP's
wired input taken from the `level` inside the loop, reports

```
Warning: Cook dependency loop detected. ...
	# Cook stack starts
	/…/Lkeep
	# Cook dependency loop starts
	/…/Lgrade  /…/Lcomp  /…/Lfb
```

on `Lgrade`. `td_errors` and `td_network` both name it, in either call order, on
repeated calls. Both are asked for the subtree the loop is in. `td_errors` walks
the project by default and takes any other `path` by name, so a loop built
outside the project is only reported when it is asked for. What *does* differ is
the state **before the loop has cooked**. Freshly built with nothing pulling it,
every operator sat at `0` cooks and both tools reported nothing at all. Adding a
`cacheTOP` with `alwayscook` took them to 90 cooks, and from then on both tools
carried the full text. The silence was the loop not running, which is *A branch
nothing consumes never runs* above. Check errors *after* something pulls the
chain.

`td_health` folds every warned operator into one `note` line, and carries the
warning's first line:

```
[note ] 1 operator(s) reporting a warning
         /…/Lgrade (Cook dependency loop detected. Check for exports, ...)
```

The cook stack behind that sentence is left to `td_errors`, which prints the
whole thing.

**Fix.** Take the Feedback TOP's wired input from a node *before* the loop.

## A composite's first input is the foreground

`compositeTOP` with `operand = over` is not symmetric, and nothing tells you
which way round it goes. The shipped help gives the formula, and it settles it:

```
over:  (input2.rgba * (1.0 - input1.a)) + input1.rgba
```

The help numbers inputs from 1; connectors are numbered from 0. So the help's
`input1` is **connector index 0**, and it is the layer that covers the other
one. Put the foreground on index 0.

Get it backwards with an opaque foreground and the frame goes solid black, with
no error and no warning. An opaque layer on index 0 hides index 1 completely.
This cost a session about an hour, with an accumulating feedback frame wired to
index 0 and the source to index 1, so the source was never seen.

`swaporder` ("Swaps the order of the input pairs. A operation B is changed to B
operation A") flips it without rewiring. The `operand` menu documents the
formula for many of its entries, and `td_docs("Composite TOP")` prints them,
which is faster than guessing which operations care about order. The commutative
ones do not (`add`, `multiply`, `average`, `maximum`, `minimum`). The layer
modes do, and the help names `over` and `hardlight` as its own examples.
`outside` is documented as `input1.rgba * (1.0 - input2.a)`, which is the same
asymmetry again.

## A default is not the neutral value

`levelTOP` has a `contrast` parameter, and its default is **1.0**, not 0.
Setting it to 0 collapses the image to a flat grey, every pixel the same, with
no error, no warning and nothing in `td_health`. Diagnosing it took four calls,
rendering the upstream nodes one at a time to prove they were still alive.

Measured from the index on build 2025.32460 (`params` table, `levelTOP`):
`contrast` default `1.0`, range 0–1; and so are `brightness1` and `gamma1`,
which are the same trap. `blacklevel` defaults to `0.0`.

Read `par.default`, which `td_operator_schema` prints, before assuming which
number means "no change". Multiplicative parameters are neutral at 1, additive
ones at 0, and the parameter name does not tell you which kind it is.

## A relative OP path is read from the network the node sits in

A COMP is a network, so a relative path in one of its own parameters looks as
if it should start inside it. It does not. It resolves from the network the
COMP *sits in*, alongside its siblings.

Measured, from TouchDesigner's own shipped snippet
(`OPSnippets/Snippets/COMP/geometryCOMP.tox`, expanded): every example writes
`material 0 phong1` or `material 0 constant1` on `geo1`, and `phong1` sits
beside `geo1` in the same network, not inside it. So for
`/project1/REF/gobj`, the MAT `/project1/REF/mdark` is written `mdark`.
`../mdark` points a level too high and resolves to nothing.

An OP-path parameter that resolves to nothing is silent. The geometry renders
with a default shader, `errors()` is empty and `td_errors` says nothing. The
only way to see it is `par.eval()`, which returns `None`; `par.val` happily
returns the string you wrote. When `td_op_info` shows an OP parameter, check
that its value is an operator and not just text you typed.

## A fresh Geometry COMP already has geometry in it

**Measured on the live instance (2026-08-30, build 2025.32460).** A newly
created `geometryCOMP` arrives holding one child, `torus1`, which on this build
is a `torusPOP` and not a SOP. It has `display` and `render` both on, and it is
what gets drawn. A Render TOP at 128×128 pointed at an otherwise untouched COMP
(camera at `tz 5`) returned 3268 of 16384 pixels with alpha above 0.5, in a
bounding box of 98×38. Destroying `torus1` took the same render to 0. The
shipped help treats the torus as normal enough to write examples around it
("if you had `torus1` inside `geo1`", *Run Command Examples*). The offline index
does not record a fresh COMP's contents, since they are a runtime fact.

Fresh COMPs of other families are not empty either. A `cameraCOMP` came with one
child and a `lightCOMP` with 21, but those are viewport gizmos and do not reach
a Render TOP. The Geometry COMP is the one whose contents render.

`td_network` defaults to `depth=1` and lists only direct children, so
`td_network("/project1/REF")` names the COMP and stops there. It does print
`numChildren`, which is the cheap tell, and reads `geometryCOMP … numChildren: 1`
on a COMP you have put nothing into. The contents themselves need `td_network`
pointed at the COMP, or `depth=2`.

After creating a Geometry COMP, look inside it before wondering why the render
shows something you did not build. While you are in there, check the flags on
your own SOP. That was measured too, and it is the mirror-image trap. A
`sphereSOP` created inside the COMP (through `op_create`, and through
`.create()` in `td_exec` alike) arrives with `display` **off** and `render`
**off**, while the torus beside it has both on. Until you set them, your
geometry is the invisible one:

```
/…/g6/s6      sphereSOP   display false  render false
/…/g6/torus1  torusPOP    display true   render true
```

With the flags off, every measurement of the render is the same number no matter
what you change upstream. See *Identical numbers are usually a picture that did
not change* below.

## Movie File Out

- It is terminal. Nothing consumes it, so it needs a keep-alive (above).
- Set `fps` explicitly, since the shipped examples all bind it to
  `me.time.rate`.
- Add an `infoCHOP` pointed at it. `total_frames_written` and
  `total_audio_samples_written` are the only way to know recording is
  happening; the node itself reports nothing. Both channels exist on this
  build and both read `0` while nothing is being written.
- Measured once on this build, selecting the **AAC** audio codec made it report
  frames and samples written while producing **no file at all**. `pcm16`
  worked, and so did recording with no audio. Transcode afterwards if you need
  AAC. This has not reproduced since, so treat it as a thing to check.
- Realtime recording drops frames under load; the docs suggest turning Realtime
  off for a clean capture, and TouchDesigner repeats video frames to hold A/V
  sync when it cannot keep up.

## Output devices switched off

An `audiodeviceoutCHOP` with `active` off produces silence and reports nothing.
The same applies to MIDI Out, OSC Out and DMX Out. `td_health` checks all of
them and reports `output-off`.

## Non-Commercial licence

- **Resolution is capped at 1280×1280.** Asking for 1920×1080 silently gives
  1280×720. TouchDesigner emits a *warning*, not an error, and carries on.
  Measured on a Noise TOP, `resolutionw 1920, resolutionh 1080` yields
  `width 1280, height 720`. `td_health` raises this to an error,
  `resolution-clamped`, since it changes the deliverable. The licence itself is
  reported once as the note `licence`.
- Realtime H.264/H.265 export accelerated by an Nvidia GPU is unavailable.
- Blob Track TOP is limited to 2 blobs.
- Output may not be used in paid work.

## Parameter groups are not settable

The shipped help documents `t` (Translate), `r` (Rotate) and `s` (Scale), which
are *groups*. The settable parameters are `tx`, `ty`, `tz`. `td_operator_schema`
returns the members and marks the groups; `td_build` rejects a group name with
the members as a suggestion. `td_set_params` only does the same when you pass
`op_type`; otherwise the name reaches TouchDesigner and comes back as an
`AttributeError`.

Not every operator has the transform parameters you expect. `circleTOP` has no
`tx`, it has `centerx`/`centery`.

## Saved files use contracted type names

A `.toe` on disk records `geoCOMP`, `evalDAT` and `parexecDAT`, where the full
names are `geometryCOMP`, `evaluateDAT` and `parameterexecuteDAT`. td-atlas
carries a measured alias table (71 entries) and expands them when reading a
project, so this only matters if you parse `.n` files yourself.

## exec blocks the main thread

Every bridge call runs inside a cook. A long `exec` freezes TouchDesigner, and
polling during a realtime recording causes dropped frames. Start a recording,
stay off the bridge, then stop it. This also ruins timing measurements taken
from inside `exec`. A loop that samples `cookTime` is holding up the very frame
it is measuring. Use `td_health`, which samples across an interval from the host
side.

Time is the other thing that does not move inside a request. A `noiseTOP` driven
by `absTime.frame`, cooked with `cook(force=True)` and read five times in one
`exec`, returned five identical SHA-1s and the same frame number (`51208`) five
times. The same node read once per request, three requests running, gave three
different hashes on frames 51209, 51210, 51211. A loop with `sleep` in it does
not sample a range; it samples one frame forty times. Take a strip with repeated
calls. The host-side `contact_sheet()` helper in `references/tools.md` does
that, one bridge call per frame with TouchDesigner left to run in between.

## Identical numbers are usually a picture that did not change

A session reported that `numpyArray(delayed=False)` through `td_exec` hands back
a stale frame. In that report `cook(force=True)` did not help, and three
measurements in a row came back character-for-character identical while the
scene was being changed underneath them. **That does not reproduce.** Measured
on the live instance (2026-08-30, build 2025.32460), hashing the array:

- a `constantTOP → levelTOP` chain, with `colorr` set in one request and read in
  the next, gave three distinct hashes over `0.1 → 0.5 → 0.9`, and
  `numpyArray()`, `numpyArray(delayed=True)`, `numpyArray(delayed=False)` and
  `saveByteArray()` all moved together;
- the same change made and read back **inside one request** (set `0.2`,
  `cook(force=True)`, read, then set `0.8`, `cook(force=True)`, read) gave two
  distinct hashes and the two pixel values;
- a Render TOP, camera `tz` stepped `3 → 8 → 1.5` one request apart, gave an
  alpha bounding box of `128×80 → 60×22 → 128×128` with a distinct hash each
  time, and dropping the resolution 128→64 came through as well;
- a change made at the head of a four-deep chain that **nothing pulls**, read at
  the tail with `cook(force=True)`: fresh every time.

What *does* reproduce is the symptom. Toggling `bypass` on a noise SOP inside a
Geometry COMP and measuring the Render TOP gave the identical hash and an alpha
bounding box of `0×0` on every reading. That SOP was created with `display` and
`render` off, and nothing of it was ever in the frame. Setting the two flags
turned the same toggle into `64×64 → 0×0 → 64×64` and three different hashes.

The rule is that **a number that will not move is evidence about the picture,
not about the reader.** Before theorising about staleness, render the node and
look at it, and check the flags on whatever you expected to be in the frame. For
a second opinion from a different code path, `saveByteArray()` is what
`td_render` uses, and it was measured moving in step with `numpyArray` on every
case above.

## A bypassed operator is not a broken one, and looks like neither

The bypass flag makes an operator pass its input through without applying
itself. Downstream still gets a picture, the one it would have had if the
operator were not there, and nothing reports an error. A bypass left on from an
afternoon of debugging is the quietest way to lose an effect.

`td_health` reports it as `bypassed` and lists every path. It reads the flag and
nothing else, and cannot tell an intentional bypass from a forgotten one, so it
names them all. Bypass is a flag, not a parameter. It is in `NODE_FLAGS`, so
`td_flags` reads it and `td_set_flags` clears it, and it does not appear in
`td_operator_schema`.

## Realtime off changes what "one frame" means

With Realtime on, the default, TouchDesigner drops frames to keep the timeline
on the wall clock, which is what a performance needs. With it off, every frame
is rendered no matter how long it takes, which is what an export needs. Nothing
about a running network makes it obvious which mode it is in, and the two give
different answers to "is this fast enough": with Realtime off the frame rate is
not a measurement of anything.

`td_health` reports the state as the note `non-realtime` when it is off. Turn it
off for a clean recording (see Movie File Out above), and turn it back on before
judging performance.

## A health report that only looked at part of the network

Two of `td_health`'s findings are about the report, not about the project:

- `walk-truncated` means the bridge stopped walking at its own node cap, so the
  report covers only the operators it reached. It names how many were skipped.
  Run the check again on a subtree to cover the rest.
- `interval-clamped` means the gap asked for between the two samples was
  reduced. The host sleeps through that gap and answers nothing else meanwhile,
  so the interval is bounded.

## Stale errors

`errors()` keeps its last string until the operator cooks again, so a fixed
problem can be reported for another moment. If a fix looks ineffective, wait a
beat and check again.

An operator's own error and warning strings reach the report as `node-errors`
and `node-warnings`. Tracebacks raised inside a script or a callback are kept by
TouchDesigner in a different place entirely and appear in no operator's error
list. `td_health` reads them separately and reports `script-errors`. When that
read is the thing that fails, it says `script-errors-unread`. Whether anything
raised is then unknown, and unknown is not clean.
