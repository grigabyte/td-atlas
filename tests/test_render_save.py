"""`td_render` can write the frame to a file, and wait for an edit to be drawn.

First agent report, "Что стоило бы добавить" 6: comparing two states pixel by
pixel meant `top.save(path)` through td_exec. Second report, "Спотыкания
помельче": a render right after a `par_set` returned the frame from before
it, and the workaround was `run(..., delayFrames=3)`. `td-atlas render`
already wrote a file with `-o`; the tool gets the same, and both get
`settle_frames`.
"""

from __future__ import annotations

import pytest

from td_atlas import cli
from td_atlas.bridge import settle
from td_atlas.mcp import server

PNG = b"\x89PNG\r\n\x1a\nfake"


class FakeClient:
    """A bridge whose clock moves `step` frames per ping."""

    version_warning = None
    selection_warning = None

    def __init__(self, step=1):
        self.frame = 1000
        self.step = step
        self.calls = []

    def ping(self):
        self.calls.append("ping")
        self.frame += self.step
        return {"frame": self.frame, "fps": 600.0}

    def render(self, path, fmt=".png", width=None, height=None):
        self.calls.append(("render", path, width, height))
        return PNG, {"path": path, "width": width or 1280, "height": height or 720}


@pytest.fixture
def client(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(server, "bridge", lambda: fake)
    monkeypatch.setattr(server, "_warn", lambda _c: "")
    return fake


def test_save_to_writes_the_file_and_answers_in_text(client, tmp_path):
    target = tmp_path / "frame.png"
    reply = server.td_render("/project1/out1", save_to=str(target))
    assert target.read_bytes() == PNG
    assert isinstance(reply, str)
    assert str(target) in reply and "512x" in reply


def test_without_save_to_the_image_comes_back_inline(client):
    reply = server.td_render("/project1/out1")
    assert type(reply).__name__ == "Image"


def test_settle_frames_waits_on_the_application_clock_before_rendering(client):
    server.td_render("/project1/out1", settle_frames=3)
    render_at = next(i for i, c in enumerate(client.calls) if c[0] == "render")
    pings = [c for c in client.calls[:render_at] if c == "ping"]
    # One ping to read the clock, then until it has moved three frames.
    assert len(pings) == 4


def test_a_clock_that_does_not_move_is_said_out_loud(monkeypatch, tmp_path):
    stalled = FakeClient(step=0)
    monkeypatch.setattr(server, "bridge", lambda: stalled)
    monkeypatch.setattr(server, "_warn", lambda _c: "")
    monkeypatch.setattr(settle, "DEFAULT_BUDGET", 0.05)
    reply = server.td_render(
        "/project1/out1", save_to=str(tmp_path / "f.png"), settle_frames=5
    )
    assert "drew 0 of 5" in reply


def test_the_cli_takes_the_same_settle(monkeypatch, tmp_path, capsys):
    fake = FakeClient()
    monkeypatch.setattr(cli, "_client", lambda args, **kw: fake)
    out = tmp_path / "cli.png"
    args = cli.build_parser().parse_args(
        ["render", "/project1/out1", "-o", str(out), "--settle-frames", "2"]
    )
    assert args.func(args) == 0
    assert out.read_bytes() == PNG
    assert fake.calls.count("ping") == 3
