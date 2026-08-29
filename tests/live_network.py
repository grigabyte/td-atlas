"""The network the live-vs-file measurement is made on, and the classifier.

Not a test module: it is imported both by `tests/test_live_text_diff.py` and by
the throwaway scripts that measure. The point of the sharing is the numbers in
`README.md` and in `handler.py`'s comment — they were measured on *this*
network, and a reader who wants to check them has to be able to build it again.

The build code is a string because it runs inside TouchDesigner, through the
bridge's `exec`: nothing here imports `td`.
"""

from __future__ import annotations

# Built under a parent COMP the caller names. Every operator family the
# handler's gather layer treats differently is represented: a COMP with
# children and with wiring *inside* it, a COMP wired to another COMP, DATs
# that own their text and DATs that do not, a Table DAT, custom parameters
# with a page, a parameter in expression mode and one in bind mode, flags,
# an annotation, and a component with an extension.
BUILD_CODE = '''
holder = op(%(holder)r)
existing = holder.op(%(name)r)
if existing is not None:
    existing.destroy()
d = holder.create(baseCOMP, %(name)r)
d.nodeX, d.nodeY = 400, -700
made = []

def mk(parent, kind, name, x, y):
    o = parent.create(kind, name)
    o.nodeX, o.nodeY = x, y
    made.append(o.path)
    return o

# TOPs, wired.
noise = mk(d, noiseTOP, 'noise1', 0, 0)
noise.par.seed.expr = 'absTime.frame*0.01'
noise.par.type = 'sparse'
level = mk(d, levelTOP, 'level1', 200, 0)
level.inputConnectors[0].connect(noise)
level.bypass = True
level.color = (0.9, 0.2, 0.1)

# CHOPs, one of them exporting.
lfo = mk(d, lfoCHOP, 'lfo1', 0, -150)
math = mk(d, mathCHOP, 'math1', 200, -150)
math.inputConnectors[0].connect(lfo)
math.par.gain = 2.5
math.export = True

# SOPs.
box = mk(d, boxSOP, 'box1', 0, -300)
xform = mk(d, transformSOP, 'xform1', 200, -300)
xform.inputConnectors[0].connect(box)
xform.par.tx = 1.5

# A MAT.
mat = mk(d, constantMAT, 'mat1', 400, -300)
mat.par.colorr = 0.25

# DATs: one that owns its text, one that does not, and a table.
text = mk(d, textDAT, 'text1', 0, -450)
text.text = 'line one\\n\\tindented\\nunicode \\u2014 \\u00e9\\n'
table = mk(d, tableDAT, 'table1', 200, -450)
table.clear()
table.appendRow(['key', 'value'])
table.appendRow(['a', 'b b'])
table.appendRow(['c', ''])
null = mk(d, nullDAT, 'null1', 400, -450)
null.inputConnectors[0].connect(text)

# Two COMPs wired to each other, each with children and wiring inside.
inner1 = mk(d, baseCOMP, 'inner1', 0, -600)
inner2 = mk(d, baseCOMP, 'inner2', 250, -600)
for comp in (inner1, inner2):
    a = mk(comp, noiseTOP, 'noise1', 0, 0)
    b = mk(comp, levelTOP, 'level1', 200, 0)
    b.inputConnectors[0].connect(a)
    out = mk(comp, outTOP, 'out1', 400, 0)
    out.inputConnectors[0].connect(b)
inner2_in = mk(inner2, inTOP, 'in1', -200, 0)
inner2.op('noise1').inputConnectors[0].connect(inner2_in)
inner2.inputConnectors[0].connect(inner1.outputConnectors[0])

# Custom parameters, a page, an expression and a bind.
page = d.appendCustomPage('Atlas')
page.appendFloat('Amount')
page.appendStr('Label')
page.appendToggle('Enabled')
d.par.Amount = 0.75
d.par.Label = 'a label'
d.par.Enabled = True
level.par.opacity.bindExpr = "op('%(name)s').par.Amount"
level.par.opacity.mode = ParMode.BIND

# An annotation, and a comment-carrying node.
try:
    note = mk(d, annotateCOMP, 'note1', 450, -600)
    note.par.Titletext = 'difftest'
    note.par.Bodytext = 'a note about this network'
except Exception as exc:
    print('annotate: %%s' %% exc)

# An extension on the container.
ext = mk(d, textDAT, 'ext1', 400, -150)
ext.text = 'class DiffExt:\\n    def __init__(self, owner):\\n        self.owner = owner\\n'
d.par.extension1 = "op('ext1').module.DiffExt(me)"
d.par.promoteextension1 = True

result = {'path': d.path, 'made': len(made)}
'''


