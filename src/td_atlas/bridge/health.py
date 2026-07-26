"""Detecting the failures TouchDesigner does not report.

`errors` covers what TouchDesigner considers an error. It does not cover a
network that never cooks, an audio device switched off, a CPU-bound operator
holding the frame rate at 3, or a licence that forbids what you just asked
for. Each of those looks completely healthy from every other angle, and each
one silently produces nothing.

Two samples a moment apart separate live nodes from dormant ones; the rest is
read off a single snapshot.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .client import BridgeClient

# A node cooking for longer than this is worth naming: at 60 fps the whole
# frame budget is 16.7 ms.
_SLOW_COOK_MS = 8.0

# Operators whose entire job is to reach the outside world. If one of these is
# switched off, the composition is silently doing nothing.
_OUTPUT_TYPES = {
    "audiodeviceoutCHOP": "audio output",
    "moviefileoutTOP": "movie recording",
    "midioutCHOP": "MIDI output",
    "oscoutCHOP": "OSC output",
    "dmxoutCHOP": "DMX output",
    "serialDAT": "serial output",
    "touchoutTOP": "Touch Out",
    "ndioutTOP": "NDI output",
    "videodeviceoutTOP": "video output",
}

_SEVERITY_ORDER = {"error": 0, "warning": 1, "note": 2}


@dataclass
class Finding:
    severity: str            # 'error' | 'warning' | 'note'
    kind: str
    message: str
    paths: list[str] = field(default_factory=list)

    def render(self) -> str:
        mark = {"error": "ERROR", "warning": "WARN ", "note": "note "}[self.severity]
        text = f"[{mark}] {self.message}"
        if self.paths:
            shown = ", ".join(self.paths[:6])
            if len(self.paths) > 6:
                shown += f", +{len(self.paths) - 6} more"
            text += f"\n         {shown}"
        return text


@dataclass
class Health:
    findings: list[Finding] = field(default_factory=list)
    fps_target: float = 0.0
    fps_actual: float = 0.0
    nodes: int = 0
    cooking: int = 0
    license_type: str = ""
    product: str = ""

    @property
    def ok(self) -> bool:
        return not any(f.severity == "error" for f in self.findings)

    def render(self) -> str:
        head = (
            f"{self.product or 'TouchDesigner'}"
            f"{f' ({self.license_type})' if self.license_type else ''} — "
            f"{self.fps_actual:.0f}/{self.fps_target:.0f} fps, "
            f"{self.cooking}/{self.nodes} operators cooking"
        )
        if not self.findings:
            return head + "\nNothing wrong found."
        ordered = sorted(self.findings, key=lambda f: _SEVERITY_ORDER[f.severity])
        return head + "\n" + "\n".join(f.render() for f in ordered)


def check(
    client: BridgeClient, path: str = "/project1", interval: float = 1.0
) -> Health:
    """Sample a subtree twice and report what is quietly broken."""
    first = client.call("health_sample", path=path)
    time.sleep(interval)
    second = client.call("health_sample", path=path)

    frames = second["frame"] - first["frame"]
    elapsed = max(interval, 1e-6)
    health = Health(
        fps_target=second.get("fpsTarget", 0.0),
        fps_actual=frames / elapsed,
        license_type=(second.get("license") or {}).get("type", ""),
        product=second.get("product", ""),
    )

    before = {n["path"]: n for n in first["nodes"]}
    live = second["nodes"]
    health.nodes = len(live)

    dormant: list[str] = []
    slow: list[str] = []
    errored: list[str] = []
    warned: list[str] = []
    bypassed: list[str] = []
    inactive: list[str] = []

    # Two innocent conditions freeze the frame clock: the timeline is paused,
    # or TouchDesigner's window is in the background, where it all but stops
    # rendering. In both cases *everything* looks dormant. Reporting that as a
    # dead network would be a false alarm — without frames there is simply no
    # evidence either way, so say which it is and stop there.
    paused = second.get("playing") is False
    stalled = frames < 5 or paused

    for node in live:
        previous = before.get(node["path"])
        delta = node["cooks"] - previous["cooks"] if previous else 0
        # A node is expected to keep up with the frame clock; COMPs and other
        # containers legitimately cook rarely, so only leaf operators count.
        if node["family"] == "COMP":
            continue
        if stalled:
            health.cooking += 1
        elif delta == 0:
            dormant.append(node["path"])
        else:
            health.cooking += 1

        if node["cookTime"] >= _SLOW_COOK_MS:
            slow.append(f"{node['path']} ({node['cookTime']:.0f} ms)")
        if node["errors"]:
            errored.append(node["path"])
        if node["warnings"]:
            warned.append(node["path"])
        if node["bypass"]:
            bypassed.append(node["path"])
        if node["type"] in _OUTPUT_TYPES and node.get("active") is False:
            inactive.append(f"{node['path']} ({_OUTPUT_TYPES[node['type']]})")

    # A clamped resolution is only a warning to TouchDesigner, but it silently
    # changes the deliverable: asking for 1920x1080 on a Non-Commercial licence
    # yields 1280x720 and nothing stops you.
    clamped = [
        node["path"]
        for node in live
        if node["warnings"] and "resolution limited" in node["warnings"].lower()
    ]
    if clamped:
        health.findings.append(
            Finding(
                "error", "resolution-clamped",
                f"{len(clamped)} operator(s) had their resolution silently "
                f"reduced by the licence — the output is smaller than asked for",
                clamped,
            )
        )
    if "non-commercial" in health.license_type.lower():
        health.findings.append(
            Finding(
                "note", "licence",
                "Non-Commercial licence: resolution is capped at 1280x1280, "
                "realtime H.264/H.265 export on Nvidia GPUs is unavailable, "
                "and the result may not be used in paid work",
            )
        )

    if errored:
        health.findings.append(
            Finding("error", "node-errors",
                    f"{len(errored)} operator(s) reporting an error", errored))
    if inactive:
        health.findings.append(
            Finding(
                "error", "output-off",
                f"{len(inactive)} output operator(s) switched off — these "
                f"produce nothing and report no error",
                inactive,
            )
        )
    if dormant:
        health.findings.append(
            Finding(
                "error", "not-cooking",
                f"{len(dormant)} operator(s) did not cook once in "
                f"{frames} frames. A branch nothing displays or records is "
                f"never pulled, so it is not running at all",
                dormant,
            )
        )
    if paused:
        health.findings.append(
            Finding(
                "note", "paused",
                "the timeline is paused, so the frame clock is not advancing — "
                "which operators are cooking cannot be determined until it "
                "plays again",
            )
        )
    elif stalled:
        health.findings.append(
            Finding(
                "warning", "stalled",
                f"only {frames} frame(s) advanced in {elapsed:.1f}s — "
                f"TouchDesigner throttles rendering when its window is in the "
                f"background. Bring it to the front before judging performance; "
                f"which operators are cooking cannot be determined from here",
            )
        )
    elif health.fps_target and health.fps_actual < health.fps_target * 0.6:
        health.findings.append(
            Finding(
                "warning", "slow",
                f"running at {health.fps_actual:.0f} fps against a target of "
                f"{health.fps_target:.0f}",
            )
        )
    if slow:
        health.findings.append(
            Finding(
                "warning", "expensive",
                f"{len(slow)} operator(s) cost more than {_SLOW_COOK_MS:.0f} ms "
                f"per cook (a whole frame at 60 fps is 16.7 ms)",
                slow,
            )
        )
    if bypassed:
        health.findings.append(
            Finding("warning", "bypassed",
                    f"{len(bypassed)} operator(s) bypassed", bypassed))
    if warned:
        health.findings.append(
            Finding("note", "node-warnings",
                    f"{len(warned)} operator(s) reporting a warning", warned))
    if not second.get("realTime", True):
        health.findings.append(
            Finding("note", "non-realtime",
                    "Realtime is off: TouchDesigner renders every frame rather "
                    "than dropping to keep up with the clock"))
    return health
