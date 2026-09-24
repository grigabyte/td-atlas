"""Waiting for TouchDesigner to draw an edit before a frame is taken.

A render asked for right after a parameter write returns the frame from
before the write (second agent report, "Спотыкания помельче"; the agent's
workaround was `run("...save(...)", delayFrames=3)` through td_exec). Every
request runs inside one cook on TouchDesigner's main thread, so a request
cannot wait for frames itself: the handler would block the very frames it is
waiting for. The wait has to happen between requests, on the host.

The clock is the one `ping` reports, `absTime.frame` — the application's own
frame count, which advances whether or not the timeline plays. A paused
timeline does not stop it; an application that is asleep or behind a modal
dialog does, and then the wait says so instead of pretending.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

# How long a settle may take before it gives up and says how far it got.
# Ten seconds is 600 frames at 60 fps; asking for more than that is not a
# settle but a wait, and a stalled TouchDesigner should be reported quickly.
DEFAULT_BUDGET = 10.0


@dataclass
class Settled:
    asked: int
    advanced: int | None
    """Frames the application clock moved, or None when it could not be read."""
    seconds: float

    @property
    def complete(self) -> bool:
        return self.advanced is not None and self.advanced >= self.asked

    def note(self) -> str:
        """One line for the caller, empty when the wait did what was asked."""
        if self.asked <= 0 or self.complete:
            return ""
        if self.advanced is None:
            return (
                f"settle: the bridge reports no frame counter, so this waited "
                f"{self.seconds:.2f} s by the clock instead of counting "
                f"{self.asked} frame(s)"
            )
        return (
            f"settle: TouchDesigner drew {self.advanced} of {self.asked} "
            f"frame(s) in {self.seconds:.1f} s — it is stalled, asleep or "
            f"behind a dialog, and this image may predate the last edit"
        )


def wait_frames(client, frames: int, budget: float | None = None) -> Settled:
    """Return once TouchDesigner's own clock has moved `frames` frames.

    Polls `ping` rather than sleeping `frames / fps`: the rate `ping` states is
    the one asked for, not the one achieved, and a heavy network or a
    background window draws fewer frames than it — exactly the case where the
    edit would still be missing from the image.
    """
    budget = DEFAULT_BUDGET if budget is None else budget
    clock, sleep = time.monotonic, time.sleep
    started = clock()
    if frames <= 0:
        return Settled(frames, 0, 0.0)
    first = client.ping()
    origin = first.get("frame")
    rate = float(first.get("fps") or 60.0) or 60.0
    step = max(frames / rate, 0.005)
    if not isinstance(origin, (int, float)):
        sleep(step)
        return Settled(frames, None, clock() - started)
    advanced = 0
    while True:
        sleep(step)
        now = client.ping().get("frame")
        if isinstance(now, (int, float)):
            advanced = int(now - origin)
        if advanced >= frames or clock() - started >= budget:
            return Settled(frames, advanced, clock() - started)
        step = max(1.0 / rate, 0.005)
