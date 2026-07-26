"""Capturing a TOP over time as a contact sheet.

A single render tells you what a frame looks like; it says nothing about
motion, and nothing at all about an effect whose whole nature is temporal — a
strobe judged from one frame is a coin toss. Frames cannot be collected inside
one request either, because a request runs during a cook and time does not
advance until it returns.

So the host drives the sampling: capture, let TouchDesigner run, capture again,
then ask it to tile what it collected.
"""

from __future__ import annotations

import struct
import time
import zlib
from pathlib import Path

from .client import BridgeClient


def _png(width: int, height: int, rgb: bytes) -> bytes:
    """Encode 8-bit RGB into a PNG. Written out to avoid a Pillow dependency."""
    stride = width * 3
    raw = bytearray()
    for row in range(height):
        raw.append(0)  # filter type 0 (None)
        raw += rgb[row * stride : (row + 1) * stride]

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    header = struct.pack(">2I5B", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
        + chunk(b"IEND", b"")
    )


def contact_sheet(
    client: BridgeClient,
    path: str,
    output: str | Path,
    frames: int = 9,
    interval: float = 0.12,
    columns: int = 3,
    width: int = 320,
) -> dict:
    """Sample a TOP over time and write a tiled proof sheet.

    `interval` is wall-clock seconds between captures, so the sheet spans
    roughly `frames * interval` seconds of the running composition.
    """
    for index in range(frames):
        client.call("capture", path=path, reset=(index == 0))
        if index < frames - 1:
            time.sleep(interval)

    sheet = client.call("contact_sheet", columns=columns, width=width)
    import base64

    pixels = base64.b64decode(sheet["data"])
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(_png(sheet["width"], sheet["height"], pixels))

    return {
        "output": str(output),
        "frames": sheet["count"],
        "grid": f"{sheet['columns']}x{sheet['rows']}",
        "size": f"{sheet['width']}x{sheet['height']}",
        "span_seconds": round(frames * interval, 2),
    }
