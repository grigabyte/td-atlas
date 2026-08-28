"""Tests for reading .toe/.tox files.

The unit tests run anywhere. The integration tests need a TouchDesigner
installation, because they exercise the real `toeexpand` against the example
libraries it ships, and skip when there is none.
"""

from __future__ import annotations

import json
import struct

import pytest

from td_atlas.project.diff import diff
from td_atlas.project.formats import (
    FormatError,
    read_custom_parms,
    read_node,
    read_parms,
    read_payload,
    read_table,
)
from td_atlas.project.model import Node, Project
from td_atlas.project.render import describe, grep


def payload(body: bytes, version: bytes = b"2") -> bytes:
    """Build a .text file the way toeexpand writes one."""
    return version + b"\n*" + struct.pack(">6I", 1, 1, 1, 1, 2, len(body)) + body


# -- payload files ----------------------------------------------------------

def test_read_payload_uses_the_length_field_not_the_first_newline():
    # A length whose low byte is not 0x0A is the case that breaks newline
    # scanning: here the header ends ...\x00\x00\x016 with no newline at all.
    body = b"// shader\nvoid main() {}\n" * 12
    assert len(body) == 0x12C
    assert read_payload(payload(body)) == body.decode()


def test_read_payload_keeps_content_that_starts_with_a_newline():
    body = b"\n// leading blank line\n"
    assert read_payload(payload(body)) == body.decode()


def test_read_payload_rejects_a_non_payload_file():
    with pytest.raises(FormatError):
        read_payload(b"just some text")


def test_read_payload_survives_invalid_utf8():
    assert "�" in read_payload(payload(b"caf\xff"))


def test_read_table_splits_cells_into_rows():
    cells = [b"a", b"b", b"c", b"d"]
    body = b"".join(struct.pack(">2I", 2, len(c)) + c for c in cells)
    data = b"1\n*" + struct.pack(">4I", 1, 2, 2, 0) + body
    assert read_table(data) == [["a", "b"], ["c", "d"]]


# -- node files -------------------------------------------------------------

_N = """TOP:constant
tile 465 -166 130 72
flags =  current on viewer 1 parlanguage 0
inputs
{
0 \tmono1
1 \tblur2
}
color 0.55 0.55 0.55
end
"""


def test_read_node_extracts_type_position_and_wiring():
    node = read_node(_N)
    assert node.family == "TOP"
    assert node.op_type == "constantTOP"
    assert (node.x, node.y) == (465.0, -166.0)
    assert node.inputs == [(0, "mono1"), (1, "blur2")]
    assert "current" in node.flags


def test_read_node_handles_a_node_with_no_inputs():
    node = read_node("COMP:base\ntile 0 0 160 129\nend\n")
    assert node.op_type == "baseCOMP"
    assert node.inputs == []


# -- parameter files --------------------------------------------------------

def test_read_parms_reads_a_plain_constant():
    parm = read_parms("?\nradiusx 0 0.2\n?")["radiusx"]
    assert not parm.is_expression
    assert parm.effective == "0.2"


def test_read_parms_keeps_quotes_inside_an_expression():
    # Stripping these would hand back Python that no longer parses.
    parm = read_parms("?\nfillcolorr 17 1 op('circle_red').par.value0\n?")
    assert parm["fillcolorr"].is_expression
    assert parm["fillcolorr"].effective == "op('circle_red').par.value0"
    assert parm["fillcolorr"].value == "1"


def test_read_parms_unwraps_a_quoted_expression():
    parm = read_parms('?\nsize 49 7 "me.inputs[0].width * .1"\n?')["size"]
    assert parm.effective == "me.inputs[0].width * .1"
    assert parm.value == "7"


def test_read_parms_unescapes_a_quoted_expression():
    line = '?\nBody 81 "" "said \\"hi\\" and \\\'bye\\\'"\n?'
    assert read_parms(line)["Body"].effective == "said \"hi\" and 'bye'"


