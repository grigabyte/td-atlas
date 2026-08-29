"""How far the live network text is from the same network read out of a file.

Item 19 writes the network text from the *live* operators; `td-atlas project
text` prints it from the *expanded file*. The two are not equal, and until this
was measured only one class of difference had a name.

Two halves:

- The classifier (`live_network.classify`) is pure and runs everywhere. Its
  tests are the ones that keep the class names honest.
- The measurement builds the network of `live_network.BUILD_CODE` inside a
  running TouchDesigner, saves it with `save_tox`, reads the file back with the
  offline reader and asserts that every differing field falls into a named
  class. It needs a bridge and skips without one; it builds under
  `/tdatlas/difftest` and destroys it again, and it never saves the project.
  Set `TD_ATLAS_NO_LIVE=1` to skip it even where a bridge is reachable.
"""

from __future__ import annotations

import json

import pytest

import live_network as ln


HOLDER = "/tdatlas"
NAME = "difftest"


# -- the classifier, no TouchDesigner ---------------------------------------

def _diff(path, field, live, file):
    return {"path": path, "field": field, "live": live, "file": file}


def test_a_custom_parameter_read_from_the_file_as_a_built_in_one_is_one_class():
    diffs = [
        _diff("/d", "custom_parms.Amount", "0.75", None),
        _diff("/d", "parms.Amount", None, "0.75"),
    ]
    buckets = ln.classify(diffs, "/d")
    assert len(buckets[ln.CUSTOM_PLACEMENT]) == 2
    assert buckets["unexplained"] == []


def test_a_custom_parameter_the_file_does_not_carry_at_all_is_another_class():
    buckets = ln.classify([_diff("/d", "custom_parms.Opacity", "1", None)], "/d")
    assert len(buckets[ln.CUSTOM_DEFAULT]) == 1
    assert buckets[ln.CUSTOM_PLACEMENT] == []


def test_a_parameter_only_the_file_has_is_not_confused_with_a_misplaced_one():
    # No custom twin, so it is the file carrying a line for a parameter the
    # live side calls default — not the placement class.
    buckets = ln.classify([_diff("/d/x", "parms.iop1op", None, "")], "/d")
    assert len(buckets[ln.DEFAULT_PARM]) == 1
    assert buckets[ln.CUSTOM_PLACEMENT] == []


def test_the_root_s_external_tox_switch_is_charged_to_the_save_not_to_the_text():
    buckets = ln.classify(
        [_diff("/d", "parms.enableexternaltox", None, "off")], "/d"
    )
    assert len(buckets[ln.TOX_ROOT]) == 1
    assert buckets[ln.DEFAULT_PARM] == []
    # The same parameter one level down is not the save's doing.
    deeper = ln.classify([_diff("/d/x", "parms.enableexternaltox", None, "off")], "/d")
    assert len(deeper[ln.DEFAULT_PARM]) == 1
    assert deeper[ln.TOX_ROOT] == []


def test_wiring_only_the_live_side_has_is_the_one_wiring_class_left():
    only_live = ln.classify([_diff("/d/c", "inputs", [[0, "a/out1"]], [])], "/d")
    assert len(only_live[ln.COMP_WIRE]) == 1
    # The other direction was the panel wiring class until 2026-08-29, when
    # the live gather learned to read `inputCOMPConnectors`. A wire the file
    # has and the live side does not is now nobody's class.
    only_file = ln.classify([_diff("/d/c", "inputs", [], [[0, "spacer"]])], "/d")
    assert len(only_file["unexplained"]) == 1
    # Wiring that differs in content, not in presence, is nobody's class.
    both = ln.classify([_diff("/d/c", "inputs", [[0, "a"]], [[0, "b"]])], "/d")
    assert len(both["unexplained"]) == 1


def test_a_transposed_table_is_no_longer_explained_away():
    """It was a class of its own until the reader was fixed.

    The `.table` header's row and column counts were read the wrong way round,
    so a 3x2 table came back as 2x3 with the cells in the same order — and the
    round trip never noticed, because the writer repeated the same swap. Now a
    table that differs in shape is a real difference again, not a known class.
    """
    transposed = ln.classify(
        [_diff("/d/t", "table", [["a", "b"], ["c", "d"]], [["a", "b", "c", "d"]])],
        "/d",
    )
    assert len(transposed["unexplained"]) == 1


