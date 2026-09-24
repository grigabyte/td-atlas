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

# The two samples are taken `interval` apart by sleeping on the host, and the
# MCP server answers nothing else while it sleeps — a caller passing 600 takes
# the server down for ten minutes. Ten seconds is ten times the default and
# still well inside the bridge client's own 30 s timeout (client.py), so a
# clamped call still returns rather than failing somewhere else. Chosen from
# those two numbers, not measured.
_MAX_INTERVAL = 10.0

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

# A cook this many frames before the first sample still counts as current:
# the sample may be answered in a frame before the operator's own cook in it.
# One frame, chosen rather than measured — the sampling order inside a frame
# was not observed.
_CURRENT_ALLOWANCE = 1

# Operators whose job is to scale a signal. Bypass on any operator hands its
# input through, but on these it reads as "switched off" and does the
# opposite: a bypassed Level TOP leaves its layer at full strength. An agent
# lost part of an hour to exactly that (report of 2026-09-20).
_GAIN_TYPES = ("levelTOP", "mathTOP", "hsvadjustTOP", "mathCHOP", "mathPOP")

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


def _file_cook_time(node: dict, first_frame: float, now: float,
                    current: list[str], stale: list[str]) -> None:
    """Put an expensive operator in the list its cook time belongs to.

    `cookTime` is the duration of the last cook, whenever that was. Read
    without its frame it passes for the cost of the frame being drawn now,
    and a Movie File In last cooked 374,144 frames earlier was reported as a
    44 ms culprit that way (agent report, 2026-09-20). A bridge older than
    `cookAbsFrame` sends no frame, and its line stays what it was.
    """
    cost = f"{node['path']} ({node['cookTime']:.0f} ms"
    last = node.get("cookAbsFrame")
    if not isinstance(last, (int, float)):
        current.append(cost + ")")
        return
    if last >= first_frame - _CURRENT_ALLOWANCE:
        current.append(f"{cost}, cooked {now - last:.0f} frame(s) ago, during "
                       f"this check, at absolute frame {last:.0f})")
        return
    timeline = node.get("cookFrame")
    where = (f", timeline frame {timeline:.0f}"
             if isinstance(timeline, (int, float)) else "")
    stale.append(
        f"{cost}, last cooked {now - last:.0f} frames ago at absolute frame "
        f"{last:.0f}{where})"
    )


