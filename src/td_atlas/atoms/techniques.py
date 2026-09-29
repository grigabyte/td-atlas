"""Names of techniques, mapped to the operators that carry them out.

An agent asks for the algorithm by its name — "marching cubes" — and no
operator page uses that name. On 2026-09-25 the search put Cube Map TOP and
Projection TOP first for "marching cubes isosurface from volume field", and
Polygonize POP turned up on the fifth query, once the agent already knew the
word "polygonize" (agent report 5, point 1).

Kept to what the index's own text supports: each mapping below follows from
the named operators' documentation, and the one entry with no operator says
so because no operator page mentions it. Not a thesaurus — a term goes in
when an agent missed an operator for want of it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Technique:
    terms: tuple[str, ...]
    types: tuple[str, ...]
    note: str


TECHNIQUES: tuple[Technique, ...] = (
    Technique(
        terms=("marching cubes", "marching cube", "isosurface", "iso surface",
               "iso-surface", "implicit surface", "volume to mesh", "voxel"),
        types=("polygonizePOP", "isosurfaceSOP", "metaballSOP"),
        note=(
            "Polygonize POP builds a polygon surface around the voxels of a 3D "
            "texture (Texture 3D TOP, or a GLSL TOP writing a 3D texture) that "
            "exceed a threshold — the GPU route. Iso Surface SOP draws an "
            "implicit function; Metaball SOP makes metaball surfaces"
        ),
    ),
    Technique(
        terms=("metaball", "blobby"),
        types=("metaballSOP", "polygonizePOP"),
        note=(
            "Metaball SOP makes the surfaces directly; Polygonize POP meshes a "
            "field you compute yourself into a 3D texture"
        ),
    ),
    Technique(
        terms=("signed distance", "sdf", "distance field"),
        types=("fieldPOP", "polygonizePOP"),
        note=(
            "Field POP outputs a signed distance attribute per point "
            "(signeddistance); a distance field sampled into a 3D texture is "
            "meshed by Polygonize POP"
        ),
    ),
    Technique(
        terms=("dilate", "dilation", "erode", "erosion", "max filter",
               "min filter", "morphology", "morphological"),
        types=("glslTOP",),
        note=(
            "no operator page in this index mentions dilation or erosion; a "
            "GLSL TOP taking the maximum (or minimum) over a 3x3 neighbourhood "
            "is what agents have used"
        ),
    ),
)


def _pattern(term: str) -> re.Pattern[str]:
    # Whole words, an optional plural 's', and any run of spaces or hyphens
    # between the words of a phrase.
    words = [re.escape(w) for w in re.split(r"[\s-]+", term)]
    return re.compile(r"\b" + r"[\s-]+".join(words) + r"s?\b", re.IGNORECASE)


_COMPILED = tuple(
    (t, tuple(_pattern(term) for term in t.terms)) for t in TECHNIQUES
)


def matching(query: str) -> list[tuple[str, Technique]]:
    """Every technique named in `query`, with the term that named it."""
    found = []
    for technique, patterns in _COMPILED:
        for term, pattern in zip(technique.terms, patterns):
            if pattern.search(query):
                found.append((term, technique))
                break
    return found
