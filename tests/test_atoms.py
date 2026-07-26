"""Offline tests. None of these require a running TouchDesigner."""

from __future__ import annotations

import pytest

from td_atlas.atoms.extract_static import (
    _doc_page_candidates,
    _normalize,
    _parse_class_page,
    _parse_expr_help,
    _resolve_doc_page,
)
from td_atlas.atoms.htmltext import html_to_text, page_title
from td_atlas.atoms.store import AtomStore, fts_query
from td_atlas.atoms.validate import validate_params


# -- full-text query building ----------------------------------------------

def test_fts_query_ors_terms_and_drops_filler():
    # FTS5 ANDs bare terms, so a natural-language question would match nothing.
    assert fts_query("displace an image using noise") == (
        '"displace" OR "image" OR "noise"'
    )


def test_fts_query_passes_through_explicit_syntax():
    assert fts_query('"noise" OR "blur"') == '"noise" OR "blur"'


def test_fts_query_survives_punctuation_only_input():
    # Must not produce a syntactically invalid MATCH expression.
    assert fts_query("???") == '"???"'
    assert fts_query("  ") == '""'


def test_fts_query_keeps_short_terms_when_nothing_else_remains():
    assert fts_query("the of a") == '"the" OR "of" OR "a"'


# -- wiki HTML extraction ---------------------------------------------------

_PAGE = """
<html><head><title>Noise TOP - Derivative</title></head><body>
<div id="mw-content-text">
  <h2><span class="mw-headline">Summary</span>
      <span class="mw-editsection">[<a href="#">edit</a>]</span></h2>
  <p>The Noise TOP generates noise.</p>
  <ul><li>Perlin</li><li>Simplex</li></ul>
  <span class="catList navigation-not-searchable">Add &bull; Blur &bull; ZED</span>
  <div class="mw-lingo-tooltip">An Operator Family that...</div>
</div>
<div class="printfooter">Retrieved from...</div>
</body></html>
"""


def test_html_to_text_keeps_content():
    text = html_to_text(_PAGE)
    assert "The Noise TOP generates noise." in text
    assert "## Summary" in text
    assert "- Perlin" in text


def test_html_to_text_drops_navigation_and_tooltips():
    text = html_to_text(_PAGE)
    # The family footer and glossary tooltips would otherwise be appended to
    # every single article.
    assert "ZED" not in text
    assert "Operator Family" not in text
    assert "edit" not in text
    assert "Retrieved from" not in text


def test_page_title_strips_site_suffix():
    assert page_title(_PAGE) == "Noise TOP"


def test_html_to_text_tolerates_malformed_markup():
    assert "hello" in html_to_text("<div id='mw-content-text'><p>hello<p></div")


# -- operator page resolution ----------------------------------------------

def test_doc_page_candidates_prefers_label():
    assert _doc_page_candidates("noiseTOP", "Noise", "TOP")[0] == "Noise_TOP"


def test_resolve_falls_back_to_vendor_prefixed_page():
    pages = {_normalize(p): p for p in ["NVIDIA_Flex_TOP", "Noise_TOP"]}
    assert (
        _resolve_doc_page("flexTOP", "Flex", "TOP", pages, frozenset())
        == "NVIDIA_Flex_TOP"
    )


def test_resolve_refuses_to_steal_another_operators_page():
    # 'flowTOP' suffix-matches both NVIDIA_Flow_TOP and Optical_Flow_TOP; the
    # latter belongs to opticalflowTOP and must not be claimed.
    pages = {
        _normalize(p): p for p in ["NVIDIA_Flow_TOP", "Optical_Flow_TOP"]
    }
    claimed = frozenset({_normalize("opticalflowTOP")})
    assert (
        _resolve_doc_page("flowTOP", "Flow", "TOP", pages, claimed)
        == "NVIDIA_Flow_TOP"
    )


# -- Python class reference -------------------------------------------------

_CLASS_PAGE = """
# noiseTOP Class
This class inherits from the TOP class.

## Members
myMember -> int (Read Only):
Something read only.

## Methods
doThing(a, b=1)-> bool:
Does the thing.

# TOP Class

## Methods
saveByteArray(filetype)-> bytearray:
Saves the image.
""".replace("->", "→")


def test_class_page_attributes_members_to_declaring_class():
    cls, members = _parse_class_page("noiseTOP", _CLASS_PAGE)
    owners = {m["name"]: m["class_name"] for m in members}
    # Inherited members are restated on every operator page; attributing them
    # to TOP is what keeps the table from growing to ~150 rows per operator.
    assert owners["myMember"] == "noiseTOP"
    assert owners["saveByteArray"] == "TOP"
    assert cls["inherits"] == ["TOP"]


def test_class_page_captures_signatures_and_readonly():
    _cls, members = _parse_class_page("noiseTOP", _CLASS_PAGE)
    by_name = {m["name"]: m for m in members}
    assert by_name["doThing"]["signature"] == "doThing(a, b=1)"
    assert by_name["doThing"]["returns"] == "bool"
    assert by_name["myMember"]["read_only"] == 1


# -- expression help --------------------------------------------------------

