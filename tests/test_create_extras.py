"""A create says what TouchDesigner made besides it, and a write says what did not resolve.

Measured 2026-09-29 on 2025.32460: a GLSL TOP arrives with docked _pixel,
_info and _compute DATs, a Script CHOP with _callbacks even when `callbacks`
is given in the same create, a Geometry COMP with a torus1 holding the render
and display flags; an OP parameter written '../wire' or './wire' reads back
None while 'wire' finds the sibling. Agents cleaned up some ten DATs a session
by hand and rendered grey for an hour on '../wire' (reports 4 and 5).
"""

from __future__ import annotations

from td_atlas.component import handler
from td_atlas.mcp.server import _step_notes


class Par:
    def __init__(self, name, value=None, is_op=False):
        self.name, self.value, self.isOP = name, value, is_op

    def eval(self):
        return self.value


class Pars:
    pass


class Node:
    def __init__(self, path, optype, family="TOP", pars=(), docked=(),
                 children=(), render=None, is_comp=False):
        self.path, self.OPType, self.family = path, optype, family
        self.par = Pars()
        self._pars = list(pars)
        for par in pars:
            setattr(self.par, par.name, par)
        self.docked = list(docked)
        self.children = list(children)
        self.isCOMP = is_comp
        if render is not None:
            self.render = render

    def pars(self):
        return self._pars


def test_docked_dats_are_named_with_whether_the_node_points_at_them():
    pixel = Node("/p/g_pixel", "textDAT", "DAT")
    info = Node("/p/g_info", "infoDAT", "DAT")
    glsl = Node("/p/g", "glslTOP", pars=[Par("pixeldat", pixel, is_op=True)],
                docked=[pixel, info])
    extra = handler._after_create(glsl, Node("/p", "baseCOMP", "COMP"))
    assert extra["alsoCreated"] == [
        {"path": "/p/g_pixel", "type": "textDAT", "used": True},
        {"path": "/p/g_info", "type": "infoDAT", "used": False},
    ]


def test_a_pop_in_a_geometry_comp_says_who_holds_the_render_flag():
    torus = Node("/p/geo/torus1", "torusPOP", "POP", render=True)
    geo = Node("/p/geo", "geometryCOMP", "COMP", children=[torus], is_comp=True)
    box = Node("/p/geo/box", "boxPOP", "POP", render=False)
    geo.children.append(box)
    extra = handler._after_create(box, geo)
    assert extra["render"] is False
    assert extra["renderFlagOn"] == ["/p/geo/torus1"]


def test_an_op_reference_that_reads_back_none_is_reported():
    geo = Node("/p/geo", "geometryCOMP", "COMP",
               pars=[Par("material", None, is_op=True), Par("tx", 0.0)])
    assert handler._unresolved_refs(geo, {"material": "../wire", "tx": 1}) == {
        "material": "../wire"}
    # An empty value is a cleared reference, not a broken one.
    assert handler._unresolved_refs(geo, {"material": ""}) == {}


def test_the_host_prints_one_line_per_thing_done_besides_the_ask():
    notes = _step_notes({
        "path": "/p/sc",
        "alsoCreated": [{"path": "/p/sc_callbacks", "type": "textDAT",
                         "used": False}],
        "unresolved": {"material": "../wire"},
        "render": False, "renderFlagOn": ["/p/geo/torus1"],
    })
    assert any("leftovers" in n and "/p/sc_callbacks" in n for n in notes)
    assert any("'../wire' points at nothing" in n for n in notes)
    assert any("render flag is off" in n and "torus1" in n for n in notes)
    assert _step_notes({"path": "/p/x"}) == []