def test_a_float_written_with_an_exponent_is_the_formatting_class():
    buckets = ln.classify([_diff("/d/x", "parms.rmax", "2000000", "2e+06")], "/d")
    assert len(buckets[ln.FLOAT_FORMAT]) == 1
    # Numbers that are not the same number are not a formatting difference.
    other = ln.classify([_diff("/d/x", "parms.rmax", "2000000", "3e+06")], "/d")
    assert len(other["unexplained"]) == 1


def test_a_flag_the_live_gather_does_not_know_is_the_vocabulary_class():
    buckets = ln.classify(
        [_diff("/d/x", "flags", {"viewer": "1"},
               {"viewer": "1", "showDocked": "off"})],
        "/d",
    )
    assert len(buckets[ln.FLAG_VOCABULARY]) == 1
    # A flag both sides know, with different values, is not vocabulary.
    disagree = ln.classify(
        [_diff("/d/x", "flags", {"bypass": "on"}, {"bypass": "off"})], "/d"
    )
    assert len(disagree["unexplained"]) == 1


def test_every_class_the_documentation_names_has_a_constant_here():
    assert len(ln.CLASSES) == 7
    assert len(set(ln.CLASSES)) == 7


def test_compare_counts_one_parameter_as_one_field_not_one_map_as_one_field():
    def node(parms):
        return {"name": "x", "type": "noiseTOP", "family": "TOP", "tile": [0, 0, 1, 1],
                "flags": {}, "color": None, "inputs": [], "parms": parms,
                "custom_parms": {}, "text": None, "table": None, "children": []}

    report = ln.compare(node({"a": "1", "b": "2"}), node({"a": "1", "b": "3"}))
    assert len(report["diffs"]) == 1
    assert report["diffs"][0]["field"] == "parms.b"
    # Ten fields that are one field each — name, type, family, tile, flags,
    # color, inputs, custom_pages, text, table — plus one per parameter in the
    # map that differs, plus the one that agrees whole (`custom_parms`).
    assert report["fields"] == 13


def test_a_node_on_one_side_only_is_reported_rather_than_skipped():
    def node(name, children):
        return {"name": name, "type": "baseCOMP", "family": "COMP",
                "tile": [0, 0, 1, 1], "flags": {}, "color": None, "inputs": [],
                "parms": {}, "custom_parms": {}, "text": None, "table": None,
                "children": children}

    report = ln.compare(node("d", [node("kid", [])]), node("d", []))
    assert [d["field"] for d in report["diffs"]] == ["*node*"]
    assert report["diffs"][0]["live"] == "present"


# -- the measurement, with a running TouchDesigner --------------------------

@pytest.fixture(scope="module")
def measured(tmp_path_factory):
    """Build, save, read back and diff. Destroys what it built, always."""
    client = ln.bridge_or_skip()
    from td_atlas.project import model, serialize

    client.call("exec", **ln.build_params(HOLDER, NAME))
    path = "%s/%s" % (HOLDER, NAME)
    try:
        tox = str(tmp_path_factory.mktemp("difftest") / "difftest.tox")
        client.call("save_tox", path=path, file=tox)
        live = json.loads(
            client.call("exec", code="json.dumps(live_node_data(op(%r)))" % path)[
                "result"
            ]
        )
        project = model.load_file(tox, resolver=model.index_resolver())
        file_root = serialize.project_data(project)["operators"][0]
        report = ln.compare(live, file_root)
        report["buckets"] = ln.classify(report["diffs"], "/" + NAME)
        yield report
    finally:
        client.call("exec", code="op(%r).destroy()" % path)


def test_no_field_differs_for_a_reason_nobody_has_named(measured):
    unexplained = measured["buckets"]["unexplained"]
    assert unexplained == [], "\n".join(
        "%s %s live=%r file=%r" % (d["path"], d["field"], d["live"], d["file"])
        for d in unexplained
    )


def test_the_network_still_exercises_every_class_the_documentation_names(measured):
    """The class list is only honest while the network still produces it.

    An empty class here means the network stopped covering it — not that the
    difference went away — and the documented list would then be a claim
    nothing measures.
    """
    empty = [name for name in ln.CLASSES if not measured["buckets"][name]]
    assert empty == []


def test_most_of_the_two_texts_agree(measured):
    """The measured shape of the disagreement, so a regression is visible.

    Measured on 2025.32460, macOS: 722 fields compared, 618 equal, 104 in the
    seven classes. The assertion is deliberately loose about the exact numbers —
    a TouchDesigner build that ships a different annotate component moves them —
    and tight about the shape: the great majority of fields agree.
    """
    assert measured["fields"] > 500
    assert measured["same"] > 0.8 * measured["fields"]
