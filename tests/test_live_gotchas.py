"""The gotchas that could only be settled with TouchDesigner running.

`plugin/skills/touchdesigner/references/gotchas.md` carried four claims that came out
of one build session and had never been measured: that a fresh Geometry COMP
ships with a torus that is what actually renders, that a SOP created inside one
arrives invisible, that `td_errors` is blind to a cook dependency loop, and
that `numpyArray` hands back a stale frame. A gotcha nobody can reproduce is
worse than no gotcha, because an agent believes the file over its own
measurements — so each of them is pinned here against the live instance, in the
same shape the file states it.

Two of the entries are about *tools* rather than TouchDesigner — that
`op_create` carries a DAT's text far enough for a shader to compile, and that a
parameter read-back names the member TouchDesigner will only name by group.
They live here for the same reason: both were written against a message
TouchDesigner produces, and only a running TouchDesigner produces it.

Needs a bridge and skips without one (`TD_ATLAS_NO_LIVE=1` skips it anyway).
Everything is built under `/tdatlas/gotchaprobe` and destroyed again; the
project is never saved, and nothing here touches `/project1`.
"""

from __future__ import annotations

import time

import pytest

import live_network as ln


# Every test here drives a running instance, so the marker goes on the
# module rather than on eighteen individual tests.
pytestmark = pytest.mark.live

HOLDER = "/tdatlas"
NAME = "gotchaprobe"
ROOT = HOLDER + "/" + NAME
OWNER = "test_live_gotchas"


@pytest.fixture(scope="module")
def probe():
    client = ln.bridge_or_skip()
    client.call(
        "exec",
        code=(
            "old = op(%r)\n"
            "if old is not None:\n"
            "    old.destroy()\n"
            "d = op(%r).create(baseCOMP, %r)\n"
            "d.nodeX, d.nodeY = 1400, 600\n"
            "result = d.path\n" % (ROOT, HOLDER, NAME)
        ),
    )
    try:
        yield client
    finally:
        client.call(
            "exec",
            code="o = op(%r)\nif o is not None:\n    o.destroy()\nresult = 1\n" % ROOT,
        )


def _exec(client, code):
    return client.call("exec", code=code)["result"]


def _wait_for_cooks(client, path, at_least, timeout=5.0):
    """Let TouchDesigner run until a node has cooked, or give up.

    Nothing cooks while a request is being served, so this has to be a host-side
    wait with the bridge idle between polls — the same reason a `sleep` inside
    `exec` samples one frame forty times.
    """
    deadline = time.time() + timeout
    while True:
        cooks = _exec(client, "result = op(%r).totalCooks" % path)
        if cooks >= at_least or time.time() > deadline:
            return cooks
        time.sleep(0.2)


# -- a fresh Geometry COMP --------------------------------------------------

def test_a_fresh_geometry_comp_arrives_holding_a_torus_with_its_flags_on(probe):
    kids = _exec(
        probe,
        "g = op(%r).create(geometryCOMP, 'freshgeo')\n"
        "result = [{'name': k.name, 'optype': k.OPType,\n"
        "           'display': bool(k.display), 'render': bool(k.render)}\n"
        "          for k in g.children]\n" % ROOT,
    )
    assert [k["name"] for k in kids] == ["torus1"]
    assert kids[0]["display"] and kids[0]["render"]
    # The type is a runtime fact too: on this build the shipped child is a POP,
    # which is why the offline index cannot answer this question.
    assert kids[0]["optype"].endswith("POP")