def test_parse_expr_help_reads_brace_blocks(tmp_path):
    path = tmp_path / "exprhelp"
    path.write_text(
        "{\nabs number \nDescription:\n\tAbsolute value.\n}\n\n"
        "{\nacos number \nDescription:\n\tArccosine.\n}\n"
    )
    entries = _parse_expr_help(path)
    assert [e["name"] for e in entries] == ["abs", "acos"]
    assert entries[0]["signature"] == "abs number"
    assert "Absolute value." in entries[0]["text"]


# -- store ------------------------------------------------------------------

@pytest.fixture
def store(tmp_path):
    db = AtomStore(tmp_path / "atlas.db")
    db.create()
    db.insert_ops(
        [{"type": "noiseTOP", "family": "TOP", "label": "Noise",
          "summary": "Generates noise."}]
    )
    db.insert_params(
        [
            {"op_type": "noiseTOP", "name": "t", "label": "Translate",
             "summary": "Translates the noise.", "par_type": "XYZ"},
            {"op_type": "noiseTOP", "name": "type", "label": "Type",
             "summary": "The noise function.", "par_type": "Menu"},
        ]
    )
    db.merge_runtime_params(
        "noiseTOP",
        [
            {"name": "tx", "label": "Translate", "style": "XYZ", "default": 0.0,
             "group": "t", "page": "Transform", "is_number": True,
             "min": 0.0, "max": 1.0, "clamp_min": True, "clamp_max": False},
            {"name": "type", "label": "Type", "style": "Menu",
             "default": "simplex3d", "page": "Noise", "is_menu": True,
             "menu_names": ["perlin3d", "simplex3d"],
             "menu_labels": ["Perlin 3D", "Simplex 3D"]},
        ],
    )
    db.conn.commit()
    yield db
    db.close()


@pytest.mark.parametrize(
    "query",
    [
        'noise "unbalanced',        # stray quote from prose
        "it's a NEAR() thing",      # apostrophe plus an FTS keyword
        "(((",
        "*",
        "C++ GLSL",
        "blur AND",
        "???",
        "   ",
    ],
)
def test_match_never_produces_invalid_fts_syntax(store, query):
    # A syntax error would surface to the agent as a crash rather than as an
    # empty result set.
    expression = store.match(query)
    store.conn.execute(
        "SELECT 1 FROM ops_fts WHERE ops_fts MATCH ? LIMIT 1", (expression,)
    ).fetchone()


def test_match_preserves_deliberate_fts_syntax(store):
    assert store.match('"noise" OR "blur"') == '"noise" OR "blur"'


def test_parameters_marks_help_only_names_unsettable(store):
    rows = {p["name"]: p for p in store.parameters("noiseTOP")}
    # 't' is documented but is a group heading, not something you can set.
    assert rows["t"]["settable"] is False
    assert rows["tx"]["settable"] is True


def test_parameters_inherits_group_prose_onto_members(store):
    rows = {p["name"]: p for p in store.parameters("noiseTOP")}
    assert rows["tx"]["summary"] == "Translates the noise."


def test_parameters_decodes_menus_and_defaults(store):
    rows = {p["name"]: p for p in store.parameters("noiseTOP")}
    assert rows["type"]["menu_names"] == ["perlin3d", "simplex3d"]
    assert rows["type"]["default"] == "simplex3d"


# -- validation -------------------------------------------------------------

def test_validate_accepts_correct_values(store):
    result = validate_params(store, "noiseTOP", {"type": "perlin3d", "tx": 0.5})
    assert result.ok


def test_validate_points_group_names_at_their_members(store):
    result = validate_params(store, "noiseTOP", {"t": 0.5})
    assert not result.ok
    assert result.problems[0].suggestions == ["tx"]


def test_validate_suggests_correction_for_typos(store):
    result = validate_params(store, "noiseTOP", {"tpye": 1})
    assert "type" in result.problems[0].suggestions


def test_validate_rejects_unknown_menu_entry(store):
    result = validate_params(store, "noiseTOP", {"type": "simplex5d"})
    assert not result.ok
    assert "simplex3d" in result.problems[0].suggestions


def test_validate_honours_clamped_minimum(store):
    assert not validate_params(store, "noiseTOP", {"tx": -5}).ok
    # The upper bound is not clamped, so a large value is allowed through.
    assert validate_params(store, "noiseTOP", {"tx": 900}).ok


def test_validate_allows_expressions_without_checking_their_value(store):
    result = validate_params(
        store, "noiseTOP", {"type": {"expr": "'perlin3d'"}}
    )
    assert result.ok


def test_validate_reports_unknown_operator(store):
    assert not validate_params(store, "nosuchTOP", {"x": 1}).ok


def test_validate_defers_when_operator_was_never_probed(store):
    store.insert_ops([{"type": "oddTOP", "family": "TOP", "label": "Odd"}])
    store.insert_params(
        [{"op_type": "oddTOP", "name": "doc_only", "par_type": "Float"}]
    )
    # Without runtime facts there is nothing authoritative to check against,
    # so TouchDesigner gets the final say rather than a false rejection.
    assert validate_params(store, "oddTOP", {"anything": 1}).ok
