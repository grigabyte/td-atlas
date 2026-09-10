# The network text, written on save

The bridge can write the network out as text beside the `.toe` on every save.
The project then gets a diffable history in git, and nobody has to remember to
ask for one. It is **off by default**. One key in `~/.td-atlas/config.json`
turns it on:

```json
{ "text_on_save": true }
```

The file is `<project>.network.json` beside the `.toe`, in the same format
`td-atlas project text` produces. The version number TouchDesigner adds on each
save is stripped, so one project keeps one text and git holds the history. The
write is atomic, a temporary in the same directory and then a rename. A file
already at that name that is not one of ours is never overwritten. The bridge
refuses and says so on its status panel.

Measured on 2025.32460, the text costs about 0.25–0.30 ms per operator, and
networks are covered up to a cap of 2000 operators. Past that the save would
grow by more than half a second, so the bridge writes nothing and again says so
on the panel. `"text_on_save_max_ops"` raises the cap for anyone willing to
wait.

The text covers the artist's own root components. TouchDesigner's `/local` and
`/perform`, the bridge's own `/tdatlas`, and the external `.tox` roots `/ui` and
`/sys` are left out, each by a measured rule the handler's comments give.

The text the bridge writes is printed from the live network. The one
`td-atlas project text` prints is read from the expanded file, so the two are
not byte-identical. Seven classes of difference are known and nothing else was
left over. The measurement ran on 2025.32460 over a purpose-built network of 45
operators, 21 built by the fixture and the other 24 the annotation component's
own subtree, and **104 of 722 compared fields differ**.

| fields | class | why |
| --- | --- | --- |
| 80 | custom parameter placement | The file keeps a custom parameter's *value* in `.parm` beside the built-in ones and its *definition* in `.cparm`, which the reader does not parse — so the reader files the value under `parms` where the live side files it under `custom_parms`. Forty parameters, two fields each. |
| 10 | custom parameter at its default | A custom parameter still at its default has no `.parm` line at all, so the file side has nothing to show; the live side prints every custom parameter. |
| 8 | float text formatting | The file keeps TouchDesigner's own printing (`2e+06`), the live side prints `2000000`. |
| 3 | parameter at its default with a flags word | `.parm` carries a line for a default-valued parameter whose flags word is not zero; the live side drops anything `isDefault`. |
| 1 | COMP input wiring | A COMP's operator input is stored in a `.network` file, which the offline reader does not parse, so the file-side text shows no inputs where the live-side text shows them. |
| 1 | flag vocabulary | `.n` flags the live gather has no name for (`showDocked`). |
| 1 | the `.tox` save's own root parameter | `enableexternaltox` is written into the root of a saved `.tox` and of no `.toe` — an artefact of how the measurement was taken, not of the text. |

A test re-measures those numbers. `tests/live_network.py` builds
the network again and `tests/test_live_text_diff.py` fails if a field falls
outside these seven classes. The test sits behind the `live` marker, so a plain
`pytest` does not collect it at all. `pytest -m live` runs it, and needs an
instance with the bridge.