# -- parameter modes: which halves a line carries ---------------------------
#
# One line per bit combination that the expansion cache actually contains
# (222 components, 474,656 lines). The combinations are: no bit, 0x10 alone,
# 0x200 alone, 0x10|0x200, and 0x200000.

def test_read_parms_splits_a_bind_expression_off_the_constant():
    """The evidence line: bind mode with no expression bit.

    `/checker/checker/color1` in the shipped `checker.tox`. Flags 515 is
    0x203 — bind, no 0x10 — and reading the halves off the shape of the line
    instead of the flags word reported the parameter as holding the constant
    and the bind expression glued together.
    """
    parm = read_parms("?\ncolorr 515 1 parent.Checker.par.Color1r\n?")["colorr"]
    assert parm.value == "1"
    assert parm.bind == "parent.Checker.par.Color1r"
    assert parm.expr is None
    assert parm.mode == "bind"
    assert parm.effective == "parent.Checker.par.Color1r"


def test_read_parms_reads_a_bind_expression_that_is_a_chop_channel():
    """A bind master need not be a parameter — 26 lines in the cache are not.

    `op('bind1')['chan1']` is a Bind CHOP channel, which the index's
    `Binding` article names as one of the things allowed to be a bind master.
    """
    line = "?\nValue0 67109443 0.43 op('bind1')['chan1']\n?"
    parm = read_parms(line)["Value0"]
    assert parm.value == "0.43"
    assert parm.bind == "op('bind1')['chan1']"


def test_read_parms_reads_a_constant_an_expression_and_a_bind_together():
    """547 lines in the cache carry both bits and three values."""
    line = (
        "?\nfontsize 561 20 \"parent.Widget.par.Labelfontsize.eval() or 1\" "
        "op('bg').par.fontsize\n?"
    )
    parm = read_parms(line)["fontsize"]
    assert parm.value == "20"
    assert parm.expr == "parent.Widget.par.Labelfontsize.eval() or 1"
    assert parm.bind == "op('bg').par.fontsize"
    # Bind outranks expression when both bits are set; both halves are kept.
    assert parm.mode == "bind"


def test_read_parms_admits_it_cannot_split_the_unknown_layout():
    """Bit 0x200000: one value more than the mode bits account for.

    13 lines in the cache carry it, and in every one the extra value is
    identical to the one before it — so nothing can say which position is the
    expression and which the bind. The reader reports the constant and hands
    the rest back unsplit rather than guessing.
    """
    line = (
        "?\nAngleofview 69206592 210 op('./float1').par.Value0 "
        "op('./float1').par.Value0\n?"
    )
    parm = read_parms(line)["Angleofview"]
    assert parm.value == "210"
    assert parm.expr is None and parm.bind is None
    assert parm.unrecognised == (
        "op('./float1').par.Value0 op('./float1').par.Value0"
    )
    assert parm.mode == "unknown"


def test_read_parms_does_not_invent_a_pair_without_a_mode_bit():
    """A constant with spaces stays one value, whatever it looks like."""
    parm = read_parms("?\nlabel 0 1 parent.Thing.par.Value\n?")["label"]
    assert parm.value == "1 parent.Thing.par.Value"
    assert parm.expr is None and parm.bind is None
    assert parm.mode == "constant"


def test_read_parms_keeps_a_byte_order_mark_out_of_the_constant():
    """`<BOM>"..."` is still one quoted field, not a fragment plus debris.

    6 lines in the cache write a BOM before an expression's opening quote.
    Without this the constant comes back as `\ufeff"[x` — a piece of a value.
    """
    line = '?\nexpr 49 7 \ufeff"[x.name for x in op(\'a\').points]"\n?'
    parm = read_parms(line)["expr"]
    assert parm.value == "7"
    assert parm.expr == "\ufeff[x.name for x in op('a').points]"


# -- .cparm is a different grammar ------------------------------------------

