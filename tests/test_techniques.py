"""A technique named by its algorithm finds the operator that carries it out.

On 2026-09-25 "marching cubes isosurface from volume field" ranked Cube Map TOP
and Projection TOP first; Polygonize POP turned up on the fifth query, once
the agent knew the word "polygonize" (agent report 5, point 1). No operator
page uses the algorithm's name, so ranking alone cannot find it.
"""

from __future__ import annotations

import pytest

from td_atlas.atoms.store import AtomStore
from td_atlas.atoms.techniques import matching


@pytest.fixture
def store(tmp_path):
    db = AtomStore(tmp_path / "atlas.db")
    db.create()
    db.insert_ops([
        {"type": "cubemapTOP", "family": "TOP", "label": "Cube Map",
         "summary": "Builds a cube map texture from a volume of images."},
        {"type": "polygonizePOP", "family": "POP", "label": "Polygonize",
         "summary": "Creates a surface enclosing pixels of a 3D texture."},
        {"type": "isosurfaceSOP", "family": "SOP", "label": "Iso Surface",
         "summary": "Uses implicit functions."},
        {"type": "blurTOP", "family": "TOP", "label": "Blur",
         "summary": "Blurs the image."},
    ])
    db.rebuild_fts()
    db.conn.commit()
    yield db
    db.close()


def test_marching_cubes_puts_polygonize_first(store):
    rows = store.search_ops("marching cubes isosurface from volume field")
    assert [r["type"] for r in rows][:2] == ["polygonizePOP", "isosurfaceSOP"]
    # Pinned once, not repeated by the ranked tail.
    assert [r["type"] for r in rows].count("isosurfaceSOP") == 1


def test_a_family_filter_still_applies_to_the_pinned_operators(store):
    rows = store.search_ops("marching cubes", family="SOP")
    assert [r["type"] for r in rows] == ["isosurfaceSOP"]


def test_an_operator_this_build_lacks_is_not_invented(store):
    rows = store.search_ops("metaballs")
    assert "metaballSOP" not in [r["type"] for r in rows]


def test_a_query_naming_no_technique_ranks_as_before(store):
    assert matching("blur an image") == []
    assert store.search_ops("blur an image")[0]["type"] == "blurTOP"


@pytest.mark.parametrize("query,term", [
    ("Marching-Cubes mesh", "marching cubes"),
    ("render metaballs", "metaball"),
    ("an SDF sphere", "sdf"),
    ("dilate bright lines", "dilate"),
])
def test_terms_match_as_words_in_any_case_and_plural(query, term):
    assert [t for t, _ in matching(query)] == [term]


def test_a_term_inside_another_word_does_not_match():
    assert matching("sdfx is a name") == []
    assert matching("voxelated") == []

