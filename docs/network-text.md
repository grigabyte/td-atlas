# The network text, written on save

The bridge can write the network out as text beside the `.toe` every time the
artist saves, so a project gets a diffable history in git without anybody
remembering to ask for one. It is **off by default** — writing a file into
somebody's own project folder is not something to start doing unasked — and
turning it on is one key in `~/.td-atlas/config.json`:

```json
{ "text_on_save": true }
```

The file is `<project>.network.json` beside the `.toe`, in the same format
`td-atlas project text` produces. The version number TouchDesigner adds on
each save is stripped, so one project keeps one text and git holds the
history. The write is atomic (a temporary in the same directory, then a
rename), and a file already at that name that is not one of ours is never
overwritten — the bridge refuses and says so on its status panel.

Measured on 2025.32460, the text costs about 0.25–0.30 ms per operator, so
networks are covered up to a cap of 2000 operators — beyond that the save
would grow by more than half a second and the bridge writes nothing, again
saying so on the panel. Raise it with `"text_on_save_max_ops"` if you would
rather wait.

The text covers the artist's own root components. TouchDesigner's `/local` and
`/perform`, the bridge's own `/tdatlas`, and the external `.tox` roots `/ui`
and `/sys` are left out — each by a measured rule the handler's comments give.

This text is printed from the live network, and the one `td-atlas project text`
prints is read from the expanded file, so the two are not byte-identical. Seven
classes of difference are known and nothing else was left over: measured on
2025.32460 over a purpose-built network of 45 operators — 21 built by the
fixture, the other 24 the annotation component's own subtree — **104 of 722
compared fields differ**.

| fields | class | why |
| --- | --- | --- |
| 80 | custom parameter placement | The file keeps a custom parameter's *value* in `.parm` beside the built-in ones and its *definition* in `.cparm`, which the reader does not parse — so the reader files the value under `parms` where the live side files it under `custom_parms`. Forty parameters, two fields each. |
| 10 | custom parameter at its default | A custom parameter still at its default has no `.parm` line at all, so the file side has nothing to show; the live side prints every custom parameter. |
| 8 | float text formatting | The file keeps TouchDesigner's own printing (`2e+06`), the live side prints `2000000`. |
| 3 | parameter at its default with a flags word | `.parm` carries a line for a default-valued parameter whose flags word is not zero; the live side drops anything `isDefault`. |
| 1 | COMP input wiring | A COMP's operator input is stored in a `.network` file, which the offline reader does not parse, so the file-side text shows no inputs where the live-side text shows them. |
| 1 | flag vocabulary | `.n` flags the live gather has no name for (`showDocked`). |
| 1 | the `.tox` save's own root parameter | `enableexternaltox` is written into the root of a saved `.tox` and of no `.toe` — an artefact of how the measurement was taken, not of the text. |

None of those numbers is a memory: `tests/live_network.py` builds the network
again and `tests/test_live_text_diff.py` fails if a field falls outside these
seven classes. It sits behind the `live` marker, so a plain `pytest` does not
collect it at all; `pytest -m live` runs it, and needs an instance with the
bridge.

Two more classes stood here until the measurement found their cause, and both
were bugs of ours rather than limits of the text. The `.table` header's row and
column counts were read the wrong way round, so a 3×2 table came back as 2×3 —
and the round trip had never caught it, because the writer repeated the same
swap. And a panel COMP's wire to the COMP beside it sits in the file's `inputs`
block while live it hangs off `inputCOMPConnectors`, which the live gather did
not read; it now reads both connector lists, each wire under its own
connector's index. Both sides are fixed.
