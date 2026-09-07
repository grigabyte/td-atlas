"""Driving TouchDesigner's `toeexpand` / `toecollapse` helpers.

Both tools work in place, and not in the same way: `toeexpand` writes
`<file>.dir` and `<file>.toc` beside its input and leaves the input alone,
while `toecollapse` moves whatever already sits at its destination aside to
`<file>.bkp1` — `<file>.bkp2` on a second run. Everything
here therefore operates on a copy in a cache directory, so reading a project
never modifies or litters next to the user's own files.
"""

from __future__ import annotations

import hashlib
import os
import platform
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePath

from ..config import home
from ..install import InstallNotFound, TDInstall, discover


class ExpandError(RuntimeError):
    """toeexpand could not unpack a file."""


# Prefix of the working trees `rebuild()` makes in this same directory. They
# are live for the length of one call and are never evicted: deleting one
# mid-collapse surfaces as a corrupt output file, not as a full cache.
WORK_PREFIX = "rebuild-"

# How many expansions the cache keeps, oldest use evicted first.
#
# Measured 2026-09-06 on the cache this project had grown on one machine:
# 2209 expansions, 4.9 GB on disk, 1 026 851 files. Listing the top-level
# entries and stat-ing each of them costs 0.034 s; adding up the file sizes
# beneath them costs 34.1 s. So eviction counts entries and never weighs
# them — a size budget would cost a hundred times the expansion it guards.
# 200 entries is roughly 0.44 GB at that cache's measured mean of 2.2 MB an
# expansion, though one expansion's size is really the source file's: a
# cache of 200 big projects would be larger.
_MAX_CACHED_EXPANSIONS = 200

# How many are dropped by any one expansion. Measured 2026-09-06: deleting a
# cache entry costs 39.9 ms (five entries of the real cache, 3013 files,
# copied to a temporary directory and timed there rather than on the
# originals). A cache that reached 2209 entries before this ceiling existed
# would therefore spend about 80 s inside the first read that trims it —
# a freeze the caller did not ask for. Trimming 25 at a time costs about 1 s
# and the backlog goes with the reads that follow; `td-atlas doctor
# --clear-cache` is the way to take it all back at once.
_MAX_EVICTIONS_PER_CALL = 25


def _tool(install: TDInstall, name: str) -> Path:
    """Locate toeexpand/toecollapse inside an installation.

    The macOS branch is measured: both binaries sit in `Contents/MacOS` beside
    the application, and every test in this repository that expands a file has
    run through it. UNVERIFIED against a TouchDesigner installed on Windows:
    the four locations the else-branch tries are read off Derivative's
    published install tree, not observed. CI's Windows leg (first run
    2026-09-07) runs this function but has no installation to find, so which
    of the four is right — and whether the extensionless names are present
    there at all — is still a question only a Windows machine with
    TouchDesigner on it can settle. Hence all four are tried, not one
    asserted.
    """
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


def cached_expansions() -> list[Path]:
    """Every cache entry, least recently used first. Working trees excluded.

    The exclusion is what both `evict` and `clear_cache` are built on, so it
    is stated once here: a WORK_PREFIX directory belongs to a `rebuild()` that
    may still be running, and removing it mid-collapse produces a corrupt
    output file rather than a freed byte.
    """
    root = cache_dir()
    if not root.is_dir():
        return []
    dated = []
    for entry in root.iterdir():
        if entry.name.startswith(WORK_PREFIX) or not entry.is_dir():
            continue
        try:
            dated.append((entry.stat().st_mtime, entry))
        except OSError:
            # Vanished between the listing and the stat — another process
            # evicting it is the ordinary reason, and it is already gone.
            continue
    return [entry for _, entry in sorted(dated, key=lambda pair: pair[0])]


def cache_summary() -> tuple[int, int, Path]:
    """How many expansions are cached, how many are kept, and where they live."""
    return len(cached_expansions()), _MAX_CACHED_EXPANSIONS, cache_dir()


def evict(keep: int | None = None) -> int:
    """Drop the least recently used expansions past `keep`. Returns how many.

    Called on every expansion, which is why it must stay cheap: it reads one
    mtime per entry and never descends into one. Failure to delete is not
    raised — a full cache is a housekeeping problem and the expansion the
    caller asked for has already succeeded.
    """
    keep = _MAX_CACHED_EXPANSIONS if keep is None else keep
    entries = cached_expansions()
    if len(entries) <= keep:
        return 0
    removed = 0
    over = min(len(entries) - keep, _MAX_EVICTIONS_PER_CALL)
    for entry in entries[:over]:
        shutil.rmtree(entry, ignore_errors=True)
        removed += 1
    return removed


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
        # Touched so eviction is by last use and not by first: a project read
        # every day would otherwise be dropped for having been expanded a
        # month ago, and re-expanded on the next read.
        try:
            os.utime(target, None)
        except OSError:
            pass
        return Expansion(source, expanded, toc if toc.exists() else None, True)

    if install is None:
        try:
            install = discover()
        except InstallNotFound as exc:
            raise ExpandError(str(exc)) from exc
    tool = _tool(install, "toeexpand")

    # Both tolerant of a second process working on the same key: two readers
    # of one file race here, and losing that race raised FileExistsError —
    # an exception outside this module's ExpandError contract, from a
    # condition that is not an error at all.
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
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
    # After the new entry exists, so the one just made is the newest and is
    # never the one evicted.
    evict()
    return Expansion(source, expanded, toc if toc.exists() else None, False)


def _toc_entry(relative: PurePath) -> str:
    """One line of a toeexpand table of contents.

    Measured: every .toc toeexpand wrote in this project's cache separates
    entries with '/' ('beatCHOP/example1.n'), so str() — which on Windows
    would emit 'beatCHOP\\example1.n' — must not be used. A mismatch there is
    silent: the template lines stop matching the files on disk, the original
    ordering is lost, and the listing handed back to toecollapse is written
    in a separator it was never seen to use.
    UNVERIFIED: the '/' was measured in toeexpand's macOS output only, and
    CI's Windows leg cannot add to that — it has no toeexpand to run.
    """
    return relative.as_posix()


def _relative_files(root: Path) -> list[str]:
    return sorted(
        _toc_entry(p.relative_to(root)) for p in root.rglob("*") if p.is_file()
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
    """Remove every cached expansion. Returns how many were removed.

    Eviction keeps the cache from growing without end; this is for emptying
    one that already has. Reached from `td-atlas doctor --clear-cache`, so a
    user who wants the disk back does not have to know the directory layout.

    "Every cached expansion" is the whole of what the cache is *for*, and not
    quite the whole of what is in the directory: `rebuild()` puts its working
    tree here too, under WORK_PREFIX, and this leaves those alone for the same
    reason `evict` does — deleting one out from under a rebuild running in
    another process surfaces as a corrupt output file, which is a worse
    failure than a directory that was not reclaimed. `rebuild()` removes its
    own tree in a `finally`, so one survives only a process that was killed
    mid-rebuild; if `cache_dir()` still holds a `rebuild-*` directory when no
    rebuild is running, it is that, and it is safe to delete by hand.
    """
    entries = cached_expansions()
    for entry in entries:
        shutil.rmtree(entry, ignore_errors=True)
    return len(entries)
