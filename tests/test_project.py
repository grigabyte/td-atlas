"""Tests for reading .toe/.tox files.

The unit tests run anywhere. The integration tests need a TouchDesigner
installation, because they exercise the real `toeexpand` against the example
libraries it ships, and skip when there is none.
"""

from __future__ import annotations

import struct

import pytest

from td_atlas.project.diff import diff
from td_atlas.project.formats import (
    FormatError,
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
