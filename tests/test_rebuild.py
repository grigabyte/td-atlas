"""Writing a network back: text -> the toeexpand tree -> a .toe/.tox.

Two layers are checked separately, because they fail differently.

The renderers are checked as an *inverse property* — `read(render(read(x)))
== read(x)` — on the shapes measured in the expansion cache, above all the
asymmetry where the same quoted constant is read one way with a mode bit set
and another way without it. Byte identity is deliberately not the property:
`toecollapse` packs bytes and does not care how a value was spelled, so
demanding it would fail on lines that are perfectly correct.

The whole trip is checked against a real shipped component, and skips without
a TouchDesigner installation like the other integration tests here — it shells
out to `toecollapse` and `toeexpand`. It never needs a *running* instance.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

from td_atlas.project import formats, rebuild
from td_atlas.project.formats import ParmValue


def _reread(name: str, parm: ParmValue) -> ParmValue:
    line = rebuild.render_parm_line(name, parm)
    parsed = formats.read_parms("?\n" + line + "\n?\n")
    assert list(parsed) == [name], f"the name did not survive: {line!r}"
    return parsed[name]


# -- the renderers, as inverses --------------------------------------------

# Every shape the cache holds, plus the ones that would break a naive writer.
# `flags` is the word the reader keys on, so the same value appears twice with
# different words on purpose: that pair is the asymmetry this module exists to
# survive.
_PARM_CASES = [
    ("plain", ParmValue(flags=0, value="1")),
    ("empty", ParmValue(flags=0, value="")),
    ("spaced", ParmValue(flags=0, value="two words")),
    # Read with `_unquote`: the backslash is content, not an escape.
    ("quotedconst", ParmValue(flags=0, value='say \\"hi\\"')),
    # The value that *is* a quoted string; writing it bare would strip it.
    ("selfquoted", ParmValue(flags=0, value='"quoted"')),
    ("doublequote", ParmValue(flags=0, value='""')),
    ("padded", ParmValue(flags=0, value="  padded  ")),
    # Read with `_take_field`: the same backslash is an escape.
    ("expr", ParmValue(flags=0x10, value="1", expr='op("x").par.v')),
    ("expr_empty", ParmValue(flags=0x10, value="", expr="")),
    ("expr_quotes", ParmValue(flags=0x10, value='has "quotes"', expr='"lead"')),
    ("expr_back", ParmValue(flags=0x10, value="c:\\path\\x", expr="a\\b")),
    ("bind", ParmValue(flags=0x200, value="0", bind="parent.C.par.Color1r")),
    ("both", ParmValue(flags=0x210, value="0", expr="me.time", bind="p.par.X")),
    ("bom", ParmValue(flags=0x10, value="\ufeffmarked", expr="\ufeff\"q\"")),
    ("unknown", ParmValue(flags=0x200010, value="v", unrecognised="tail tail")),
    # `_PARM_LINE` accepts a negative flags word; -1 has every bit, so the
    # `0x200000` branch takes it and the tail comes back unrecognised.
    ("negative", ParmValue(flags=-1, value="x", unrecognised="rest of it")),
]


@pytest.mark.parametrize("name,parm", _PARM_CASES, ids=[c[0] for c in _PARM_CASES])
def test_a_parm_line_survives_being_written_and_read(name, parm):
    assert _reread(name, parm) == parm


# Real lines from the expansion cache, in their exact spelling. Byte
# identity is not the correctness property — `toecollapse` does not care how a
# value is spelled — but it is what keeps a rebuild from rewriting lines
# nobody edited, so it is worth locking on the shapes that carry it: 415,090
# of the 472,699 lines in the cache come back byte for byte.
_VERBATIM = [
    "radiusx 0 0.2",
    "label 0 some words with spaces",
    "colorr 515 1 parent.Checker.par.Color1r",
    # 4,456 lines in the cache leave the last value unquoted *and* spaced.
    # Lexing it strictly instead would quote all of them for nothing.
    "alignorder 48 0 20000 - me.nodeY",
    "language 0 python",
]


@pytest.mark.parametrize("line", _VERBATIM)
def test_an_untouched_parameter_line_is_written_back_verbatim(line):
    name = line.split(" ", 1)[0]
    parm = formats.read_parms(line + "\n")[name]
    assert rebuild.render_parm_line(name, parm) == line


def test_the_reader_asymmetry_is_reproduced_in_both_directions():
    """The same constant, two flags words, two correct answers.

    Measured on 55 lines in the expansion cache (`Bodytext` in
    `alembicoutPOP/example2/comment1.parm` is one): with a mode bit the
    constant is unescaped on the way in, without one it is not. A writer that
    picks one spelling for both corrupts whichever half it did not pick.
    """
    source = '"say \\"hi\\""'
    loose = formats.read_parms(f"body 0 {source}\n")["body"]
    strict = formats.read_parms(f"body 16 {source} expr\n")["body"]
    assert loose.value == 'say \\"hi\\"'
    assert strict.value == 'say "hi"'

    assert _reread("body", loose) == loose
    assert _reread("body", strict) == strict
    # And the two really do render differently — a writer that emitted one
    # line for both would pass the two checks above only by accident.
    assert rebuild.render_parm_line("body", loose) != rebuild.render_parm_line(
        "body", strict
    )


_NODE = (
    "TOP:constant\n"
    "tile 465 -166 130 72\n"
    "flags =  current on viewer 1 parlanguage 0\n"
    "inputs\n"
    "{\n"
    "0 \tmono1\n"
    "}\n"
    "color 0.55 0.55 0.55 \n"
    "view 8 0 1 1 1 0 0 0 0 1 1\n"
    "end\n"
)


def test_an_unchanged_node_file_is_rewritten_byte_for_byte():
    node = formats.read_node(_NODE)
    assert rebuild.patch_node_file(_NODE, node) == _NODE


def test_a_node_file_keeps_lines_the_reader_does_not_understand():
    node = formats.read_node(_NODE)
    node.x = 10.0
    out = rebuild.patch_node_file(_NODE, node)
    assert "tile 10 -166 130 72" in out
    # `view` is not a line any reader in this project parses; losing it would
    # be a silent edit to somebody's file.
    assert "view 8 0 1 1 1 0 0 0 0 1 1" in out
    assert formats.read_node(out).x == 10.0


def test_node_wiring_flags_and_colour_are_written_back():
    node = formats.read_node(_NODE)
    node.inputs = [(0, "a"), (2, "b")]
    node.flags = {"viewer": "0"}
    node.color = (0.25, 0.5, 0.75)
    again = formats.read_node(rebuild.patch_node_file(_NODE, node))
    assert again.inputs == [(0, "a"), (2, "b")]
    assert again.flags == {"viewer": "0"}
    assert again.color == (0.25, 0.5, 0.75)


def test_a_node_flag_without_a_value_is_refused_rather_than_guessed():
    from td_atlas.project import ExpandError

    node = formats.read_node(_NODE)
    node.flags = {"viewer": ""}
    with pytest.raises(ExpandError):
        rebuild.patch_node_file(_NODE, node)


def test_dropping_an_input_leaves_an_empty_block_behind():
    node = formats.read_node(_NODE)
    node.inputs = []
    out = rebuild.patch_node_file(_NODE, node)
    assert "inputs\n{\n}\n" in out
    assert formats.read_node(out).inputs == []


# -- the .parm file as a whole ---------------------------------------------

_PARM_FILE = "?\nRes1 0 1280\nRes2 16 720 me.par.Res1\n?\n"


def test_only_the_changed_parameter_line_is_rewritten():
    parms = formats.read_parms(_PARM_FILE)
    assert rebuild.patch_parm_file(_PARM_FILE, parms) == _PARM_FILE

    parms["Res1"] = ParmValue(flags=0, value="1920")
    out = rebuild.patch_parm_file(_PARM_FILE, parms)
    assert "Res1 0 1920" in out
    assert "Res2 16 720 me.par.Res1" in out
    assert formats.read_parms(out) == parms


def test_parameters_can_be_added_and_removed():
    parms = formats.read_parms(_PARM_FILE)
    del parms["Res2"]
    parms["Aspect"] = ParmValue(flags=0, value="0.5")
    out = rebuild.patch_parm_file(_PARM_FILE, parms)
    assert formats.read_parms(out) == parms
    # The sentinels toeexpand writes are kept, and the new line goes inside.
    assert out.startswith("?\n") and out.rstrip().endswith("?")


def test_a_duplicated_name_patches_the_line_the_reader_keeps():
    """2 files of 46,452 in the cache name a parameter twice.

    `read_parms` keeps the last one, so that is the one a write has to change;
    patching the first would leave the file reading as it did before.
    """
    text = "?\nRes1 0 1\nRes1 0 2\n?\n"
    parms = formats.read_parms(text)
    assert parms["Res1"].value == "2"
    parms["Res1"] = ParmValue(flags=0, value="9")
    out = rebuild.patch_parm_file(text, parms)
    assert formats.read_parms(out)["Res1"].value == "9"


# -- payload and table writers ---------------------------------------------

def test_a_payload_keeps_its_prologue_and_declares_the_new_length():
    body = "print('hi')\nsecond line\n"
    original = rebuild.render_payload(b"x")
    out = rebuild.render_payload(body.encode(), original[:23])
    assert formats.read_payload(out) == body
    (declared,) = struct.unpack(">I", out[23:27])
    assert declared == len(body.encode())
    assert out[:23] == original[:23]


def test_a_table_round_trips_through_its_writer():
    rows = [["name", "value"], ["a", "1"], ["b", "two words"]]
    out = rebuild.render_table(rows)
    assert formats.read_table(out) == rows


def test_a_table_written_over_a_source_keeps_the_source_header_words():
    rows = [["a"], ["b"]]
    first = rebuild.render_table(rows)
    second = rebuild.render_table([["c"], ["d"]], first)
    assert second[:3] == first[:3]
    assert formats.read_table(second) == [["c"], ["d"]]


# -- applying a text to a tree ---------------------------------------------


def _tree(tmp_path: Path) -> Path:
    """A minimal expanded project: one base COMP holding one Text DAT."""
    root = tmp_path / "p.toe.dir"
    (root / "project1").mkdir(parents=True)
    (root / ".build").write_text("version 099\nbuild 2025.30000\n")
    (root / "project1.n").write_text(
        "COMP:base\ntile 0 0 160 130\nflags =  parlanguage 0\ncolor 0.5 0.5 0.5 \nend\n"
    )
    (root / "project1" / "text1.n").write_text(
        "DAT:text\ntile 5 5 130 90\nflags =  parlanguage 0\ncolor 0.5 0.5 0.5 \nend\n"
    )
    (root / "project1" / "text1.parm").write_text("?\nlanguage 0 python\n?\n")
    (root / "project1" / "text1.text").write_bytes(
        rebuild.render_payload(b"print(1)\n")
    )
    return root


def _loaded(root: Path):
    from td_atlas.project.expand import Expansion
    from td_atlas.project.model import load

    return load(Expansion(root.parent / "p.toe", root, None, True))


def _text_of(root: Path) -> dict:
    from td_atlas.project.serialize import project_data

    return json.loads(json.dumps(project_data(_loaded(root))))


def test_an_unedited_text_touches_nothing(tmp_path):
    root = _tree(tmp_path)
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    changes = rebuild.apply_text(root, _text_of(root), _loaded(root))
    assert changes.touched == 0
    assert changes.gaps == []
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == before


def test_edits_to_parameters_text_and_placement_land(tmp_path):
    root = _tree(tmp_path)
    data = _text_of(root)
    dat = data["operators"][0]["children"][0]
    dat["parms"]["language"] = "text"
    dat["text"] = ["print(2)", "print(3)", ""]
    dat["tile"] = [50.0, 60.0, 130.0, 90.0]
    changes = rebuild.apply_text(root, data, _loaded(root))
    assert changes.gaps == []

    after = _text_of(root)
    got = after["operators"][0]["children"][0]
    assert got["parms"]["language"] == "text"
    assert got["text"] == ["print(2)", "print(3)", ""]
    assert got["tile"] == [50.0, 60.0, 130.0, 90.0]


def test_the_flags_word_comes_from_the_source_line(tmp_path):
    """The text carries the mode, never the word — so it is read off the file.

    Bit 0x400 here stands for whatever else the parameter carried. A writer
    that rebuilt the word from the text's keys would silently drop it, which
    is the whole reason this module needs the original file.
    """
    root = _tree(tmp_path)
    (root / "project1" / "text1.parm").write_text("?\nlanguage 1024 python\n?\n")
    data = _text_of(root)
    data["operators"][0]["children"][0]["parms"]["language"] = "glsl"
    changes = rebuild.apply_text(root, data, _loaded(root))

    line = (root / "project1" / "text1.parm").read_text()
    assert "language 1024 glsl" in line
    assert changes.estimated_flags == 0


def test_a_node_with_no_flags_keeps_the_shorter_flags_line(tmp_path):
    """32 `.n` files in the cache write `flags = ` with one trailing space.

    Writing the two-space form there rewrites a file the text never asked to
    change, which is the same silent edit as any other.
    """
    text = "DAT:text\ntile 0 0 130 90\nflags = \ncolor 0.5 0.5 0.5 \nend\n"
    node = formats.read_node(text)
    assert node.flags == {}
    assert rebuild.patch_node_file(text, node) == text


def test_the_unknown_extra_bit_keeps_the_source_flags_word(tmp_path):
    """Bit `0x200000` marks the 13 lines whose layout was never measured.

    Its own presence is what decides how the line is read, so it has to be
    checked before the expression and bind bits — and carried across, since
    composing a word from the text's keys would drop it and change how the
    line parses next time.
    """
    root = _tree(tmp_path)
    (root / "project1" / "text1.parm").write_text(
        "?\nlanguage 2097168 python python\n?\n"
    )
    data = _text_of(root)
    parm = data["operators"][0]["children"][0]["parms"]["language"]
    assert parm["unrecognised"] == "python"
    parm["value"] = "glsl"
    changes = rebuild.apply_text(root, data, _loaded(root))

    assert changes.estimated_flags == 0
    assert changes.gaps == []
    written = formats.read_parms((root / "project1" / "text1.parm").read_text())
    assert written["language"].flags == 2097168
    assert written["language"].value == "glsl"
    assert written["language"].unrecognised == "python"


def test_changing_a_parameter_mode_is_reported_as_an_estimate(tmp_path):
    root = _tree(tmp_path)
    (root / "project1" / "text1.parm").write_text("?\nlanguage 1024 python\n?\n")
    data = _text_of(root)
    data["operators"][0]["children"][0]["parms"]["language"] = {
        "expr": "me.time.frame",
        "value": "python",
    }
    changes = rebuild.apply_text(root, data, _loaded(root))
    assert changes.estimated_flags == 1
    assert any("flags word was composed" in g for g in changes.gaps)
    # Still written, and still readable as the mode the text asked for.
    parms = formats.read_parms((root / "project1" / "text1.parm").read_text())
    assert parms["language"].mode == "expression"
    assert parms["language"].expr == "me.time.frame"


def test_deleting_an_operator_removes_every_file_it_owned(tmp_path):
    root = _tree(tmp_path)
    data = _text_of(root)
    data["operators"][0]["children"] = []
    changes = rebuild.apply_text(root, data, _loaded(root))
    assert changes.deleted == ["/project1/text1"]
    assert not list((root / "project1").glob("text1.*"))


def test_an_operator_the_source_lacks_is_a_named_gap_not_a_guess(tmp_path):
    """The text can ask for a node this module cannot build.

    A `.n` file needs a family and type line, and a component needs panel
    state and custom parameter definitions that the text does not carry, so
    the honest answer is to say so — the project invariant applied to its own
    write path.
    """
    root = _tree(tmp_path)
    data = _text_of(root)
    fresh = json.loads(json.dumps(data["operators"][0]["children"][0]))
    fresh["name"] = "text2"
    data["operators"][0]["children"].append(fresh)
    changes = rebuild.apply_text(root, data, _loaded(root))

    assert not (root / "project1" / "text2.n").exists()
    assert any("adds an operator" in g for g in changes.gaps)


def test_changing_an_operator_type_is_a_named_gap(tmp_path):
    root = _tree(tmp_path)
    data = _text_of(root)
    data["operators"][0]["children"][0]["type"] = "tableDAT"
    changes = rebuild.apply_text(root, data, _loaded(root))
    assert any("changes the operator type" in g for g in changes.gaps)
    assert (root / "project1" / "text1.n").read_text().startswith("DAT:text")


def test_a_subtree_dump_does_not_delete_the_rest_of_the_file(tmp_path):
    """A dump made with `--path` names part of the file, not all of it."""
    from td_atlas.project.serialize import project_data

    root = _tree(tmp_path)
    data = json.loads(json.dumps(project_data(_loaded(root), path="/project1/text1")))
    data["operators"][0]["tile"] = [7.0, 8.0, 130.0, 90.0]
    changes = rebuild.apply_text(root, data, _loaded(root))

    assert changes.deleted == []
    assert (root / "project1.n").exists()
    assert _text_of(root)["operators"][0]["children"][0]["tile"][0] == 7.0


def test_text_that_is_not_a_network_dump_is_refused(tmp_path):
    from td_atlas.project import ExpandError

    with pytest.raises(ExpandError, match="not a network dump"):
        rebuild.rebuild(tmp_path / "nope.toe", "{}", tmp_path / "out.toe")


# -- the whole trip, against a shipped component ---------------------------

def _installed():
    from td_atlas.install import InstallNotFound, discover

    try:
        return discover()
    except InstallNotFound:
        return None


needs_td = pytest.mark.skipif(
    _installed() is None, reason="no TouchDesigner installation"
)


def _palette(name: str) -> Path | None:
    install = _installed()
    if install is None:
        return None
    root = install.root / "Contents" / "Resources" / "tfs" / "Samples" / "Palette"
    if not root.is_dir():
        root = install.root / "Samples" / "Palette"
    found = sorted(root.rglob(name))
    return found[0] if found else None


@needs_td
def test_the_round_trip_invariant_holds_on_a_shipped_component(tmp_path):
    """Dump, build back, dump again — the contract's gate, end to end.

    The output keeps the source's file name on purpose: the dump records the
    file it came from, so renaming it would make the two texts differ for a
    reason that has nothing to do with the network.
    """
    from td_atlas.project.model import load_file
    from td_atlas.project.serialize import to_text

    source = _palette("checker.tox")
    if source is None:
        pytest.skip("palette not present")

    before = to_text(load_file(source))
    out = tmp_path / source.name
    changes = rebuild.rebuild(source, before, out)
    assert changes.gaps == []
    assert to_text(load_file(out, refresh=True)) == before


@needs_td
def test_an_edit_survives_the_round_trip_on_a_shipped_component(tmp_path):
    """The same gate with the text actually changed.

    An identity round trip alone would pass on a module that copied the file
    and ignored the text entirely, so the edit is the half that matters.
    """
    from td_atlas.project.model import load_file
    from td_atlas.project.serialize import dumps, to_text

    source = _palette("checker.tox")
    if source is None:
        pytest.skip("palette not present")

    data = json.loads(to_text(load_file(source)))

    edited = 0
    def walk(ops):
        nonlocal edited
        for op in ops:
            op["tile"] = [op["tile"][0] + 13.0] + op["tile"][1:]
            for name, value in list(op.get("parms", {}).items()):
                if isinstance(value, str) and not edited:
                    op["parms"][name] = value + " marked"
                    edited += 1
            if op.get("text"):
                op["text"] = list(op["text"]) + ["# marked"]
            walk(op.get("children") or [])

    walk(data["operators"])
    assert edited, "the fixture no longer has a plain constant to edit"

    wanted = dumps(data)
    out = tmp_path / source.name
    changes = rebuild.rebuild(source, wanted, out)
    assert changes.gaps == []
    assert changes.touched > 0
    assert to_text(load_file(out, refresh=True)) == wanted
