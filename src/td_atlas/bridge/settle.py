"""Waiting for TouchDesigner to draw an edit before a frame is taken.

A render asked for right after a parameter write returns the frame from
before the write (second agent report, "Спотыкания помельче"; the agent's
workaround was `run("...save(...)", delayFrames=3)` through td_exec). Every
request runs inside one cook on TouchDesigner's main thread, so a request
cannot wait for frames itself: the handler would block the very frames it is
waiting for. The wait has to happen between requests, on the host.

The clock is `ping`'s `tick`, `op.TDResources.time.frame`: a frame count
that advances while the application draws, whether or not the project
timeline plays. It is not `absTime.frame`, which this module first waited on
on the belief that it was that count. Measured on 2025.32460 (demo2.toe),
two pings a second apart with the root timeline paused: absTime.frame
2013719 -> 2013719 while op.TDResources.time.frame went 516 -> 577. On a
paused timeline the old wait sat out its ten seconds and reported "drew 0 of
3 frame(s) in 10.0 s — stalled, asleep or behind a dialog" about an
application that was drawing. A bridge older than `tick` sends only `frame`,
and is waited on by it, with that blind spot. An application that is asleep
or behind a modal dialog stops both, and then the wait says so.
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
    clock: str = "tick"
    """The `ping` field counted: `tick`, or `frame` from an older bridge."""

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
        text = (
            f"settle: TouchDesigner drew {self.advanced} of {self.asked} "
            f"frame(s) in {self.seconds:.1f} s — it is stalled, asleep or "
            f"behind a dialog, and this image may predate the last edit"
        )
        if self.clock == "frame":
            text += (
                ". This bridge counts absTime.frame, which also stands still "
                "while the timeline is paused; 'td-atlas reload' gives it a "
                "clock that does not"
            )
        return text


def wait_frames(client, frames: int, budget: float | None = None) -> Settled:
    """Return once TouchDesigner's own clock has moved `frames` frames.

    Polls `ping` rather than sleeping `frames / fps`: the rate `ping` states is
    the one asked for, not the one achieved, and a heavy network or a
    background window draws fewer frames than it — exactly the case where the
    edit would still be missing from the image.

    The polls are left out of the call journal: they are this wait's
    bookkeeping, one a frame, and a stalled application turned ten seconds of
    them into six hundred lines between the calls a reader came for. The call
    the settle came before is journaled as usual.
    """
    budget = DEFAULT_BUDGET if budget is None else budget
    clock, sleep = time.monotonic, time.sleep
    started = clock()
    if frames <= 0:
        return Settled(frames, 0, 0.0)
    first = client.ping(journaled=False)
    # One key for the whole wait: `tick` where the bridge sends it, else the
    # older `frame` (absTime.frame, which stands still on a paused timeline).
    key = "tick" if isinstance(first.get("tick"), (int, float)) else "frame"
    origin = first.get(key)
    rate = float(first.get("fps") or 60.0) or 60.0
    step = max(frames / rate, 0.005)
    if not isinstance(origin, (int, float)):
        sleep(step)
        return Settled(frames, None, clock() - started, key)
    advanced = 0
    while True:
        sleep(step)
        now = client.ping(journaled=False).get(key)
        if isinstance(now, (int, float)):
            advanced = int(now - origin)
        if advanced >= frames or clock() - started >= budget:
            return Settled(frames, advanced, clock() - started, key)
        step = max(1.0 / rate, 0.005)
