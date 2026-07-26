"""Locating a TouchDesigner installation and the atom sources inside it.

Everything td-atlas needs in order to build its offline index ships inside the
application bundle, so an index is always exact for the build it was made from
rather than scraped from a wiki that may describe a different release.
"""

from __future__ import annotations

import os
import platform
import re
from dataclasses import dataclass
from pathlib import Path

# Relative to the platform-specific "tfs" resource root.
_PARAM_HELP = "Config/TDParameterHelp.json"
_PARAM_HELP_EXPERIMENTAL = "Config/TDParameterHelpExperimental.json"
_OFFLINE_HELP = "Samples/Learn/OfflineHelp"
_SNIPPETS = "Samples/Learn/OPSnippets/Snippets"
_PALETTE = "Samples/Palette"
_COMMAND_HELP = "Config/Help/command.help"
_EXPR_HELP = "Config/Help/exprhelp"


class InstallNotFound(RuntimeError):
    """Raised when no TouchDesigner installation could be located."""


@dataclass(frozen=True)
class TDInstall:
    """A located TouchDesigner installation."""

    root: Path
    """Application root: the .app bundle on macOS, the install dir elsewhere."""

    tfs: Path
    """The 'tfs' resource tree holding help, samples and config."""

    version: str
    """Build string, e.g. '2025.32460'."""

    executable: Path | None
    """Launchable binary, when one could be identified."""

    @property
    def param_help(self) -> Path:
        return self.tfs / _PARAM_HELP

    @property
    def param_help_experimental(self) -> Path:
        return self.tfs / _PARAM_HELP_EXPERIMENTAL

    @property
    def offline_help(self) -> Path:
        """The wiki mirror root (the single 'https.docs.derivative.ca' dir)."""
        base = self.tfs / _OFFLINE_HELP
        mirrors = sorted(p for p in base.glob("*") if p.is_dir())
        return mirrors[0] if mirrors else base

    @property
    def snippets(self) -> Path:
        return self.tfs / _SNIPPETS

    @property
    def palette(self) -> Path:
        """Ready-made components shipped in the palette browser."""
        return self.tfs / _PALETTE

    @property
    def command_help(self) -> Path:
        return self.tfs / _COMMAND_HELP

    @property
    def expr_help(self) -> Path:
        return self.tfs / _EXPR_HELP

    def missing_sources(self) -> list[str]:
        """Names of expected atom sources that are absent from this install."""
        expected = {
            "TDParameterHelp.json": self.param_help,
            "OfflineHelp": self.offline_help,
            "OPSnippets": self.snippets,
            "command.help": self.command_help,
            "exprhelp": self.expr_help,
        }
        return [name for name, path in expected.items() if not path.exists()]


def _version_from_macos_bundle(app: Path) -> str:
    plist = app / "Contents" / "Info.plist"
    if not plist.exists():
        return "unknown"
    # Read the plist without shelling out: the short version string is stored
    # adjacent to its key in both the XML and binary encodings.
    data = plist.read_bytes()
    match = re.search(
        rb"CFBundleShortVersionString.{0,64}?(\d{4}\.\d+)", data, re.DOTALL
    )
    return match.group(1).decode() if match else "unknown"


def _candidates_macos() -> list[Path]:
    roots = [Path("/Applications"), Path.home() / "Applications"]
    found: list[Path] = []
    for root in roots:
        if root.is_dir():
            found.extend(sorted(root.glob("TouchDesigner*.app")))
    return found


def _candidates_windows() -> list[Path]:
    found: list[Path] = []
    for env in ("ProgramFiles", "ProgramW6432", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if not base:
            continue
        derivative = Path(base) / "Derivative"
        if derivative.is_dir():
            found.extend(sorted(derivative.glob("TouchDesigner*")))
    return [p for p in found if p.is_dir()]


def _candidates_linux() -> list[Path]:
    found: list[Path] = []
    for base in (Path("/opt"), Path.home()):
        if base.is_dir():
            found.extend(sorted(base.glob("TouchDesigner*")))
    return [p for p in found if p.is_dir()]


def _build(root: Path) -> TDInstall | None:
    """Turn a candidate install root into a TDInstall, if it looks valid."""
    system = platform.system()
    if system == "Darwin":
        tfs = root / "Contents" / "Resources" / "tfs"
        executable = root / "Contents" / "MacOS" / "TouchDesigner"
        version = _version_from_macos_bundle(root)
    else:
        # Windows and Linux keep the resource tree beside the binary.
        tfs = next(
            (p for p in (root / "Config", root / "Samples") if p.exists()), None
        )
        tfs = root if tfs else root
        exe_names = ("TouchDesigner.exe", "TouchDesigner")
        executable = next(
            (root / "bin" / n for n in exe_names if (root / "bin" / n).exists()),
            None,
        ) or next((root / n for n in exe_names if (root / n).exists()), None)
        match = re.search(r"(\d{4}\.\d+)", root.name)
        version = match.group(1) if match else "unknown"

    if not tfs.is_dir():
        return None
    return TDInstall(
        root=root,
        tfs=tfs,
        version=version,
        executable=executable if executable and executable.exists() else None,
    )


def _version_key(install: TDInstall) -> tuple[int, int]:
    match = re.match(r"(\d+)\.(\d+)", install.version)
    return (int(match.group(1)), int(match.group(2))) if match else (0, 0)


def discover(explicit: str | Path | None = None) -> TDInstall:
    """Locate a TouchDesigner install, preferring the newest build found.

    An explicit path, then TD_ATLAS_INSTALL, then the platform's usual
    locations. Raises InstallNotFound when nothing usable turns up.
    """
    override = explicit or os.environ.get("TD_ATLAS_INSTALL")
    if override:
        root = Path(override).expanduser()
        if not root.exists():
            raise InstallNotFound(f"No TouchDesigner installation at {root}")
        install = _build(root)
        if install is None:
            raise InstallNotFound(
                f"{root} exists but holds no TouchDesigner resource tree "
                f"(expected a 'tfs' directory or Config/ + Samples/)"
            )
        return install

    finders = {
        "Darwin": _candidates_macos,
        "Windows": _candidates_windows,
        "Linux": _candidates_linux,
    }
    candidates = finders.get(platform.system(), _candidates_linux)()
    installs = [i for i in (_build(c) for c in candidates) if i is not None]
    if not installs:
        raise InstallNotFound(
            "No TouchDesigner installation found. Pass --install-path or set "
            "TD_ATLAS_INSTALL to the application directory."
        )
    return max(installs, key=_version_key)
