"""Detecting the failures TouchDesigner does not report.

`errors` covers what TouchDesigner considers an error. It does not cover a
network that never cooks, an audio device switched off, a CPU-bound operator
holding the frame rate at 3, a shader that failed to compile, a traceback
raised inside a script or a callback, or a licence that forbids what you just
asked for. Each of those looks completely healthy from every other angle, and
each one silently produces nothing.

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

# How much of a compiler log or a traceback is worth putting beside a path.
_EXCERPT = 110

# Measured on build 2025.32460: a shader that built returns
# "Vertex Shader Compile Results:\n\nCompiled Successfully\n..." and a shader
# that failed returns the same preamble with
# "ERROR: /project1/probe/frag1:2: 'notAThing' : undeclared identifier".
# So a non-empty compileResult is not a failure — the marker is.
_COMPILE_FAILED = "ERROR"


def _lines(text: str) -> list[str]:
    return [line.strip() for line in str(text).splitlines() if line.strip()]


def _clip_line(line: str) -> str:
    return line if len(line) <= _EXCERPT else line[:_EXCERPT] + "..."


def _warning_excerpt(text: str) -> str:
    """The one line of an operator's warning worth putting beside its path.

    TouchDesigner's warnings are one sentence except when they are not: a cook
    dependency loop appends the whole cook stack, tab-indented, and printing
    that under every warned operator would bury the report. Measured shape
    (build 2025.32460):

        Warning: Cook dependency loop detected. Check for exports, ...:
        \tCook stack starts
        \t/…/Lgrade  \t/…/Lcomp  \t/…/Lfb

    The leading `Warning: ` is dropped because the finding already says these
    are warnings, and the stack is left to `td_errors`, which prints it whole.
    """
    lines = _lines(text)
    if not lines:
        return ""
    head = lines[0]
    if head.startswith("Warning: "):
        head = head[len("Warning: "):]
    return _clip_line(head)


def _compile_excerpt(text: str) -> str:
    """The compiler's own line — it names the source DAT and the line number."""
    for line in _lines(text):
        if _COMPILE_FAILED in line:
            return _clip_line(line)
    return ""


def _script_excerpt(text: str) -> str:
    """The exception line out of a traceback TouchDesigner kept for an operator.

    Measured shape (build 2025.32460): "  Error: Traceback (most recent call
    last):", the File lines, the exception, then the operator path in brackets.
    Neither the first nor the last line is the one worth showing, so the
    exception line is picked out and the whole text falls back to its first
    line when nothing matches.
    """
    lines = _lines(text)
    for line in lines:
        # The wrapper TouchDesigner puts in front ("Error: Traceback (most
        # recent call last):") ends in 'Error' itself, so it is skipped along
        # with the frame lines it introduces.
        if "Traceback" in line or line.startswith("File "):
            continue
        head = line.split(":", 1)[0].strip()
        if head.endswith(("Error", "Exception", "Interrupt", "Exit")):
            return _clip_line(line)
    return _clip_line(lines[0]) if lines else ""


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

    def summary(self) -> str:
        """The whole verdict on one line, for the status panel inside TD.

        The counts, not the findings: a panel is read at a glance from across
        a room, and one line that says how many errors there are sends the
        reader to `td_health` for the list. "Nothing wrong found" is the same
        wording the full report uses, so the two never look like disagreement.
        """
        counts = []
        for severity, word in (("error", "error"), ("warning", "warning"),
                               ("note", "note")):
            found = sum(1 for f in self.findings if f.severity == severity)
            if found:
                counts.append(f"{found} {word}{'s' if found != 1 else ''}")
        verdict = ", ".join(counts) if counts else "nothing wrong found"
        return (
            f"{self.fps_actual:.0f}/{self.fps_target:.0f} fps, "
            f"{self.cooking}/{self.nodes} cooking - {verdict}"
        )

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


def _publish(client: BridgeClient, health: Health) -> None:
    """Put the verdict on the bridge's own status panel. Best effort.

    The verdict is decided here and not inside TouchDesigner — it needs two
    samples an interval apart and the rules above — so the panel has to be
    told. Every failure is swallowed: a bridge older than this method answers
    404, and a health check must not fail because a display did not update.
    """
    try:
        client.call("status_note", health=health.summary())
    except Exception:
        pass


def check(
    client: BridgeClient,
    path: str = "/project1",
    interval: float = 1.0,
    publish: bool = True,
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

    # The bridge stops walking at its own cap. Said out loud, because every
    # count below — and "Nothing wrong found" above all — describes the part
    # that was walked, and reads as a verdict on the whole project otherwise.
    if second.get("truncated"):
        health.findings.append(
            Finding(
                "warning", "walk-truncated",
                f"only {second.get('scanned', len(live))} operator(s) under "
                f"{path} were sampled (the bridge walks at most "
                f"{second.get('limit')}); at least "
                f"{second.get('notScanned')} more were not looked at, and "
                f"nothing below is known about them. Run this on a subtree "
                f"to cover the rest",
            )
        )

    dormant: list[str] = []
    slow: list[str] = []
    errored: list[str] = []
    warned: list[str] = []
    bypassed: list[str] = []
    inactive: list[str] = []
    shaders: list[str] = []

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
        # Read before the COMP guard below: the guard is about cooking, not
        # about compiling. A compileResult that mentions no ERROR is a
        # successful build's log, not a failure — see _compile_excerpt.
        failure = _compile_excerpt(node.get("compileResult") or "")
        if failure:
            shaders.append(f"{node['path']} ({failure})")
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
            # The path alone sends the reader back to `td_errors` to find out
            # what is wrong, and a cook dependency loop — the warning worth
            # catching here — reads as a bare path with no hint that it is one.
            # Measured: the bridge hands the whole string over, this is only
            # where it was being dropped.
            warned.append(f"{node['path']} ({_warning_excerpt(node['warnings'])})")
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
    if shaders:
        health.findings.append(
            Finding(
                "error", "shader-compile",
                f"{len(shaders)} GLSL operator(s) whose shader did not "
                f"compile — the operator outputs a checkerboard or a black "
                f"frame. TouchDesigner records this as a warning that says to "
                f"open an Info DAT; the compiler's own line, with the source "
                f"DAT and the line number, is below",
                shaders,
            )
        )

    # Attributed per operator where the bridge managed it; the raw recursive
    # read is the fallback, because an unattributed traceback still beats an
    # unreported one.
    scripted = second.get("scriptErrors") or {}
    raw = second.get("scriptErrorsRaw") or ""
    if scripted:
        health.findings.append(
            Finding(
                "error", "script-errors",
                f"{len(scripted)} operator(s) raised a traceback in a script "
                f"or a callback — the code stopped where it threw, and "
                f"TouchDesigner keeps script errors apart from the error list, "
                f"so nothing else reports them",
                [f"{node_path} ({_script_excerpt(message)})"
                 for node_path, message in sorted(scripted.items())],
            )
        )
    elif raw:
        health.findings.append(
            Finding(
                "error", "script-errors",
                f"a script or callback raised a traceback somewhere under "
                f"{path}, and which operator it belongs to could not be "
                f"determined: {_script_excerpt(raw)}",
            )
        )
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
    if publish:
        _publish(client, health)
    return health
