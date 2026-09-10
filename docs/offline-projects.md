# Reading projects offline

TouchDesigner ships `toeexpand`, which unpacks a `.toe`/`.tox` into a tree of
text files. td-atlas reads that tree, so a project can be inspected, searched
and compared **without TouchDesigner running**. Everything happens on a copy in
a cache, and the original file is never touched.

```bash
td-atlas project read  myproject.toe --path /project1 --params
td-atlas project grep  myproject.toe "def onValueChange"
td-atlas project diff  before.toe after.toe
td-atlas project variant save myproject.toe --label before-the-rewire
td-atlas project variant diff myproject.toe --label before-the-rewire --other after
```

`grep` reaches code no file search can. The Python and GLSL inside DATs lives in
the container, not on disk.

`diff` compares meaning. It lists added, removed, retyped, rewired and
re-parameterised operators, plus a line diff of changed DAT code. Nodes that
were only repositioned are kept separate, so they cannot bury a real change.
Paired with `td_snapshot` that is an audit trail for agent edits.

The file formats are undocumented, and the readers were derived by measurement.
The payload prologue is a fixed 27 bytes with a length field, and scanning for a
newline corrupts about a third of the shaders. Bit 4 of a parameter's flags word
marks expression mode. The 71 saved-name aliases come from instantiating every
type and comparing what TouchDesigner writes with what it reports.