def test_read_custom_parms_reads_the_page_header_as_page_names():
    """`pages 4 ...` is a page list; the number is a count, not flags.

    Read with the `.parm` grammar the component gains a parameter named
    `pages` whose value is the four page names glued into one string.
    """
    text = (
        '?\npages 4 Text Settings "OP Viewer" About\n'
        '772804868 Version Version 1 1 0 0 1 1 1 2 0 2.3.4 "" About 1\n?'
    )
    custom = read_custom_parms(text)
    assert custom.pages == ["Text", "Settings", "OP Viewer", "About"]
    # The definition lines are not read at all — an admitted blank.
    assert custom.parms == {}


def test_read_parms_keeps_spaces_in_a_constant():
    parm = read_parms("?\nlabel 0 some words here\n?")["label"]
    assert parm.effective == "some words here"


def test_read_parms_tolerates_an_unterminated_quote():
    assert read_parms('?\nx 17 "" "unclosed\n?')["x"].effective == "unclosed"


# -- model and rendering ----------------------------------------------------

def _node(path: str, n_text: str, parms: str = "", text: str | None = None):
    node = Node(path=path, name=path.rsplit("/", 1)[-1], node=read_node(n_text))
    node.parms = read_parms(parms) if parms else {}
    node.text = text
    return node


@pytest.fixture
def project(tmp_path):
    root = _node("/project1", "COMP:container\nend\n")
    noise = _node(
        "/project1/noise1",
        "TOP:noise\ntile 0 0 1 1\nend\n",
        "?\nperiod 0 2.5\n?",
    )
    blur = _node(
        "/project1/blur1",
        "TOP:blur\ntile 5 5 1 1\ninputs\n{\n0 \tnoise1\n}\nend\n",
        "?\nsize 0 4\n?",
    )
    script = _node(
        "/project1/script1",
        "DAT:text\nend\n",
        text="import math\nprint(math.pi)\n",
    )
    root.children = [noise, blur, script]
    return Project(source=tmp_path / "demo.toe", build={"build": "2025.1"}, roots=[root])


def test_wiring_resolves_to_absolute_paths(project):
    assert project.find("/project1/blur1").input_paths() == ["/project1/noise1"]


def test_describe_shows_structure_and_wiring(project):
    out = describe(project, params=True)
    assert "noise1 (noiseTOP)" in out
    assert "blur1 (blurTOP)  <- /project1/noise1" in out
    assert ".period = 2.5" in out


def test_grep_searches_inside_dat_contents(project):
    matches = grep(project, r"math\.\w+")
    assert [(m.node_path, m.line_number) for m in matches] == [
        ("/project1/script1", 2)
    ]
    assert matches[0].line == "print(math.pi)"
    assert matches[0].op_type == "textDAT"


def test_grep_rejects_a_bad_pattern(project):
    with pytest.raises(ValueError):
        grep(project, "(unclosed")


# -- diff -------------------------------------------------------------------

def test_diff_reports_nothing_for_identical_projects(project):
    assert diff(project, project).empty


def test_diff_reports_added_removed_and_changed(project, tmp_path):
    after = Project(source=tmp_path / "b.toe", build=project.build, roots=[])
    root = _node("/project1", "COMP:container\nend\n")
    noise = _node(
        "/project1/noise1",
        "TOP:noise\ntile 0 0 1 1\nend\n",
        "?\nperiod 0 9.0\n?",          # changed
    )
    added = _node("/project1/level1", "TOP:level\nend\n")
    root.children = [noise, added]     # blur1 and script1 removed
    after.roots = [root]

    result = diff(project, after)
    assert [n.path for n in result.added] == ["/project1/level1"]
    assert sorted(n.path for n in result.removed) == [
        "/project1/blur1",
        "/project1/script1",
    ]
    changed = {c.path: c for c in result.changed}
    assert changed["/project1/noise1"].params[0].render() == (
        "period: '2.5' -> '9.0'"
    )


