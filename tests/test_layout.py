"""Where a created node lands: no overlaps, and a batch reads left to right.

The defect these cover is not subtle. `op_create` assigned `nodeX`/`nodeY` only
when the caller passed a position, so every node an agent built without one sat
at (0, 0) — ten nodes stacked into one visible tile.

Nothing here needs TouchDesigner. The geometry is plain module-level arithmetic,
and the placement is exercised through `m_op_create` against a fake network
whose tiles carry the sizes measured on a live 2025.32460: noiseTOP 130x90,
outTOP 130x72, geometryCOMP 160x130. Those differences are the point — a single
assumed tile size lays out one of them wrongly.
"""

from __future__ import annotations

import pytest

from td_atlas.component import handler


# -- a network without TouchDesigner ----------------------------------------

# type -> (family, width, height), as read off live operators.
TILES = {
    "noiseTOP": ("TOP", 130, 90),
    "levelTOP": ("TOP", 130, 90),
    "outTOP": ("TOP", 130, 72),
    "geometryCOMP": ("COMP", 160, 130),
    "annotateCOMP": ("COMP", 382, 288),
}


class FakeConnector:
    def __init__(self, owner):
        self.owner = owner
        self.connected = []

    def connect(self, other):
        self.connected.append(other)
        other.owner.inputs.append(self.owner)


class FakeOP:
    def __init__(self, name, op_type="noiseTOP", parent=None):
        family, width, height = TILES[op_type]
        self.name = name
        self.OPType = op_type
        self.family = family
        self.nodeWidth = width
        self.nodeHeight = height
        self.nodeX = 0
        self.nodeY = 0
        self.valid = True
        self.parent_op = parent
        self.children = []
        self.inputs = []
        self.inputConnectors = [FakeConnector(self), FakeConnector(self)]
        self.outputConnectors = [FakeConnector(self)]

    @property
    def path(self):
        if self.parent_op is None:
            return "/" + self.name
        head = self.parent_op.path
        return ("" if head == "/" else head) + "/" + self.name

    def create(self, op_type, name=None):
        child = FakeOP(name or op_type, op_type, parent=self)
        self.children.append(child)
        return child

    def errors(self, recurse=False):
        return ""

    def warnings(self, recurse=False):
        return ""

    def pars(self):
        return []


class FakeUndo:
    def __init__(self):
        self.blocks = []
        self.depth = 0

    def startBlock(self, name, enable=True):
        self.blocks.append(name)
        self.depth += 1

    def endBlock(self):
        if self.depth == 0:
            raise RuntimeError("Cannot end non existent undo operation.")
        self.depth -= 1

    def undo(self):
        self.depth = 0


class FakeUI:
    def __init__(self):
        self.undo = FakeUndo()


@pytest.fixture
def network(monkeypatch):
    """An empty '/project1' reachable through the handler's `op` global."""
    root = FakeOP("", "geometryCOMP")
    root.name = ""
    project = root.create("geometryCOMP", "project1")

    def lookup(path):
        if path in ("/", ""):
            return root
        node = root
        for piece in path.strip("/").split("/"):
            found = None
            for child in node.children:
                if child.name == piece:
                    found = child
                    break
            if found is None:
                return None
            node = found
        return node

    monkeypatch.setattr(handler, "op", lookup, raising=False)
    monkeypatch.setattr(handler, "ui", FakeUI(), raising=False)
    monkeypatch.setattr(handler, "_guard_scopes", lambda params, *paths: None)
    return project


def create(parent, op_type, name=None, **params):
    request = {"parent": parent, "type": op_type, "name": name}
    request.update(params)
    return handler.m_op_create(request)


def box(node):
    return (node.nodeX, node.nodeY, node.nodeWidth, node.nodeHeight)


# -- the geometry -----------------------------------------------------------

def test_a_tile_grows_up_and_right_from_nodex_nodey():
    """nodeX/nodeY are the left and bottom edges, not the centre.

    Reading them as a centre puts every computed row half a tile out, and the
    mistake is invisible until two nodes touch.
    """
    node = FakeOP("n1", "noiseTOP")
    node.nodeX, node.nodeY = 100, 200
    assert handler._node_box(node) == (100.0, 200.0, 130.0, 90.0)


def test_two_tiles_on_the_same_spot_clash():
    one = (0.0, 0.0, 130.0, 90.0)
    assert handler._boxes_clash(one, one)


def test_tiles_closer_than_the_gap_clash_even_without_overlapping():
    """Touching tiles are as unreadable as overlapping ones."""
    one = (0.0, 0.0, 130.0, 90.0)
    just_touching = (130.0, 0.0, 130.0, 90.0)
    assert handler._boxes_clash(one, just_touching)
    clear = (130.0 + handler._LAYOUT_GAP_X, 0.0, 130.0, 90.0)
    assert not handler._boxes_clash(one, clear)


def test_a_tile_in_another_row_does_not_clash():
    one = (0.0, 0.0, 130.0, 90.0)
    below = (0.0, -90.0 - handler._LAYOUT_GAP_Y, 130.0, 90.0)
    assert not handler._boxes_clash(one, below)


def test_a_free_position_avoids_everything_it_was_given():
    boxes = [(0.0, 0.0, 130.0, 90.0), (170.0, 0.0, 130.0, 90.0)]
    x, y = handler._free_position(boxes, 130.0, 90.0, seed=(0.0, 0.0))
    candidate = (x, y, 130.0, 90.0)
    assert not any(handler._boxes_clash(candidate, box) for box in boxes)


