# Reading projects offline

TouchDesigner ships `toeexpand`, which unpacks a `.toe`/`.tox` into a tree of
text files. td-atlas reads that tree, so a project can be inspected, searched
and compared **without TouchDesigner running** — and without touching the
original, since everything happens on a copy in a cache.

```bash
td-atlas project read  myproject.toe --path /project1 --params
td-atlas project grep  myproject.toe "def onValueChange"
td-atlas project diff  before.toe after.toe
td-atlas project variant save myproject.toe --label before-the-rewire
td-atlas project variant diff myproject.toe --label before-the-rewire --other after
```

`grep` reaches code no file search can: the Python and GLSL inside DATs lives
in the container, not on disk. `diff` compares meaning — added, removed,
retyped, rewired and re-parameterised operators, plus a line diff of changed
DAT code, with pure repositioning kept separate so it cannot bury a real
change. Paired with `td_snapshot` that is an audit trail for agent edits.

The file formats are undocumented, so the readers were derived by measurement:
the payload prologue is a fixed 27 bytes with a length field (scanning for a
newline instead corrupts about a third of the shaders), bit 4 of a parameter's
flags word marks expression mode, and the 71 saved-name aliases come from
instantiating every type and comparing what TouchDesigner writes with what it
reports.