def test_diff_separates_a_pure_move_from_a_real_change(project, tmp_path):
    root = _node("/project1", "COMP:container\nend\n")
    root.children = [
        _node(
            "/project1/noise1",
            "TOP:noise\ntile 800 900 1 1\nend\n",   # moved only
            "?\nperiod 0 2.5\n?",
        ),
        _node(
            "/project1/blur1",
            "TOP:blur\ntile 5 5 1 1\ninputs\n{\n0 \tnoise1\n}\nend\n",
            "?\nsize 0 4\n?",
        ),
        _node(
            "/project1/script1", "DAT:text\nend\n",
            text="import math\nprint(math.pi)\n",
        ),
    ]
    after = Project(source=tmp_path / "b.toe", build=project.build, roots=[root])

    result = diff(project, after)
    assert result.empty
    assert [c.path for c in result.moved_only] == ["/project1/noise1"]


def test_diff_shows_a_line_diff_of_changed_dat_code(project, tmp_path):
    root = _node("/project1", "COMP:container\nend\n")
    root.children = [
        _node("/project1/noise1", "TOP:noise\ntile 0 0 1 1\nend\n", "?\nperiod 0 2.5\n?"),
        _node(
            "/project1/blur1",
            "TOP:blur\ntile 5 5 1 1\ninputs\n{\n0 \tnoise1\n}\nend\n",
            "?\nsize 0 4\n?",
        ),
        _node(
            "/project1/script1", "DAT:text\nend\n",
            text="import math\nprint(math.tau)\n",
        ),
    ]
    after = Project(source=tmp_path / "b.toe", build=project.build, roots=[root])

    change = {c.path: c for c in diff(project, after).changed}["/project1/script1"]
    assert "-print(math.pi)" in change.text_diff
    assert "+print(math.tau)" in change.text_diff


# -- integration against the shipped libraries ------------------------------

def _installed():
    from td_atlas.install import InstallNotFound, discover

    try:
        return discover()
    except InstallNotFound:
        return None


needs_td = pytest.mark.skipif(
    _installed() is None, reason="no TouchDesigner installation"
)


@needs_td
def test_expands_and_reads_a_shipped_example():
    from td_atlas.project import load_file

    install = _installed()
    tox = install.snippets / "TOP" / "blurTOP.tox"
    if not tox.exists():
        pytest.skip("snippet library not present")

    project = load_file(tox)
    nodes = list(project.walk())
    assert len(nodes) > 10
    assert any(n.op_type == "blurTOP" for n in nodes)
    # Every node should have resolved to a canonical, index-joinable type.
    assert all(n.op_type for n in nodes if n.family)


# -- serialising to JSON ----------------------------------------------------

# Every way a hand-written line format has been seen to lose DAT text, in one
# string: a trailing newline (so the last element is empty), trailing spaces on
# a line, a lone CR, an embedded quote and backslash, a tab, and the three
# characters `str.splitlines()` breaks on but `split("\n")` does not.
_ADVERSARIAL = (
    'print("a\\\\b")\t \n'
    "trailing spaces   \n"
    "carriage\rreturn\n"
    "form\x0cfeed and \x0bvtab and  separator\n"
    "\n"
)


def test_serialised_project_is_ordinary_json(project):
    from td_atlas.project.serialize import to_text

    data = json.loads(to_text(project))
    assert data["operator_count"] == 4
    assert data["operators"][0]["name"] == "project1"
    assert [c["name"] for c in data["operators"][0]["children"]] == [
        "noise1", "blur1", "script1"
    ]