def test_the_torus_is_what_the_render_top_draws(probe):
    lit = (
        "d = op(%r)\n"
        "for n in ('gcam', 'glight', 'grend'):\n"
        "    o = d.op(n)\n"
        "    if o is not None:\n"
        "        o.destroy()\n"
        "cam = d.create(cameraCOMP, 'gcam')\n"
        "cam.par.tz = 5\n"
        "d.create(lightCOMP, 'glight')\n"
        # Its own untouched COMP, so this does not read whatever an earlier
        # test happened to leave behind.
        "if d.op('freshgeo') is None:\n"
        "    d.create(geometryCOMP, 'freshgeo')\n"
        "r = d.create(renderTOP, 'grend')\n"
        "r.par.camera = 'gcam'\n"
        "r.par.geometry = 'freshgeo'\n"
        "r.par.lights = 'glight'\n"
        "r.par.resolutionw, r.par.resolutionh = 128, 128\n"
        "r.cook(force=True)\n"
        "result = int((r.numpyArray(delayed=False)[:, :, 3] > 0.5).sum())\n" % ROOT
    )
    drawn = _exec(probe, lit)
    assert drawn > 0, "a fresh Geometry COMP rendered nothing at all"

    empty = _exec(
        probe,
        "g = op(%r + '/freshgeo')\n"
        "for k in list(g.children):\n"
        "    k.destroy()\n"
        "r = op(%r + '/grend')\n"
        "r.cook(force=True)\n"
        "result = int((r.numpyArray(delayed=False)[:, :, 3] > 0.5).sum())\n"
        % (ROOT, ROOT),
    )
    assert empty == 0, (
        "the same render kept %d pixels after the COMP was emptied, so the "
        "torus was not what it was drawing" % empty
    )


def test_a_sop_created_inside_a_geometry_comp_arrives_invisible(probe):
    probe.call("op_create", parent=ROOT, type="geometryCOMP", name="flaggeo",
               owner=OWNER)
    probe.call("op_create", parent=ROOT + "/flaggeo", type="sphereSOP",
               name="mysop", owner=OWNER)
    mine = probe.call("flags", path=ROOT + "/flaggeo/mysop")["ops"][0]["flags"]
    theirs = probe.call("flags", path=ROOT + "/flaggeo/torus1")["ops"][0]["flags"]
    assert mine["display"] is False and mine["render"] is False
    # The mirror image is the whole point: the geometry you did not create is
    # the visible one.
    assert theirs["display"] is True and theirs["render"] is True


def test_td_network_at_its_default_depth_still_says_the_comp_is_not_empty(probe):
    children = probe.call("network", path=ROOT)["children"]
    row = next(c for c in children if c["name"] == "flaggeo")
    assert row["numChildren"] >= 1
    assert not any(c["path"].startswith(ROOT + "/flaggeo/") for c in children)


# -- numpyArray is not stale ------------------------------------------------

_HASH_READ = (
    "import hashlib\n"
    "c = op(%(root)r + '/nsrc')\n"
    "t = op(%(root)r + '/nlvl')\n"
    "c.par.colorr = %(value)r\n"
    "t.cook(force=True)\n"
    "a = t.numpyArray(delayed=False)\n"
    "result = {'hash': hashlib.sha1(a.tobytes()).hexdigest(),\n"
    "          'png': hashlib.sha1(bytes(t.saveByteArray('.png'))).hexdigest(),\n"
    "          'px': round(float(a[0, 0, 0]), 3)}\n"
)


def test_a_numpy_read_follows_a_parameter_change_made_in_an_earlier_request(probe):
    _exec(
        probe,
        "d = op(%r)\n"
        "c = d.create(constantTOP, 'nsrc')\n"
        "c.par.resolutionw, c.par.resolutionh = 64, 64\n"
        "t = d.create(levelTOP, 'nlvl')\n"
        "t.inputConnectors[0].connect(c)\n"
        "result = 1\n" % ROOT,
    )
    reads = [
        _exec(probe, _HASH_READ % {"root": ROOT, "value": value})
        for value in (0.1, 0.5, 0.9)
    ]
    assert [r["px"] for r in reads] == [0.102, 0.502, 0.898]
    assert len({r["hash"] for r in reads}) == 3, (
        "three different colours read back as %d distinct arrays" % len(
            {r["hash"] for r in reads})
    )
    # The other code path — the one td_render uses — has to move with it, or
    # "judge pixels by rendering instead" would be advice with no basis.
    assert len({r["png"] for r in reads}) == 3


def test_the_same_change_read_back_inside_one_request_is_not_stale_either(probe):
    seen = _exec(
        probe,
        "import hashlib\n"
        "c = op(%r + '/nsrc')\n"
        "t = op(%r + '/nlvl')\n"
        "out = []\n"
        "for v in (0.2, 0.8):\n"
        "    c.par.colorr = v\n"
        "    t.cook(force=True)\n"
        "    out.append(hashlib.sha1(t.numpyArray(delayed=False).tobytes()).hexdigest())\n"
        "result = out\n" % (ROOT, ROOT),
    )
    assert len(set(seen)) == 2