def build_params(holder: str, name: str) -> dict:
    """The `exec` params that build the network under `holder/name`."""
    return {"code": BUILD_CODE % {"holder": holder, "name": name}}


# -- the comparison ---------------------------------------------------------

# The fields `live_node_data` and `serialize.node_data` both emit for a node.
_FIELDS = (
    "name", "type", "family", "tile", "flags", "color", "inputs",
    "parms", "custom_parms", "custom_pages", "text", "table",
)


def _index(node: dict, path: str = "") -> dict:
    """A node tree flattened to {path: node}, children stripped."""
    here = path + "/" + node.get("name", "?")
    out = {here: {k: node.get(k) for k in _FIELDS}}
    for child in node.get("children") or ():
        out.update(_index(child, here))
    return out


def compare(live_root: dict, file_root: dict) -> dict:
    """Field-by-field diff of the same network read live and read from a file.

    Returns `{"fields": n, "same": n, "diffs": [...]}` where each diff is
    `{"path":, "field":, "live":, "file":}` — one entry per differing field,
    never per differing line, so a class shows up as a count and not as noise.
    """
    live = _index(live_root)
    file = _index(file_root)
    diffs = []
    fields = same = 0
    for path in sorted(set(live) | set(file)):
        l_node = live.get(path)
        f_node = file.get(path)
        if l_node is None or f_node is None:
            diffs.append({
                "path": path,
                "field": "*node*",
                "live": "present" if l_node else "absent",
                "file": "present" if f_node else "absent",
            })
            fields += 1
            continue
        for field in _FIELDS:
            fields += 1
            if l_node.get(field) == f_node.get(field):
                same += 1
                continue
            if field in ("parms", "custom_parms"):
                # A map is not one field: a differing parameter is one field,
                # so that a class's size is the number of parameters it
                # touches and not the number of operators that have any.
                fields -= 1
                keys = set(l_node.get(field) or {}) | set(f_node.get(field) or {})
                for key in sorted(keys):
                    fields += 1
                    lv = (l_node.get(field) or {}).get(key)
                    fv = (f_node.get(field) or {}).get(key)
                    if lv == fv:
                        same += 1
                    else:
                        diffs.append({"path": path, "field": "%s.%s" % (field, key),
                                      "live": lv, "file": fv})
                continue
            diffs.append({"path": path, "field": field,
                          "live": l_node.get(field), "file": f_node.get(field)})
    return {"fields": fields, "same": same, "diffs": diffs}


# -- the classes ------------------------------------------------------------
#
# Every class here was measured on the network above (2025.32460, macOS) and
# every one names the file the reader does or does not parse. The names are
# the ones `README.md` and `handler.py`'s comment use.

CUSTOM_PLACEMENT = "custom parameter placement"
COMP_WIRE = "COMP input wiring"
PANEL_WIRE = "panel COMP input wiring"
TABLE_SHAPE = "Table DAT shape"
FLOAT_FORMAT = "float text formatting"
FLAG_VOCABULARY = "flag vocabulary"
DEFAULT_PARM = "parameter at its default with a flags word"
CUSTOM_DEFAULT = "custom parameter at its default"
TOX_ROOT = "the .tox save's own root parameter"

CLASSES = (
    CUSTOM_PLACEMENT, COMP_WIRE, PANEL_WIRE, TABLE_SHAPE,
    FLOAT_FORMAT, FLAG_VOCABULARY, DEFAULT_PARM, CUSTOM_DEFAULT, TOX_ROOT,
)

# The flag names the live gather knows. Kept here rather than imported so the
# classifier says what it checked even when `handler._FLAG_WHEN_ON` grows.
_LIVE_FLAGS = frozenset((
    "parlanguage", "viewer", "display", "render", "bypass", "lock", "current",
    "picked", "pickable", "cloneImmune", "activate", "export",
))