def test_the_scan_is_bounded_and_still_lands_somewhere_free():
    """A crowded network must not turn one create into a long main-thread stall.

    The fallback past the right edge of everything is free by construction: no
    box reaches beyond its own right edge plus the gap.
    """
    # A tile on every position the scan will try, so the scan cannot succeed
    # and the fallback is the only thing left.
    step_x = 130.0 + handler._LAYOUT_GAP_X
    step_y = 90.0 + handler._LAYOUT_GAP_Y
    boxes = [
        (col * step_x, -row * step_y, 130.0, 90.0)
        for col in range(9)
        for row in range(handler._LAYOUT_MAX_PROBES // 8 + 1)
    ]
    x, y = handler._free_position(boxes, 130.0, 90.0, seed=(0.0, 0.0))
    candidate = (x, y, 130.0, 90.0)
    assert not any(handler._boxes_clash(candidate, box) for box in boxes)


def test_a_seed_with_a_source_sits_to_its_right_and_centred():
    """Centred rather than bottom-aligned, because tile heights differ."""
    source = FakeOP("src", "noiseTOP")  # 130 x 90
    source.nodeX, source.nodeY = 0, 0
    x, y = handler._placement_seed([], height=72.0, sources=[source])
    assert x == 130.0 + handler._LAYOUT_GAP_X
    assert y == pytest.approx((90.0 - 72.0) / 2.0)


# -- through m_op_create ----------------------------------------------------

def test_a_position_that_was_asked_for_is_the_position_it_gets(network):
    """The one behaviour placement must not touch: explicit coordinates.

    An agent that computed a layout of its own gets that layout, even where a
    node already sits — overriding it would be worse than an overlap.
    """
    sitting = network.create("noiseTOP", "sitting_there")
    sitting.nodeX, sitting.nodeY = 777, -333
    create("/project1", "noiseTOP", "exact", position=[777, -333])
    placed = [c for c in network.children if c.name == "exact"][0]
    assert (placed.nodeX, placed.nodeY) == (777.0, -333.0)


def test_two_nodes_created_without_a_position_do_not_overlap(network):
    """The defect itself: both used to stay at (0, 0)."""
    create("/project1", "noiseTOP", "a")
    create("/project1", "levelTOP", "b")
    a, b = network.children
    assert not handler._boxes_clash(box(a), box(b))
    assert a.nodeX < b.nodeX


def test_ten_nodes_created_without_a_position_all_stay_apart(network):
    for i in range(10):
        create("/project1", "noiseTOP", "n%d" % i)
    boxes = [box(child) for child in network.children]
    clashes = [
        (i, j)
        for i in range(len(boxes))
        for j in range(i + 1, len(boxes))
        if handler._boxes_clash(boxes[i], boxes[j])
    ]
    assert clashes == []


def test_a_wired_chain_reads_left_to_right(network):
    """What a batch of connected creates should look like in the editor."""
    create("/project1", "noiseTOP", "src")
    previous = "/project1/src"
    for index, op_type in enumerate(["levelTOP", "outTOP", "levelTOP"]):
        name = "step%d" % index
        create(
            "/project1",
            op_type,
            name,
            connect=[{"from": previous, "index": 0}],
        )
        previous = "/project1/" + name

    xs = [child.nodeX for child in network.children]
    assert xs == sorted(xs)
    assert len(set(xs)) == len(xs)
    boxes = [box(child) for child in network.children]
    assert not any(
        handler._boxes_clash(boxes[i], boxes[j])
        for i in range(len(boxes))
        for j in range(i + 1, len(boxes))
    )


def test_a_new_node_does_not_land_on_a_node_that_was_already_there(network):
    """The other half: a network the agent did not build is still in the way."""
    sitting = network.create("geometryCOMP", "geo1")
    sitting.nodeX, sitting.nodeY = 0, 0
    create("/project1", "noiseTOP", "new")
    fresh = [c for c in network.children if c.name == "new"][0]
    assert not handler._boxes_clash(box(sitting), box(fresh))


def test_a_sibling_whose_tile_cannot_be_read_is_skipped_not_fatal(network):
    """A node the read did not understand must not fail the create.

    The worst case is a tile the placement does not avoid, which beats
    refusing to build; with the only sibling unreadable the seed is free, so
    the new node lands on it.
    """
    broken = network.create("noiseTOP", "broken")
    del broken.nodeWidth
    handler.m_op_create({"parent": "/project1", "type": "noiseTOP", "name": "ok"})
    fresh = [c for c in network.children if c.name == "ok"][0]
    assert (fresh.nodeX, fresh.nodeY) == (0.0, 0.0)


def test_a_node_of_its_own_unreadable_size_is_left_alone(network):
    """No honest position can be computed for a tile with no size."""
    original_create = type(network).create

    def create_broken(self, op_type, name=None):
        child = original_create(self, op_type, name)
        del child.nodeWidth
        return child

    network.create = create_broken.__get__(network, type(network))
    handler.m_op_create({"parent": "/project1", "type": "noiseTOP", "name": "sizeless"})
    fresh = network.children[0]
    assert (fresh.nodeX, fresh.nodeY) == (0, 0)