def test_time_does_not_advance_inside_one_request(probe):
    """The measured grain of truth under the stale-frame report."""
    inside = _exec(
        probe,
        "import hashlib\n"
        "d = op(%r)\n"
        "a = d.op('nanim')\n"
        "if a is None:\n"
        "    a = d.create(noiseTOP, 'nanim')\n"
        "    a.par.resolutionw, a.par.resolutionh = 64, 64\n"
        "    a.par.type = 'simplex3d'\n"
        "    a.par.tz.expr = 'absTime.frame*0.1'\n"
        "out = []\n"
        "for _ in range(4):\n"
        "    a.cook(force=True)\n"
        "    out.append(hashlib.sha1(a.numpyArray(delayed=False).tobytes()).hexdigest())\n"
        "result = {'hashes': out, 'frames': [absTime.frame]}\n" % ROOT,
    )
    assert len(set(inside["hashes"])) == 1, (
        "the clock moved inside a single request, which it must not"
    )

    across = []
    for _ in range(3):
        across.append(
            _exec(
                probe,
                "import hashlib\n"
                "a = op(%r + '/nanim')\n"
                "a.cook(force=True)\n"
                "result = hashlib.sha1(a.numpyArray(delayed=False).tobytes()).hexdigest()\n"
                % ROOT,
            )
        )
        time.sleep(0.1)
    assert len(set(across)) == 3, (
        "one read per request gave %d distinct frames, not 3" % len(set(across))
    )


def test_a_render_of_a_sop_whose_flags_are_off_is_the_constant_the_session_saw(probe):
    """The symptom reproduces — as a picture that never changed, not a stale read."""
    build = (
        "d = op(%r)\n"
        "for n in ('bgeo', 'brend'):\n"
        "    o = d.op(n)\n"
        "    if o is not None:\n"
        "        o.destroy()\n"
        # Its own camera and light: without them the render is empty whatever
        # the flags say, and the measurement below would pass for the wrong
        # reason.
        "if d.op('gcam') is None:\n"
        "    d.create(cameraCOMP, 'gcam').par.tz = 5\n"
        "if d.op('glight') is None:\n"
        "    d.create(lightCOMP, 'glight')\n"
        "g = d.create(geometryCOMP, 'bgeo')\n"
        "for k in list(g.children):\n"
        "    k.destroy()\n"
        "s = g.create(sphereSOP, 'sph')\n"
        "n = g.create(noiseSOP, 'nse')\n"
        "n.inputConnectors[0].connect(s)\n"
        "n.par.amp = 0.6\n"
        "r = d.create(renderTOP, 'brend')\n"
        "r.par.camera = 'gcam'\n"
        "r.par.geometry = 'bgeo'\n"
        "r.par.lights = 'glight'\n"
        "r.par.resolutionw, r.par.resolutionh = 64, 64\n"
        "result = 1\n" % ROOT
    )
    _exec(probe, build)

    toggle = (
        "import hashlib\n"
        "n = op(%r + '/bgeo/nse')\n"
        "r = op(%r + '/brend')\n"
        "n.bypass = %%r\n"
        "r.cook(force=True)\n"
        "a = r.numpyArray(delayed=False)\n"
        "result = {'hash': hashlib.sha1(a.tobytes()).hexdigest(),\n"
        "          'lit': int((a[:, :, 3] > 0.5).sum())}\n" % (ROOT, ROOT)
    )
    off = [_exec(probe, toggle % state) for state in (False, True, False)]
    assert [r["lit"] for r in off] == [0, 0, 0]
    assert len({r["hash"] for r in off}) == 1, (
        "an invisible SOP still moved the frame, so the constant reading has "
        "some other cause"
    )

    _exec(probe, "n = op(%r + '/bgeo/nse')\nn.display = True\nn.render = True\n"
                 "result = 1\n" % ROOT)
    on = [_exec(probe, toggle % state) for state in (False, True, False)]
    assert on[0]["lit"] > 0 and on[1]["lit"] == 0, (
        "with the flags on, bypassing the SOP should empty the frame: %r" % on
    )
    assert on[0]["hash"] != on[1]["hash"]


# -- a cook dependency loop -------------------------------------------------