def test_dat_text_round_trips_byte_for_byte(project, tmp_path):
    """The failure this exists to catch: text that comes back subtly edited.

    Every hand-written format measured for this lost something here — a
    trailing newline, a trailing space, a cell separator. The array of lines
    survives only because `split("\\n")` and `"\\n".join` are exact inverses,
    so the assertion is byte equality against what `model.py` read, not
    'looks the same'.
    """
    from td_atlas.project.serialize import join_text, to_text

    script = project.roots[0].children[2]
    script.text = _ADVERSARIAL

    data = json.loads(to_text(project))
    lines = data["operators"][0]["children"][2]["text"]
    assert join_text(lines) == _ADVERSARIAL

    # And the tempting alternative really does lose data, which is why this
    # test is worth its length.
    assert "\n".join(_ADVERSARIAL.splitlines()) != _ADVERSARIAL


def test_dat_text_is_one_output_line_per_dat_line(project):
    """The whole reason for the custom printer: a one-line edit diffs as one."""
    from td_atlas.project.serialize import to_text

    project.roots[0].children[2].text = "import math\nprint(math.pi)\n"
    text = to_text(project)
    assert '        "import math",\n' in text
    assert '        "print(math.pi)",\n' in text


def test_vectors_and_flags_stay_on_one_line(project):
    from td_atlas.project.serialize import to_text

    text = to_text(project)
    assert '"tile": [5.0, 5.0, 1.0, 1.0],\n' in text
    assert '"inputs": [[0, "noise1"]],\n' in text


def test_serialised_inputs_keep_their_index(project):
    """Input 2 wired with 0 and 1 empty is not the same as input 0 wired."""
    from td_atlas.project.serialize import node_data

    node = _node(
        "/project1/comp1",
        "COMP:geo\ninputs\n{\n2 \tnoise1\n}\nend\n",
    )
    assert node_data(node)["inputs"] == [[2, "noise1"]]


def test_expression_parameters_keep_the_constant_as_well(project):
    from td_atlas.project.serialize import node_data

    node = _node(
        "/project1/noise1", "TOP:noise\nend\n",
        '?\nperiod 16 2.5 "absTime.seconds"\n?',
    )
    assert node_data(node)["parms"]["period"] == {
        "expr": "absTime.seconds", "value": "2.5"
    }


def test_parameters_are_sorted(project):
    from td_atlas.project.serialize import node_data

    node = _node(
        "/project1/noise1", "TOP:noise\nend\n",
        "?\nperiod 0 2.5\namp 0 1\nharmonics 0 3\n?",
    )
    assert list(node_data(node)["parms"]) == ["amp", "harmonics", "period"]


def test_table_rows_are_one_per_output_line(project):
    from td_atlas.project.serialize import to_text

    project.roots[0].children[2].table = [["a", "b"], ["c", "d"]]
    text = to_text(project)
    assert '          ["a", "b"],\n' in text
    assert '          ["c", "d"]\n' in text


def test_an_unknown_path_is_refused_with_the_top_level_listed(project):
    from td_atlas.project.serialize import to_text

    with pytest.raises(LookupError) as caught:
        to_text(project, path="/nope")
    assert "/project1" in str(caught.value)


def test_a_subtree_serialises_alone(project):
    from td_atlas.project.serialize import to_text

    data = json.loads(to_text(project, path="/project1/noise1"))
    assert data["operator_count"] == 1
    assert data["operators"][0]["name"] == "noise1"


# -- the flags line ---------------------------------------------------------

def test_node_flags_are_pairs_not_a_flat_token_list():
    """`viewer 1 parlanguage 0` is two flags with values, not four flags.

    Read flat, roughly half of every flags line's tokens are reported as
    flags that do not exist: 88,720 tokens across the 19,001 `flags` lines in
    the expansion cache, of which 44,360 are values. The old assertion here
    (`"current" in node.flags`) passed either way and so guarded nothing.
    """
    assert read_node(_N).flags == {
        "current": "on", "viewer": "1", "parlanguage": "0"
    }
    assert "on" not in read_node(_N).flags


# -- serialising real files -------------------------------------------------