def _clock_read_line(read: dict) -> str:
    where = read.get("where") or ""
    if where == "text":
        return f"{read.get('path')} uses {read.get('call')} (script text)"
    if where.startswith("callbacks of "):
        return f"{read.get('path')} uses {read.get('call')} ({where})"
    return f"{read.get('path')} {where} = {read.get('call')}"


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
    """Sample a subtree twice and report what is quietly broken.

    `interval` is clamped: it is a sleep on the host, and this process answers
    nothing else during it. A negative one would raise from `time.sleep`.
    """
    asked = interval
    interval = max(0.0, min(float(interval), _MAX_INTERVAL))
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

    if asked != interval:
        health.findings.append(
            Finding(
                "note", "interval-clamped",
                f"the {asked}s gap asked for between the two samples was "
                f"changed to {interval}s — the host sleeps through it and "
                f"serves nothing else meanwhile",
            )
        )

    # Reported, not swallowed: the bridge could not read one of the surfaces
    # this report exists to check, and a verdict that leaves that out says
    # "clean" about something it never looked at.
    unread = second.get("scriptErrorsUnread") or []
    if unread:
        count = second.get("scriptErrorsUnreadCount", len(unread))
        health.findings.append(
            Finding(
                "warning", "script-errors-unread",
                f"script and callback tracebacks could not be read on "
                f"{count} operator(s); whether any raised is unknown, not "
                f"clean",
                list(unread),
            )
        )

    # The bridge stops walking at its own cap. Said out loud, because every
    # count below — and "Nothing wrong found" above all — describes the part
    # that was walked, and reads as a verdict on the whole project otherwise.
    if second.get("truncated"):
        health.findings.append(
            Finding(
                "warning", "walk-truncated",
                f"only {second.get('scanned', len(live))} operator(s) at and "
                f"under {path} were sampled (the bridge walks at most "
                f"{second.get('limit')}); at least "
                f"{second.get('notScanned')} more were not looked at, and "
                f"nothing below is known about them. Run this on a subtree "
                f"to cover the rest",
            )
        )

    dormant: list[str] = []
    slow: list[str] = []
    stale: list[str] = []
    loops: list[str] = []
    through: list[str] = []
    negative_add: list[str] = []
    negative_only: list[str] = []
    negative_depth = 0
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
            _file_cook_time(node, first["frame"], second["frame"], slow, stale)
        feedback = node.get("feedback")
        if isinstance(feedback, dict) and not feedback.get("reset"):
            target = feedback.get("target") or "no target parameter"
            loops.append(f"{node['path']} (target {target})")
        if node["bypass"] and node["type"] in _GAIN_TYPES:
            through.append(f"{node['path']} ({node['type']})")
        risk = node.get("negativeFloat")
        if isinstance(risk, dict):
            negative_depth = max(negative_depth, int(risk.get("depth") or 0))
            head = (f"{node['path']} ({risk.get('format')}, black level "
                    f"{risk.get('blacklevel')}")
            adds = risk.get("adds") or []
            if adds:
                negative_add.append(f"{head}, added in {', '.join(adds)})")
            else:
                negative_only.append(head + ")")
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
    if stale:
        health.findings.append(
            Finding(
                "note", "expensive-stale",
                f"{len(stale)} operator(s) show a cook time over "
                f"{_SLOW_COOK_MS:.0f} ms from a cook that happened before this "
                f"check began — they did not cook while it ran and are not "
                f"part of the current frame, so they are not what holds the "
                f"frame rate down",
                stale,
            )
        )
    if bypassed:
        health.findings.append(
            Finding("warning", "bypassed",
                    f"{len(bypassed)} operator(s) bypassed", bypassed))
    if through:
        health.findings.append(
            Finding(
                "note", "bypass-passes-through",
                f"{len(through)} gain operator(s) bypassed. Bypass hands the "
                f"input through unchanged — it does not switch the layer off, "
                f"and a bypassed Level TOP leaves it at full strength. To take "
                f"a layer out, bring its level to zero (brightness1 0 on a "
                f"Level TOP did it in the case this note comes from) or "
                f"disconnect it",
                through,
            )
        )
    if loops:
        health.findings.append(
            Finding(
                "note", "feedback-loops",
                f"the network has {len(loops)} feedback loop(s). "
                f"cook(force=True) does not advance them: they keep what the "
                f"last real timeline frame left, so a frame produced by "
                f"forced cooks carries a stale trail (observed on a Feedback "
                f"TOP; the CHOP and POP hold state between frames the same "
                f"way). To get the right frame, play the timeline rather than "
                f"forcing cooks",
                loops,
            )
        )
    if negative_add:
        health.findings.append(
            Finding(
                "warning", "negative-float",
                f"{len(negative_add)} Level TOP(s) can output negative values "
                f"— a float format, a black level above 0 and no clamp — and "
                f"an Add below takes them away from what it adds to, which "
                f"darkens it. Turn on the Post page clamp with clamplow2 0. "
                f"Checked: Level TOPs only, and an Add TOP or a Composite set "
                f"to add within {negative_depth} operators downstream",
                negative_add,
            )
        )
    if negative_only:
        health.findings.append(
            Finding(
                "note", "negative-float",
                f"{len(negative_only)} Level TOP(s) can output negative "
                f"values — a float format, a black level above 0 and no "
                f"clamp. No Add TOP or Composite set to add was found within "
                f"{negative_depth} operators downstream; anything else that "
                f"sums (a GLSL, a Math) is not checked. The Post page clamp "
                f"with clamplow2 0 removes them",
                negative_only,
            )
        )
    reads = second.get("clockReads") or []
    if reads:
        count = second.get("clockReadsCount", len(reads))
        more = (f" ({count - len(reads)} more not listed)"
                if count > len(reads) else "")
        health.findings.append(
            Finding(
                "warning", "nondeterministic",
                f"{count} read(s) of a clock or a random generator that is "
                f"not the timeline{more} — the frame does not reproduce "
                f"between runs, and two recordings of the same timeline "
                f"differ. absTime is the application's clock and keeps "
                f"counting while the timeline stands still; for a "
                f"deterministic render read me.time.seconds or "
                f"me.time.frame, and seed any generator",
                [_clock_read_line(read) for read in reads],
            )
        )
    scan = second.get("clockScan") or {}
    if scan.get("scanned", 0) < scan.get("of", 0):
        health.findings.append(
            Finding(
                "note", "nondeterminism-unscanned",
                f"the parameters of {scan['scanned']} of {scan['of']} "
                f"operator(s) were checked for clock reads before the "
                f"bridge's time budget ran out; the rest were not looked at, "
                f"and script DATs were all read. Run this on a subtree to "
                f"cover them",
            )
        )
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
