"""Branching over the text: save a network's state, list, restore, compare.

An agent trying two versions of the same network needs four things it did not
have: keep the state it is about to leave, see what it has kept, go back to
any of them, and see the difference between two. `serialize.py` prints the
text, `rebuild.py` writes it back and `diff.py` compares two loaded projects;
this module is the store the three of them were missing a second source for.

**What a variant holds, and why it was settled by measuring.** The text is not
self-sufficient — `rebuild.py` is a patcher and needs the original file — so
three storage shapes were possible: the text alone, the text plus a copy of
the `.toe`/`.tox`, or the text plus a copy of the `toeexpand` tree. Measured
over three shipped palette components (21, 35 and 4,080 operators):

| file | operators | source | text | text.gz | expansion | text only | +source | +expansion |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| checker.tox | 21 | 3,942 B | 17,955 B | 2,326 B | 238,448 B | 0.001 s | 0.001 s | 0.008 s |
| battery.tox | 35 | 3,870 B | 25,315 B | 2,567 B | 22,551 B | 0.002 s | 0.002 s | 0.012 s |
| kantanMapper.tox | 4,080 | 308,928 B | 6,111,237 B | 299,592 B | 3,076,502 B | 0.259 s | 0.260 s | 1.723 s |

What settles it is correctness, not size: `rebuild.py` is a patcher, so the
text alone cannot produce a `.toe` at all — a restore without the source is
not a cheaper restore, it is no restore. Size only fails to argue back. A
`.toe` is a compressed container, so across the 277 shipped components the
text runs a median 5.3x the size of the file it was printed from (0.01x to
24x; for 45 of them the text is the smaller of the two), and the copy adds a
median 19% on top of the text and no measurable time. The expansion
tree costs a further half of the text again, 9,647 files for the large case,
and 6.6x the wall time, in exchange for nothing the file copy does not give —
`expand()` reproduces it from the copy on demand and caches it.

So a variant is **the text plus a byte copy of the source file**, and two
things follow:

- **Restore is a file copy, not a rebuild.** The stored copy *is* the state at
  save time; the text was printed from it. Nothing is repacked, so
  `toecollapse` never runs and never renames a user's file to `.bkp`.
- **A changed original cannot corrupt a restore.** The manifest still records
  the original's path, size and SHA-256, and `drift()` reports `unchanged`,
  `changed` or `missing` — but that is information, not a gate, because the
  restore does not read the original at all. The refusals live where they
  still mean something: a stored copy whose hash no longer matches its
  manifest, an output path that already exists, and a label already taken.

Variants are service data, not the user's documents, so they live under
`~/.td-atlas/variants/` rather than beside the `.toe` where decision 26 puts
the network text. The cost of that choice is that they do not travel with the
project and are not covered by the user's own version control; the benefit is
that nothing this module does can write into a directory the user works in.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from .. import config as cfg
from .diff import ProjectDiff
from .diff import diff as diff_projects
from .model import TypeResolver, load_file
from .serialize import to_text

LABEL_RULE = re.compile(r"[\w.-]+")

MANIFEST = "manifest.json"
TEXT_NAME = "network.json"
COPY_DIR = "source"


class VariantError(RuntimeError):
    """A variant could not be saved, found, restored or compared.

    It carries the hint key for the refusal rather than leaving the MCP layer
    to recognise it by wording: `hints.classify` reads `.key`, so rewording a
    message cannot silently drop an agent back onto the unmapped hint.
    """

    def __init__(self, message: str, key: str = "variant_unknown"):
        super().__init__(message)
        self.key = key


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------


def store_dir() -> Path:
    return cfg.home() / "variants"


def store_key(source: str | Path) -> str:
    """The directory name that groups every variant of one source file.

    Keyed by the resolved path and nothing else. `expand._cache_key` folds in
    size and mtime, which is right for a cache — a changed file is a different
    expansion — and wrong here: editing the project must not orphan the
    variants taken from it, which is the whole point of keeping them.
    """
    resolved = Path(source).expanduser().resolve()
    digest = hashlib.sha256(str(resolved).encode()).hexdigest()[:16]
    stem = resolved.stem or "project"
    safe = "".join(c if (c.isalnum() or c in "._-") else "_" for c in stem)
    return f"{safe}-{digest}"


def source_store(source: str | Path) -> Path:
    return store_dir() / store_key(source)


def check_label(label: str) -> str:
    """The label as a directory name, or a refusal saying why not."""
    if not LABEL_RULE.fullmatch(label or ""):
        raise VariantError(
            f"{label!r} is not a usable variant label: use letters, digits, "
            f"dot, dash and underscore only.",
            key="bad_label",
        )
    if label in (".", ".."):
        raise VariantError(
            f"{label!r} is not a usable variant label.", key="bad_label"
        )
    return label


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# One saved variant
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Variant:
    """One saved state: its manifest, its text and its copy of the source."""

    directory: Path
    data: dict

    @property
    def label(self) -> str:
        return str(self.data.get("label", self.directory.name))

    @property
    def note(self) -> str:
        return str(self.data.get("note") or "")

    @property
    def saved_at(self) -> float:
        return float(self.data.get("saved_at") or 0.0)

    @property
    def origin(self) -> Path:
        return Path(str(self.data.get("source", "")))

    @property
    def text_path(self) -> Path:
        return self.directory / TEXT_NAME

    @property
    def copy_path(self) -> Path:
        return self.directory / COPY_DIR / str(self.data.get("copy_name", ""))

    def text(self) -> str:
        return self.text_path.read_text(encoding="utf-8")

    def drift(self) -> str:
        """Whether the original file still looks like the one that was saved.

        Reported, never enforced: a restore reads the stored copy, so a changed
        original cannot make it write the wrong thing. Compared by hash rather
        than mtime, because a touched file is not a changed one.
        """
        origin = self.origin
        if not origin.exists():
            return "missing"
        try:
            if origin.stat().st_size != int(self.data.get("source_size", -1)):
                return "changed"
            return (
                "unchanged"
                if _sha256(origin) == self.data.get("source_sha256")
                else "changed"
            )
        except OSError:
            return "missing"

    def verify(self) -> None:
        """Refuse if the stored copy is not the one the manifest describes."""
        copy = self.copy_path
        if not copy.exists():
            raise VariantError(
                f"variant '{self.label}' has lost its copy of the source "
                f"({copy} is gone), so there is nothing to restore. The text "
                f"is still at {self.text_path}.",
                key="variant_corrupt",
            )
        if _sha256(copy) != self.data.get("copy_sha256"):
            raise VariantError(
                f"variant '{self.label}' is corrupt: the stored copy at "
                f"{copy} no longer hashes to what its manifest records. "
                f"Nothing was restored — delete the variant directory and "
                f"save it again from the project.",
                key="variant_corrupt",
            )


def _read_variant(directory: Path) -> Variant | None:
    manifest = directory / MANIFEST
    if not manifest.is_file():
        return None
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return Variant(directory=directory, data=data)


# ---------------------------------------------------------------------------
# The four actions
# ---------------------------------------------------------------------------


def save(
    source: str | Path,
    label: str,
    note: str = "",
    path: str | None = None,
    resolver: TypeResolver | None = None,
) -> Variant:
    """Keep the current state of `source` under `label`.

    The text is printed first: a file that cannot be read is not a variant
    worth keeping, and finding that out before anything is written leaves no
    half-made directory behind.
    """
    check_label(label)
    origin = Path(source).expanduser().resolve()
    if not origin.exists():
        raise VariantError(f"No such file: {origin}", key="project_unreadable")

    directory = source_store(origin) / label
    if (directory / MANIFEST).exists():
        raise VariantError(
            f"a variant named '{label}' already exists for {origin.name} "
            f"(at {directory}). Nothing was overwritten — choose another "
            f"label, or remove that directory to replace it.",
            key="variant_label_taken",
        )

    project = load_file(origin, resolver=resolver)
    text = to_text(project, path=path or None)

    stat = origin.stat()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / COPY_DIR).mkdir(exist_ok=True)
    copy = directory / COPY_DIR / origin.name
    shutil.copyfile(origin, copy)
    (directory / TEXT_NAME).write_text(text, encoding="utf-8")

    data = {
        "label": label,
        "note": note,
        "saved_at": time.time(),
        "source": str(origin),
        "source_size": stat.st_size,
        "source_mtime_ns": stat.st_mtime_ns,
        "source_sha256": _sha256(origin),
        "copy_name": origin.name,
        "copy_sha256": _sha256(copy),
        "path": path or "/",
        "operator_count": sum(1 for _ in project.walk()),
        "text_bytes": len(text.encode()),
    }
    # The manifest is written last, so an interrupted save leaves a directory
    # `_read_variant` skips rather than a variant that lies about its copy.
    (directory / MANIFEST).write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return Variant(directory=directory, data=data)


def variants(source: str | Path) -> list[Variant]:
    """Every variant saved from `source`, newest first."""
    root = source_store(source)
    if not root.is_dir():
        return []
    found = [_read_variant(child) for child in sorted(root.iterdir()) if child.is_dir()]
    return sorted(
        (v for v in found if v is not None),
        key=lambda v: (-v.saved_at, v.label),
    )


def sources() -> list[tuple[Path, list[Variant]]]:
    """Every source file that has variants, with them, newest source first."""
    root = store_dir()
    if not root.is_dir():
        return []
    out: list[tuple[Path, list[Variant]]] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        found = [
            v
            for v in (_read_variant(g) for g in sorted(child.iterdir()) if g.is_dir())
            if v is not None
        ]
        if found:
            found.sort(key=lambda v: (-v.saved_at, v.label))
            out.append((found[0].origin, found))
    out.sort(key=lambda pair: -pair[1][0].saved_at)
    return out


def find(source: str | Path, label: str) -> Variant:
    check_label(label)
    directory = source_store(source) / label
    variant = _read_variant(directory)
    if variant is None:
        known = [v.label for v in variants(source)]
        listing = ", ".join(known) if known else "none saved yet"
        raise VariantError(
            f"no variant named '{label}' for {Path(source).name} "
            f"(saved: {listing}).",
            key="variant_unknown",
        )
    return variant


def restore(
    source: str | Path, label: str, output: str | Path
) -> tuple[Path, Variant]:
    """Write a saved state out as a file, without touching anything of the user's.

    A copy, not a repack: the bytes that come out are the bytes that went in.
    `output` may name a directory, in which case the file keeps the name it had
    when it was saved — which is also what keeps `project text` on the restored
    file byte-identical to the variant's stored text, since the dump records
    the file's name.
    """
    variant = find(source, label)
    variant.verify()

    target = Path(output).expanduser()
    if target.is_dir():
        target = target / variant.copy_path.name
    if target.exists():
        raise VariantError(
            f"{target} already exists. Restoring writes the file whole and "
            f"will not overwrite anything: name a path that does not exist "
            f"yet, then compare the two.",
            key="variant_output_exists",
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(variant.copy_path, target)
    return target, variant


def compare(
    source: str | Path,
    before: str,
    after: str,
    include_text: bool = True,
    resolver: TypeResolver | None = None,
) -> tuple[ProjectDiff, Variant, Variant]:
    """Compare two saved variants with the semantic diff that already exists.

    No second diff and no rebuild: both stored copies are real `.toe`/`.tox`
    files, so `diff.diff` sees them exactly as it sees two files on disk.
    """
    one, two = find(source, before), find(source, after)
    one.verify()
    two.verify()
    result = diff_projects(
        load_file(one.copy_path, resolver=resolver),
        load_file(two.copy_path, resolver=resolver),
        include_text=include_text,
    )
    return result, one, two


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render_list(items: list[Variant], header: str | None = None) -> str:
    if not items:
        return "no variants saved"
    lines = [header] if header else []
    for variant in items:
        stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(variant.saved_at))
        head = (
            f"  {variant.label}  {stamp}  "
            f"{variant.data.get('operator_count', '?')} operators, "
            f"{variant.data.get('text_bytes', '?')} B of text"
        )
        if variant.data.get("path", "/") != "/":
            head += f", text covers {variant.data['path']}"
        lines.append(head)
        drift = variant.drift()
        if drift != "unchanged":
            lines.append(
                f"      original since save: {drift} "
                f"(restore reads this variant's own copy, so it is unaffected)"
            )
        if variant.note:
            lines.append(f"      {variant.note}")
    return "\n".join(lines)