_LOOP = (
    "d = op(%(root)r)\n"
    "old = d.op('loop')\n"
    "if old is not None:\n"
    "    old.destroy()\n"
    "L = d.create(baseCOMP, 'loop')\n"
    "src = L.create(constantTOP, 'Lsrc')\n"
    "src.par.resolutionw, src.par.resolutionh = 64, 64\n"
    "src.par.colorr = 0.5\n"
    "comp = L.create(compositeTOP, 'Lcomp')\n"
    "comp.par.operand = 'maximum'\n"
    "grade = L.create(levelTOP, 'Lgrade')\n"
    "fb = L.create(feedbackTOP, 'Lfb')\n"
    "src.outputConnectors[0].connect(comp.inputConnectors[0])\n"
    "comp.outputConnectors[0].connect(grade.inputConnectors[0])\n"
    "fb.outputConnectors[0].connect(comp.inputConnectors[1])\n"
    "fb.par.top = 'Lcomp'\n"
    "grade.outputConnectors[0].connect(fb.inputConnectors[0])\n"
    "result = {k.name: k.totalCooks for k in L.children}\n"
)


def _loop_warnings(client):
    """What each tool says about the loop, `errors` asked first every time.

    `errors` is asked for this subtree by name. It used to be asked for the
    whole session, which worked only because this holder happens to sit near
    the top: from `/` the walk's node budget goes to TouchDesigner's own /ui
    and /sys, and anything deeper than about five path levels is never
    reached (measured 2026-09-07).
    """
    errors = client.call("errors", path=ROOT + "/loop")["nodes"]
    from_errors = [
        n["path"] for n in errors
        if n["warnings"] and "dependency loop" in n["warnings"]
        and n["path"].startswith(ROOT + "/loop/")
    ]
    children = client.call("network", path=ROOT + "/loop")["children"]
    from_network = [
        c["path"] for c in children
        if c["warnings"] and "dependency loop" in c["warnings"]
    ]
    return from_errors, from_network


def test_a_cook_loop_nothing_pulls_is_reported_by_neither_tool(probe):
    cooks = _exec(probe, _LOOP % {"root": ROOT})
    assert set(cooks.values()) == {0}, "something was already pulling the loop"
    from_errors, from_network = _loop_warnings(probe)
    assert from_errors == [] and from_network == [], (
        "a loop that has never cooked reported: %r / %r" % (from_errors, from_network)
    )


def test_once_it_cooks_td_errors_and_td_network_both_name_it(probe):
    if _exec(probe, "result = op(%r + '/loop') is None" % ROOT):
        _exec(probe, _LOOP % {"root": ROOT})
    _exec(
        probe,
        "L = op(%r + '/loop')\n"
        "k = L.op('Lkeep')\n"
        "if k is None:\n"
        "    k = L.create(cacheTOP, 'Lkeep')\n"
        "    k.par.cachesize = 1\n"
        "k.par.alwayscook = True\n"
        "L.op('Lgrade').outputConnectors[0].connect(k.inputConnectors[0])\n"
        "result = 1\n" % ROOT,
    )
    assert _wait_for_cooks(probe, ROOT + "/loop/Lgrade", 2) >= 2

    from_errors, from_network = _loop_warnings(probe)
    assert from_errors, "td_errors stayed silent about a loop that is cooking"
    assert from_errors == from_network, (
        "the two tools disagree: td_errors %r, td_network %r"
        % (from_errors, from_network)
    )
    # Asked twice, because "the first call cleared it" would look the same.
    assert _loop_warnings(probe) == (from_errors, from_network)


# -- op_create carries a DAT's text far enough for a shader to compile ------

_GOOD_SHADER = (
    "out vec4 fragColor;\n"
    "void main() {\n"
    "    fragColor = vec4(0.0, 1.0, 0.25, 1.0);\n"
    "}\n"
)
_BAD_SHADER = _GOOD_SHADER.replace("0.25, 1.0);", "0.25)")