def _palette(*parts):
    install = _installed()
    if install is None:
        pytest.skip("no TouchDesigner installation")
    path = install.palette.joinpath(*parts)
    if not path.exists():
        pytest.skip(f"{path} not shipped by this build")
    return path


@needs_td
def test_the_bind_parameters_of_the_shipped_checker_are_not_glued():
    """The defect on the file it was found in.

    `/checker/checker/color1` binds its four channels to the component's
    custom parameters. Before the flags word decided the split, each of them
    was reported as holding `1 parent.Checker.par.Color1r` — a value the
    parameter does not have, and one that would have been written back into
    somebody's `.parm` by the reassembler.
    """
    from td_atlas.project import index_resolver, load_file
    from td_atlas.project.serialize import to_text

    project = load_file(_palette("Generators", "checker.tox"),
                        resolver=index_resolver())
    node = project.find("/checker/checker/color1")
    for name, master in [
        ("colorr", "parent.Checker.par.Color1r"),
        ("colorg", "parent.Checker.par.Color1g"),
        ("colorb", "parent.Checker.par.Color1b"),
        ("alpha", "parent.Checker.par.Color1a"),
    ]:
        parm = node.parms[name]
        assert parm.value == "1"
        assert parm.bind == master
        assert parm.mode == "bind"

    # And it reaches the text, or the reassembler cannot see the two halves.
    data = json.loads(to_text(project, "/checker/checker/color1"))
    assert data["operators"][0]["parms"]["colorr"] == {
        "bind": "parent.Checker.par.Color1r",
        "value": "1",
    }

    # The `.cparm` header is page names, not a parameter called `pages`.
    owner = project.find("/checker/checker")
    assert owner.custom_pages == ["Checker", "About"]
    assert "pages" not in owner.custom_parms


@needs_td
@pytest.mark.parametrize(
    "parts",
    [
        ("Generators", "checker.tox"),
        ("Tools", "battery.tox"),
        ("UI", "popDialog.tox"),
        ("Techniques", "motionSense.tox"),
        ("Mapping", "camSchnappr.tox"),
    ],
)
def test_no_parameter_value_glues_two_fields_together(parts):
    """Every value a reader hands back is one whole field of its line.

    The check is the grammar itself, run against the file: re-lex each
    `.parm` line, and require that a line whose flags word promises halves
    splits into exactly that many fields with the reader's `value`, `expr` and
    `bind` each equal to one of them. A line with no mode bit promises no
    split at all, so its remainder is one value by definition and the only
    thing to check is that the reader did not split it anyway.
    """
    from td_atlas.project import index_resolver, load_file
    from td_atlas.project.expand import expand
    from td_atlas.project.formats import (
        _BIND_BIT,
        _EXPR_BIT,
        _PARM_LINE,
        _UNKNOWN_EXTRA_BIT,
        _all_fields,
        _unquote,
    )

    path = _palette(*parts)
    project = load_file(path, resolver=index_resolver())
    root = expand(path).root

    checked = 0
    for parm_file in sorted(root.rglob("*.parm")):
        for raw in parm_file.read_text(errors="replace").splitlines():
            line = raw.strip()
            if not line or line == "?":
                continue
            match = _PARM_LINE.match(line)
            if not match:
                continue
            flags = int(match.group("flags"))
            rest = match.group("rest").strip()
            parms = read_parms(line)
            parm = parms[match.group("name")]
            checked += 1

            if flags & _UNKNOWN_EXTRA_BIT:
                assert parm.expr is None and parm.bind is None
                continue

            halves = bool(flags & _EXPR_BIT) + bool(flags & _BIND_BIT)
            if not halves:
                assert parm.value == _unquote(rest)
                assert parm.expr is None and parm.bind is None
                continue

            fields = _all_fields(rest)
            assert len(fields) == halves + 1, line
            recovered = [parm.value]
            if flags & _EXPR_BIT:
                recovered.append(parm.expr)
            if flags & _BIND_BIT:
                recovered.append(parm.bind)
            assert recovered == fields, line
    assert checked > 50

    # And the defect's signature nowhere in the loaded project: a constant
    # that ends with the very half that was supposed to be split off it. (A
    # constant may legitimately *equal* its expression — `$ON` does — which is
    # why this looks for the glue, the separating space, and not containment.)
    for node in project.walk():
        for parm in node.parms.values():
            for half in (parm.expr, parm.bind):
                if half:
                    assert not parm.value.endswith(" " + half)


