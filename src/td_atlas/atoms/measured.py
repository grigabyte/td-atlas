"""What a parameter does that its name, label and help text do not say.

Every line here was measured on a running TouchDesigner (2025.32460, macOS,
2026-09-29) after an agent lost rounds to reading the name literally (agent
reports 3 and 5). `td_operator_schema` and `td-atlas op` print a note under
its parameter, and an operator note under the header. A claim an agent made
and the measurement did not bear out is not here; one it made too narrowly
is here as measured.

Kept to effects a render or a pixel read confirmed. Not a place for advice:
what to build with the fact is the agent's decision.
"""

from __future__ import annotations

OPERATOR_NOTES: dict[str, str] = {
    "levelTOP": (
        "Order of operations, measured on a constant 0.8: the whole Pre page "
        "(brightness1, gamma1, contrast) runs before the Range page (inlow/"
        "inhigh, then outlow/outhigh). brightness1 0.5 with inlow 0.5 gives "
        "0.0 — the brightness halves 0.8 to 0.4 first, and 0.4 is below the "
        "input floor. brightness1 runs before gamma1 and contrast too."
    ),
    "polygonizePOP": (
        "Measured on a 48-cube 3D texture from a GLSL TOP, threshold 0.5: a "
        "straight rod along the grid thinner than 1.5 cells across meshed to "
        "nothing (radius 0.6 cell: 0 points; 0.75 cell: 392), a rod across "
        "the grid stayed continuous down to 1 cell. An agent's thin, "
        "flattened ends broke into beads; that was not re-measured here."
    ),
    "wireframeMAT": (
        "On macOS (2025.32460) neither line width setting changes the render: "
        "linewidth and wirewidth at 1, 3 and 6 gave the same frame to the "
        "pixel. A camera's fog does not reach it either: black linear fog "
        "darkened a Phong box from 19040 to 5864 and left the same box in "
        "this MAT unchanged. Lines are 1 px; thicker or depth-faded lines need "
        "a GLSL MAT, or a pass over the render."
    ),
}

PARAMETER_NOTES: dict[tuple[str, str], str] = {
    ("rampTOP", "type"): (
        "radial is the ANGLE around the centre: constant along a line out "
        "from the centre, changing as you go round. circular is the DISTANCE "
        "from the centre: rings. Measured on a 65x65 ramp: radial 0.5 at "
        "every point on the line to the right edge; circular 0.01 -> 0.98 "
        "along it and 0.49 all the way round a ring."
    ),
    ("textTOP", "positionx"): (
        "An offset from the point alignx picks: the left edge for left, the "
        "centre for center, the right edge for right. Positive is always "
        "rightward, so with alignx right a value that keeps the text in frame "
        "is negative. In fraction units 0.25 is a quarter of the width."
    ),
    ("textTOP", "positiony"): (
        "An offset from the point aligny picks: the bottom edge, the centre "
        "(the default) or the top edge. Positive is upward."
    ),
    ("textTOP", "positionunit"): (
        "Changes only the unit of positionx/positiony (pixels, or a fraction "
        "of the width/height). Where the offset starts is set by alignx and "
        "aligny, not by this."
    ),
    ("wireframeMAT", "linewidth"): (
        "No effect on macOS (2025.32460): 1 and 6 render the same frame."
    ),
    ("wireframeMAT", "wirewidth"): (
        "No effect on macOS (2025.32460): 1, 3 and 6 render the same frame."
    ),
    ("cameraCOMP", "fog"): (
        "Does not reach a Wireframe MAT (measured with black linear fog); it "
        "does reach Phong. The default fog colour, 0.5 grey, fills the empty "
        "background too."
    ),
    ("levelTOP", "brightness1"): (
        "Runs first, before gamma1, contrast and the whole Range page — see "
        "the operator note."
    ),
    ("levelTOP", "inlow"): (
        "Applied after the Pre page: it cuts what brightness1, gamma1 and "
        "contrast already produced, not the input."
    ),
}


def operator_note(op_type: str) -> str:
    return OPERATOR_NOTES.get(op_type, "")


def parameter_note(op_type: str, name: str) -> str:
    return PARAMETER_NOTES.get((op_type, name), "")