def test_a_shader_delivered_at_creation_compiles_and_paints(probe):
    probe.call("op_create", parent=ROOT, type="textDAT", name="fragA",
               text=_GOOD_SHADER, owner=OWNER)
    probe.call("op_create", parent=ROOT, type="glslTOP", name="glA", owner=OWNER,
               pars={"pixeldat": "fragA", "resolutionw": 64, "resolutionh": 64})
    seen = _exec(
        probe,
        "g = op(%r + '/glA')\n"
        "g.cook(force=True)\n"
        "a = g.numpyArray(delayed=False)\n"
        "result = {'px': [round(float(x), 2) for x in a[0, 0]],\n"
        "          'compile': g.compileResult,\n"
        "          'warnings': g.warnings(recurse=False)}\n" % ROOT,
    )
    assert "compile errors" not in seen["compile"].lower(), seen["compile"]
    assert seen["px"] == [0.0, 1.0, 0.25, 1.0]
    assert not seen["warnings"]


def test_a_shader_that_does_not_compile_is_a_warning_naming_the_dat(probe):
    probe.call("op_create", parent=ROOT, type="textDAT", name="fragB",
               text=_BAD_SHADER, owner=OWNER)
    probe.call("op_create", parent=ROOT, type="glslTOP", name="glB", owner=OWNER,
               pars={"pixeldat": "fragB", "resolutionw": 64, "resolutionh": 64})
    seen = _exec(
        probe,
        "g = op(%r + '/glB')\n"
        "g.cook(force=True)\n"
        "result = {'errors': g.errors(recurse=False),\n"
        "          'warnings': g.warnings(recurse=False),\n"
        "          'compile': g.compileResult}\n" % ROOT,
    )
    assert seen["errors"] == "", "TouchDesigner started calling this an error"
    assert "compile errors" in seen["warnings"]
    assert "/fragB" in seen["compile"], seen["compile"]


def test_a_table_dat_takes_its_rows_from_the_same_text_key(probe):
    probe.call("op_create", parent=ROOT, type="tableDAT", name="tbl",
               text="a\tb\nc\td\n", owner=OWNER)
    cells = _exec(
        probe,
        "t = op(%r + '/tbl')\n"
        "result = [[c.val for c in row] for row in t.rows()]\n" % ROOT,
    )
    assert cells == [["a", "b"], ["c", "d"]]


def test_a_dat_that_recomputes_its_text_is_refused_rather_than_silently_lost(probe):
    with pytest.raises(Exception) as caught:
        probe.call("op_create", parent=ROOT, type="selectDAT", name="sel",
                   text="nope", owner=OWNER)
    assert "overwritten on the next cook" in str(caught.value)


# -- the parameter read-back names the member -------------------------------

def test_a_bad_expression_is_refused_naming_the_member_touchdesigner_will_not(probe):
    probe.call("op_create", parent=ROOT, type="geometryCOMP", name="mass",
               owner=OWNER)
    with pytest.raises(Exception) as caught:
        probe.call("par_set", path=ROOT + "/mass", owner=OWNER,
                   pars={"ty": {"expr": "0.25 * sin(absTime.seconds)"}})
    message = str(caught.value)
    # TouchDesigner's own text, quoted inside ours, and it names the group.
    assert "parameter t" in message
    assert "'ty' was written (mode EXPRESSION)" in message
    assert "does not name 'ty'" in message
    assert "math.sin" in message


def test_the_group_sentence_is_left_out_when_touchdesigner_did_name_the_member(probe):
    _exec(
        probe,
        "d = op(%r)\n"
        "x = d.op('xchop')\n"
        "if x is None:\n"
        "    lfo = d.create(lfoCHOP, 'lfo0')\n"
        "    x = d.create(expressionCHOP, 'xchop')\n"
        "    lfo.outputConnectors[0].connect(x.inputConnectors[0])\n"
        "result = 1\n" % ROOT,
    )
    with pytest.raises(Exception) as caught:
        probe.call("par_set", path=ROOT + "/xchop", owner=OWNER,
                   pars={"expr0expr": {"expr": "me.inputVal * 4.0"}})
    message = str(caught.value)
    assert "parameter expr0expr" in message
    assert "does not name" not in message, (
        "claimed a group substitution TouchDesigner did not make"
    )


def test_the_fixed_expression_goes_through(probe):
    applied = probe.call(
        "par_set", path=ROOT + "/mass", owner=OWNER,
        pars={"ty": {"expr": "0.25 * math.sin(absTime.seconds)"}},
    )["applied"]
    assert -0.25 <= applied["ty"] <= 0.25
