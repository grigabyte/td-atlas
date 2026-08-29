"""Building the bridge as a drag-and-droppable `.tox`, without TouchDesigner.

`td-atlas install` asks the user to paste a line into the textport, which then
builds the bridge network from `component/bootstrap.py`. This module produces
the same network as a file instead, so installing is a drag into the network
editor.

Nothing here starts TouchDesigner. The `.tox` is assembled as the directory
tree `toeexpand` would have written for it and handed to `toecollapse`, which
is a plain command line tool that ships in the same bundle. Every detail below
was measured by expanding shipped palette components (see docs/formats.md):

- the tree root holds `.build` plus `<name>.n` for the top node, and a
  `<name>/` directory for its children;
- `.build` is required — without it `toecollapse` warns and writes a 4-byte
  file — and its first line is the *file format* version (`099`), not the
  application build;
- the `.toc` listing beside the `.dir` must name every file, and a `.tox`
  listing opens with a `# 4 0 0 0 1` header. `collapse()` reuses an existing
  listing as its template, so the header is written here and preserved there.

A round trip through `toecollapse` and back through `toeexpand` was verified by
diffing the two trees; the test checks the round trip node by node — types, the
wiring, the port, and the handler payload byte for byte.
"""

from __future__ import annotations

import shutil
import struct
from datetime import datetime, timezone
from pathlib import Path

from .. import config as cfg
from ..component import handler as bridge_handler
from ..install import InstallNotFound, TDInstall, discover
from .expand import ExpandError, cache_dir, collapse

COMPONENT_NAME = "tdatlas"
HANDLER_DAT = "handler"
SERVER_DAT = "bridge"
PANEL_TOP = "panel"

DEFAULT_OUTPUT = Path("release") / "TdAtlas.tox"

# The format version toeexpand stamps for 2025-era files. It is not the
# application build; that goes on the next line and is informational.
_FORMAT_VERSION = "099"

_HANDLER_SOURCE = Path(__file__).parent.parent / "component" / "handler.py"


def _payload(body: bytes) -> bytes:
    """A `.text` file: the 27-byte prologue, then the bytes verbatim."""
    return b"2\n*" + struct.pack(">6I", 1, 1, 1, 1, 2, len(body)) + body


def _node(op_type: str, x: int, y: int) -> str:
    """A `.n` file: FAMILY:type, placement, flags, colour, end."""
    return (
        f"{op_type}\n"
        f"tile {x} {y} 130 90\n"
        "flags =  parlanguage 0\n"
        "color 0.55 0.55 0.55 \n"
        "end\n"
    )


def _parms(values: list[tuple[str, str]]) -> str:
    """A `.parm` file: `name <flags> <constant>` between `?` sentinels.

    Flag 0 means a plain constant; expression mode (bit 0x10) is not used here.
    """
    lines = [f"{name} 0 {value}" for name, value in values]
    return "?\n" + "".join(line + "\n" for line in lines) + "?\n"


def _build_stamp(install: TDInstall) -> str:
    when = datetime.now(timezone.utc).strftime("%a %b %d %H:%M:%S %Y")
    return (
        f"version {_FORMAT_VERSION}\n"
        f"build {install.version}\n"
        f"time {when}\n"
    )


def write_tree(root: Path, port: int, install: TDInstall) -> list[str]:
    """Lay out the expanded form of the bridge under `root` (a `*.tox.dir`).

    Returns the file names it wrote, in the order the listing wants them.
    """
    handler_source = _HANDLER_SOURCE.read_bytes()
    inner = root / COMPONENT_NAME
    inner.mkdir(parents=True, exist_ok=True)

    files: list[tuple[str, bytes]] = [
        (".build", _build_stamp(install).encode()),
        # The base COMP the two DATs live in.
        (f"{COMPONENT_NAME}.n", _node("COMP:base", -400, 400).encode()),
        # The handler module, held as the text of a Text DAT.
        (f"{COMPONENT_NAME}/{HANDLER_DAT}.n", _node("DAT:text", 0, 0).encode()),
        (
            f"{COMPONENT_NAME}/{HANDLER_DAT}.parm",
            _parms([("language", "python")]).encode(),
        ),
        (f"{COMPONENT_NAME}/{HANDLER_DAT}.text", _payload(handler_source)),
        # The server, pointed at the handler by sibling name.
        (f"{COMPONENT_NAME}/{SERVER_DAT}.n", _node("DAT:webserver", 200, 0).encode()),
        # The status panel. Laid out here as well as in component/bootstrap.py
        # so that the drag-and-drop install and the textport install produce
        # the same COMP; both take the parameters from `handler.PANEL_PARS`
        # rather than being kept in step by hand.
        (f"{COMPONENT_NAME}/{PANEL_TOP}.n", _node("TOP:text", 0, 200).encode()),
        (
            f"{COMPONENT_NAME}/{PANEL_TOP}.parm",
            _parms(
                [("text", bridge_handler.PANEL_PLACEHOLDER)]
                + list(bridge_handler.PANEL_PARS)
            ).encode(),
        ),
        (
            f"{COMPONENT_NAME}/{SERVER_DAT}.parm",
            _parms(
                [
                    ("active", "1"),
                    ("port", str(port)),
                    ("callbacks", HANDLER_DAT),
                ]
            ).encode(),
        ),
    ]

    for name, data in files:
        (root / name).write_bytes(data)
    return [name for name, _ in files]


def build_tox(
    output: str | Path | None = None,
    install: TDInstall | None = None,
    port: int | None = None,
) -> Path:
    """Assemble the bridge `.tox` and copy it to `output`.

    The tree and `toecollapse` both work inside the cache, because
    `toecollapse` renames whatever already sits at its destination to `.bkp`
    and must never do that beside a user's own files.
    """
    output = Path(output or DEFAULT_OUTPUT).expanduser()
    if install is None:
        try:
            install = discover()
        except InstallNotFound as exc:
            raise ExpandError(str(exc)) from exc
    if port is None:
        port = int(cfg.load_config().get("port", cfg.DEFAULT_PORT))

    work = cache_dir() / "release-tox"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)

    name = output.name if output.suffix.lower() == ".tox" else output.name + ".tox"
    expanded = work / f"{name}.dir"
    names = write_tree(expanded, port, install)

    # Seed the listing with its header; collapse() keeps it as the template.
    (work / f"{name}.toc").write_text(
        "# 4 0 0 0 1\n" + "".join(n + "\n" for n in names)
    )

    return collapse(expanded, output, install=install)