@needs_td
@pytest.mark.parametrize(
    "parts", [("Generators", "checker.tox"), ("Tools", "battery.tox")]
)
def test_every_dat_in_a_shipped_component_round_trips(parts):
    from td_atlas.project import index_resolver, load_file
    from td_atlas.project.serialize import join_text, to_text

    project = load_file(_palette(*parts), resolver=index_resolver())
    data = json.loads(to_text(project))

    def texts(entries):
        for entry in entries:
            if entry["text"] is not None:
                yield entry["name"], join_text(entry["text"])
            yield from texts(entry["children"])

    serialised = dict(texts(data["operators"]))
    original = {n.name: n.text for n in project.scripts()}
    assert serialised == original
    assert original  # a component with no DAT would make this vacuous
    assert data["operator_count"] == len(list(project.walk()))


@needs_td
def test_serialising_a_four_thousand_operator_component(capsys):
    """The size and cost of the format on the largest component shipped.

    Reference numbers (kantanMapper, 4080 nodes): 106,082 lines and
    6,111,237 bytes. The first measurement of the format read 99,068 lines
    and 5,475,136 bytes; the 7% growth is the bind parameters, which used to
    print as one bare string per parameter and now print as a two-key map
    because the constant and the bind expression are two values, not one.
    """
    import time

    from td_atlas.project import index_resolver, load_file
    from td_atlas.project.serialize import to_text

    project = load_file(
        _palette("Mapping", "kantanMapper.tox"), resolver=index_resolver()
    )
    count = len(list(project.walk()))
    started = time.perf_counter()
    text = to_text(project)
    elapsed = time.perf_counter() - started

    lines = text.count("\n")
    size = len(text.encode())
    with capsys.disabled():
        print(
            f"\nkantanMapper.tox: {count} operators, {lines} lines, "
            f"{size} bytes, serialised in {elapsed * 1000:.0f} ms "
            f"(reference: 4080 / 106082 / 6111237)"
        )
    assert count > 3000
    json.loads(text)
    # Not a performance target, a guard against an accidental quadratic: the
    # measured cost is a quarter of a second.
    assert elapsed < 10


@needs_td
def test_the_mcp_tool_refuses_a_network_larger_than_the_limit():
    from td_atlas.mcp.server import td_project_text

    text = td_project_text(str(_palette("Mapping", "kantanMapper.tox")), max_bytes=1000)
    assert "over the 1000 byte limit" in text
    assert "fix: " in text
    assert "td_project_read" in text


@needs_td
def test_the_mcp_tool_refuses_an_unknown_path_with_a_hint():
    from td_atlas.mcp.server import td_project_text

    text = td_project_text(str(_palette("Generators", "checker.tox")), path="/nope")
    assert "no node at '/nope'" in text
    assert "fix: " in text


@needs_td
def test_the_cli_writes_a_readable_file(tmp_path):
    from td_atlas.cli import main

    out = tmp_path / "network.json"
    assert main([
        "project", "text", str(_palette("Generators", "checker.tox")),
        "--path", "/checker", "-o", str(out),
    ]) == 0
    assert json.loads(out.read_text())["path"] == "/checker"


@needs_td
def test_the_cli_reports_an_unknown_path_instead_of_raising(capsys):
    from td_atlas.cli import main

    assert main([
        "project", "text", str(_palette("Generators", "checker.tox")),
        "--path", "/nope",
    ]) == 1
    assert "no node at '/nope'" in capsys.readouterr().err