def _float_equal(a, b) -> bool:
    if not isinstance(a, str) or not isinstance(b, str):
        return False
    try:
        return float(a) == float(b)
    except ValueError:
        return False


def classify(diffs: list, root_path: str) -> dict:
    """Sort a `compare()` diff list into the named classes.

    Returns `{class: [diff, ...]}` with one extra key, `"unexplained"`, for
    anything no class covers — the point of the exercise is that this key is
    the honest one, so it is never quietly dropped.
    """
    by_path_field = {(d["path"], d["field"]): d for d in diffs}
    out = {name: [] for name in CLASSES}
    out["unexplained"] = []
    for d in diffs:
        path, field, live, file = d["path"], d["field"], d["live"], d["file"]
        head, _, name = field.partition(".")

        if head == "custom_parms" and live is not None and file is None:
            twin = by_path_field.get((path, "parms." + name))
            if twin is not None and twin["live"] is None and twin["file"] == live:
                out[CUSTOM_PLACEMENT].append(d)
                continue
            if twin is None:
                out[CUSTOM_DEFAULT].append(d)
                continue
        if head == "parms" and live is None and file is not None:
            twin = by_path_field.get((path, "custom_parms." + name))
            if twin is not None and twin["file"] is None and twin["live"] == file:
                out[CUSTOM_PLACEMENT].append(d)
                continue
            if path == root_path and name == "enableexternaltox":
                out[TOX_ROOT].append(d)
                continue
            out[DEFAULT_PARM].append(d)
            continue
        if field == "inputs":
            if file == [] and live:
                out[COMP_WIRE].append(d)
                continue
            if live == [] and file:
                out[PANEL_WIRE].append(d)
                continue
        if field == "table" and live and file:
            flat_live = [c for row in live for c in row]
            flat_file = [c for row in file for c in row]
            if flat_live == flat_file and len(live) != len(file):
                out[TABLE_SHAPE].append(d)
                continue
        if head in ("parms", "custom_parms") and _float_equal(live, file):
            out[FLOAT_FORMAT].append(d)
            continue
        if field == "flags" and isinstance(live, dict) and isinstance(file, dict):
            extra = set(file) - set(live)
            if all(k not in _LIVE_FLAGS for k in extra) and not set(live) - set(file):
                if all(live[k] == file[k] for k in live):
                    out[FLAG_VOCABULARY].append(d)
                    continue
        out["unexplained"].append(d)
    return out


def bridge_or_skip():
    """A client aimed at a running bridge, or a skip. For live tests only."""
    import pytest

    import os

    if os.environ.get("TD_ATLAS_NO_LIVE"):
        pytest.skip("TD_ATLAS_NO_LIVE is set")
    from td_atlas.bridge.client import BridgeClient

    try:
        client = BridgeClient.discover()
        client.call("ping")
    except Exception as exc:
        # Every reason not to have a bridge is the same reason here: no
        # instance, no token, two instances and no flag to choose between them.
        pytest.skip("no TouchDesigner bridge: %s" % exc)
    return client


# Written into the panel and measured in one round trip, because the bridge
# repaints the panel at the end of every request it serves — a second call
# would measure the repaint and not the text under test.
INK_CODE = '''
import numpy
p = op("/tdatlas/panel")
p.par.text = %(text)r
a = p.numpyArray(delayed=False)
ink = a[..., 3] > 0.05
rows = ink.any(axis=1)[::-1]
cols = ink.any(axis=0)
ys = [i for i, r in enumerate(rows) if r]
xs = numpy.nonzero(cols)[0]
starts = []
prev = False
for i, r in enumerate(rows):
    if r and not prev:
        starts.append(int(i))
    prev = bool(r)
result = json.dumps({
    "height": int(a.shape[0]),
    "width": int(a.shape[1]),
    "line_starts": starts,
    "ink_top": int(ys[0]) if ys else None,
    "ink_bottom": int(ys[-1]) if ys else None,
    "ink_right": int(xs[-1]) if len(xs) else None,
    "font": p.par.font.eval(),
    "fontsize": float(p.par.fontsizex.eval()),
})
'''


def panel_ink(client, text: str) -> dict:
    """Draw `text` on the live panel and report where its pixels landed."""
    import json as _json

    return _json.loads(client.call("exec", code=INK_CODE % {"text": text})["result"])
