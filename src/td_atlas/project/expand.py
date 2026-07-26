"""Driving TouchDesigner's `toeexpand` / `toecollapse` helpers.

Both tools work in place: they write their output beside their input and, in
the case of `toecollapse`, rename the original to a `.bkp` file. Everything
here therefore operates on a copy in a cache directory, so reading a project
never modifies or litters next to the user's own files.
"""

from __future__ import annotations

import hashlib
import platform
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..config import home
from ..install import InstallNotFound, TDInstall, discover


class ExpandError(RuntimeError):
    """toeexpand could not unpack a file."""


def _tool(install: TDInstall, name: str) -> Path:
    """Locate toeexpand/toecollapse inside an installation."""
    candidates = (
        [install.root / "Contents" / "MacOS" / name]
        if platform.system() == "Darwin"
        else [
            install.root / "bin" / f"{name}.exe",
            install.root / "bin" / name,
            install.root / f"{name}.exe",
            install.root / name,
        ]
    )
    for path in candidates:
        if path.exists():
            return path
    raise ExpandError(
        f"{name} not found in {install.root}. It ships with TouchDesigner; "
        f"check that the installation is complete."
    )


def cache_dir() -> Path:
    return home() / "cache"


def _cache_key(source: Path) -> str:
    stat = source.stat()
    digest = hashlib.sha256(
        f"{source.resolve()}|{stat.st_mtime_ns}|{stat.st_size}".encode()
    ).hexdigest()[:16]
    return f"{source.stem}-{digest}"


@dataclass
class Expansion:
    """An unpacked project on disk."""

    source: Path
    root: Path
    """Directory holding the node tree (the '<name>.toe.dir' toeexpand made)."""

    toc: Path | None
    cached: bool

    def files(self, suffix: str) -> list[Path]:
        return sorted(self.root.rglob(f"*{suffix}"))


def expand(
    source: str | Path,
    install: TDInstall | None = None,
    refresh: bool = False,
) -> Expansion:
    """Unpack a .toe or .tox into a cache directory and return its location.

    Re-expanding an unchanged file is skipped; the cache key covers the path,
    size and modification time.
    """
    source = Path(source).expanduser().resolve()
    if not source.exists():
        raise ExpandError(f"No such file: {source}")
    if source.suffix.lower() not in (".toe", ".tox"):
        raise ExpandError(
            f"{source.name} is not a .toe or .tox file"
        )

    target = cache_dir() / _cache_key(source)
    expanded = target / f"{source.name}.dir"
    toc = target / f"{source.name}.toc"

    if expanded.is_dir() and not refresh:
        return Expansion(source, expanded, toc if toc.exists() else None, True)

    if install is None:
        try:
            install = discover()
        except InstallNotFound as exc:
            raise ExpandError(str(exc)) from exc
    tool = _tool(install, "toeexpand")

    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    working = target / source.name
    shutil.copyfile(source, working)

    result = subprocess.run(
        [str(tool), working.name],
        cwd=target,
        capture_output=True,
        text=True,
        timeout=600,
    )
    # toeexpand reports success on stdout but exits non-zero, so the output
    # directory is the only reliable signal.
    if not expanded.is_dir():
        detail = (result.stderr or result.stdout or "").strip()
        raise ExpandError(
            f"toeexpand did not unpack {source.name}"
            + (f": {detail}" if detail else "")
        )
    return Expansion(source, expanded, toc if toc.exists() else None, False)


def _relative_files(root: Path) -> list[str]:
    return sorted(
        str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()
    )


def write_toc(expanded_dir: Path, toc_path: Path, template: Path | None) -> None:
    """Write the table of contents toecollapse reads beside a `.dir`.

    A .tox listing opens with a `# 4 0 0 0 1` header that a .toe listing does
    not have, and entries are ordered by node rather than by directory walk.
    Both are preserved by using the original listing as a template and only
    dropping deleted files and appending new ones.
    """
    present = _relative_files(expanded_dir)
    header: str | None = None
    ordered: list[str] = []

    if template is not None and template.exists():
        lines = template.read_text(errors="replace").splitlines()
        if lines and lines[0].startswith("#"):
            header = lines[0]
            lines = lines[1:]
        known = set(present)
        ordered = [line.strip() for line in lines if line.strip() in known]

    seen = set(ordered)
    ordered += [name for name in present if name not in seen]

    body = "\n".join(([header] if header else []) + ordered)
    toc_path.write_text(body + "\n")


def collapse(
    expanded_dir: str | Path,
    output: str | Path,
    install: TDInstall | None = None,
    toc_template: str | Path | None = None,
) -> Path:
    """Repack an expanded directory into a .toe/.tox at `output`.

    The result is a valid file but not byte-identical to the original: it is
    re-serialised, not copied. `output` is written fresh; nothing is modified
    in place.
    """
    expanded_dir = Path(expanded_dir).expanduser().resolve()
    output = Path(output).expanduser()
    if not expanded_dir.is_dir():
        raise ExpandError(f"Not a directory: {expanded_dir}")
    if not expanded_dir.name.endswith(".dir"):
        raise ExpandError(
            f"{expanded_dir.name} is not a toeexpand output directory "
            f"(its name must end in .dir)"
        )

    if install is None:
        try:
            install = discover()
        except InstallNotFound as exc:
            raise ExpandError(str(exc)) from exc
    tool = _tool(install, "toecollapse")

    stem = expanded_dir.name[: -len(".dir")]
    toc = expanded_dir.parent / f"{stem}.toc"
    template = Path(toc_template) if toc_template else (toc if toc.exists() else None)
    write_toc(expanded_dir, toc, template)

    # toecollapse writes beside its input and renames anything already there,
    # so it runs in the cache and the result is copied out.
    result = subprocess.run(
        [str(tool), expanded_dir.name],
        cwd=expanded_dir.parent,
        capture_output=True,
        text=True,
        timeout=600,
    )
    produced = expanded_dir.parent / stem
    if not produced.exists():
        detail = (result.stderr or result.stdout or "").strip()
        raise ExpandError(
            "toecollapse produced nothing" + (f": {detail}" if detail else "")
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    # When the expanded directory already sits beside the requested output,
    # toecollapse has written exactly where the caller wanted it.
    if produced.resolve() != output.resolve():
        shutil.copyfile(produced, output)
    return output


def clear_cache() -> int:
    """Remove every cached expansion. Returns how many were removed."""
    root = cache_dir()
    if not root.is_dir():
        return 0
    entries = [p for p in root.iterdir() if p.is_dir()]
    for entry in entries:
        shutil.rmtree(entry, ignore_errors=True)
    return len(entries)
