#!/usr/bin/env python3
"""Build the `.mcpb` bundle, and the registry submission that points at it.

One command, from a clean checkout:

    .venv/bin/python scripts/build_mcpb.py

What it produces, all under `dist/`:

  td-atlas-<version>.mcpb   the bundle a user opens to install the server
  server.json               the official-registry submission, carrying the
                            SHA-256 of the bundle built alongside it
  build.json                which commit the bundle was packed from, so
                            `publish.sh` can refuse a stale one
  mcpb-stage/               the exact tree that was packed, kept for inspection

Nothing here publishes. `scripts/publish.sh` is the owner's separate step.

Two rules this script exists to enforce, both of which a hand-written
manifest breaks within one release:

**One source for the metadata.** Every field the manifest and the submission
share with the package — version, description, author, repository, keywords,
licence, the Python floor, the supported platforms — is read out of
`pyproject.toml`. `packaging/manifest.json` is a generated artefact that
happens to be committed so a reviewer can read it; `tests/test_packaging.py`
fails if it drifts from what this script would write.

**Only tracked files get packed.** The staging tree comes from `git archive`,
not from a copy of the working directory with exclusions bolted on. That is
not a tidiness preference: the atom index (`~/.td-atlas/atlas.db`) contains
machine-specific values — device menus carry the real names of this machine's
audio hardware — and the bridge token lives in `~/.td-atlas/config.json` at
0600. Neither is tracked, so neither can reach the bundle by accident; an
exclusion list would have to name them, and would miss the next such file.
`_assert_bundle_is_clean` checks the packed listing against an allowlist
anyway, because a gate that reruns is worth more than an argument.

**Every file written here names its encoding.** `write_text` without one uses
the locale encoding, which on Windows is cp1252: the em dash in
`LONG_DESCRIPTION` came back out of `packaging/manifest.json` as three
characters, and `tests/test_packaging.py` — reading it the same unpinned way —
reported the committed manifest stale on Windows only (measured in CI, run
34111353871, 2026-09-07). A manifest built there would have shipped inside the
bundle in cp1252, which is not a JSON encoding any host is required to read.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
STAGE = DIST / "mcpb-stage"
MANIFEST = ROOT / "packaging" / "manifest.json"
MCPBIGNORE = ROOT / "packaging" / ".mcpbignore"

# The MCPB spec version this manifest is written against. 0.4 is the first
# to define `server.type = "uv"`; the rest of the manifest is 0.3 shaped.
# Spec: https://github.com/anthropics/mcpb/blob/main/MANIFEST.md
MANIFEST_VERSION = "0.4"

# The official registry validates submissions against this schema.
SERVER_SCHEMA = "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json"

# GitHub namespace authentication: the registry only accepts `io.github.<user>/*`
# from someone who can log in as that user.
REGISTRY_NAME = "io.github.grigabyte/td-atlas"

# What `git archive` puts into the staging tree. Source, metadata, licence,
# readme — the four things the bundle needs and nothing else. `plugin/` is
# deliberately absent: it installs as a Claude Code plugin (see
# `.claude-plugin/marketplace.json`), not as payload inside an MCP bundle.
TRACKED_PATHS = ("src/td_atlas", "pyproject.toml", "LICENSE", "README.md")

# Anything packed must match one of these. A `.db` file, a `config.json`, a
# `.venv` or a stray `__pycache__` matches none of them and fails the build.
ALLOWED_IN_BUNDLE = (
    "manifest.json",
    "server.py",
    ".mcpbignore",
    "pyproject.toml",
    "LICENSE",
    "README.md",
)
ALLOWED_PREFIX = "src/td_atlas/"

# Names that must never appear in a bundle, whatever else the allowlist says.
# Belt and braces: the allowlist is the real gate, this is the message that
# tells a future reader *why* the build stopped.
FORBIDDEN_SUBSTRINGS = ("atlas.db", "config.json", ".venv", "__pycache__", ".pyc")

PLATFORM_BY_CLASSIFIER = {
    "Operating System :: MacOS": "darwin",
    "Operating System :: Microsoft :: Windows": "win32",
    "Operating System :: POSIX :: Linux": "linux",
}


def _say(message: str) -> None:
    print(message, file=sys.stderr)


def _run(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(argv, check=True, cwd=ROOT, **kwargs)


# -- metadata, read once ----------------------------------------------------

def project_metadata() -> dict:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)["project"]


def _licence(classifiers: list[str]) -> str | None:
    """The SPDX identifier, taken from the classifier rather than re-typed."""
    for line in classifiers:
        if line.startswith("License :: OSI Approved :: MIT"):
            return "MIT"
    return None


def _platforms(classifiers: list[str]) -> list[str]:
    """Only platforms the package actually claims.

    `docs/compatibility.md` is explicit that TouchDesigner on Windows is
    unverified (the OS
    itself runs in CI) and that Linux is unsupported, and the classifiers were
    trimmed to match on purpose. Deriving the manifest's
    `compatibility.platforms` from them keeps one statement of that fact
    instead of two that can disagree.
    """
    found = [
        PLATFORM_BY_CLASSIFIER[line]
        for line in classifiers
        if line in PLATFORM_BY_CLASSIFIER
    ]
    if not found:
        raise SystemExit("pyproject declares no Operating System classifier")
    return found


# -- the manifest -----------------------------------------------------------

def build_manifest() -> dict:
    meta = project_metadata()
    classifiers = meta.get("classifiers", [])
    urls = meta.get("urls", {})
    author = meta["authors"][0]

    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "name": meta["name"],
        "display_name": "TouchDesigner Atlas",
        "version": meta["version"],
        "description": meta["description"],
        "long_description": LONG_DESCRIPTION.strip(),
        "author": {"name": author["name"], "url": urls.get("Homepage")},
        "repository": {"type": "git", "url": urls["Repository"]},
        "homepage": urls.get("Homepage"),
        "documentation": urls.get("Homepage"),
        "support": urls.get("Issues"),
        "server": {
            # "uv" rather than "python": a "python" bundle must carry its own
            # dependencies under server/lib, which for `mcp` means the
            # pydantic-core wheel — a compiled artefact for one CPU and one
            # Python minor. Vendoring it would make the bundle unreproducible
            # from a clean checkout and silently narrower than the platform
            # list above. With "uv" the host resolves dependencies from the
            # pyproject that ships inside the bundle.
            "type": "uv",
            # A launcher generated into the bundle root, not `src/td_atlas/
            # cli.py`. For the uv type the spec makes `mcp_config` optional
            # and lets the host run `entry_point` itself, so entry_point has
            # to be a file that works when run as a script — and cli.py is
            # not: it opens with `from . import config as cfg`, which raises
            # "attempted relative import with no known parent package"
            # before argparse is reached, and even past that it would be
            # given no subcommand. LAUNCHER is the same call the printed
            # command makes, one frame in.
            "entry_point": "server.py",
            "mcp_config": {
                "command": "uv",
                # The launch string is deliberately the same one
                # `cli.mcp_command()` prints: `-m td_atlas.cli mcp`. What
                # changes is only where the interpreter comes from — a bundle
                # has no `sys.executable` of the user's to point at, so uv
                # supplies one for the project that ships inside the bundle.
                # `--no-dev` keeps the dev dependency group (pytest) out of
                # the environment the server runs in.
                "args": [
                    "run",
                    "--directory",
                    "${__dirname}",
                    "--no-dev",
                    "python",
                    "-m",
                    "td_atlas.cli",
                    "mcp",
                ],
            },
        },
        # No static `tools` array. The alternative was to generate one from
        # the server module, which reads better in an install dialog and was
        # tried first — but it makes the manifest go stale every time a tool
        # is added or a docstring's first line is reworded, and a manifest
        # that names a tool the bundle does not ship is exactly the confident
        # wrong answer this project refuses elsewhere. `tools_generated`
        # tells the host to ask the server, which cannot disagree with it.
        "tools_generated": True,
        "keywords": meta.get("keywords", []),
        "license": _licence(classifiers),
        "compatibility": {
            # No `claude_desktop` constraint: which host versions accept a
            # uv-type bundle has not been measured here, and a guessed floor
            # would either lock out working hosts or promise nothing.
            "platforms": _platforms(classifiers),
            "runtimes": {"python": meta["requires-python"]},
        },
    }
    return {key: value for key, value in manifest.items() if value is not None}


LAUNCHER = '''"""Bundle entry point: `td-atlas mcp`, reached as a script.

Generated by scripts/build_mcpb.py — edit that, not this.

A host running an MCPB bundle of the `uv` type may launch `mcp_config` or,
since `mcp_config` is optional for that type, the manifest's `entry_point`
directly. This file is the second path, and it exists because the first
candidate for it — `src/td_atlas/cli.py` — cannot be run as a script: it
starts with a relative import and expects a subcommand. Going through
`cli.main` rather than calling the server directly keeps both paths on one
code path, the one `cli.mcp_command()` measured.
"""

import sys

from td_atlas.cli import main

if __name__ == "__main__":
    sys.argv = [sys.argv[0], "mcp"]
    raise SystemExit(main())
'''


LONG_DESCRIPTION = """
An atomised index of TouchDesigner, a live bridge into a running instance,
and an offline reader for `.toe`/`.tox` files on disk — all three exposed to
agents over MCP.

Two things this bundle does not carry, by design:

- **The index.** It is built on your machine from your TouchDesigner
  installation (`td-atlas build`, then `td-atlas probe` with TouchDesigner
  open) and it contains machine-specific values, so it is a local cache
  rather than something to ship. Until it is built, the index tools say so
  and tell you which command fixes it.
- **The bridge.** Live tools need `td-atlas install` once, plus one line
  pasted into TouchDesigner's textport. The offline project-reader tools
  work without it.

Developed and measured on macOS. On Windows the code paths run in CI but no
TouchDesigner on Windows has ever been driven by them, so that half is
unverified. Linux is not supported, because TouchDesigner is not released for
it.
"""


# -- the registry submission ------------------------------------------------

def build_server_json(version: str, description: str, repo_url: str,
                      bundle_name: str, sha256: str) -> dict:
    """The official-registry submission for the bundle just built.

    Not committed, and deliberately so: `fileSha256` describes one built
    file, and a hash checked into the tree is a promise about a file nobody
    can point at. Clients verify this hash before installing, so it has to be
    the hash of the artefact actually attached to the release.
    """
    download = f"{repo_url.rstrip('/')}/releases/download/v{version}/{bundle_name}"
    return {
        "$schema": SERVER_SCHEMA,
        "name": REGISTRY_NAME,
        "title": "TouchDesigner Atlas",
        "description": description,
        "version": version,
        "repository": {"url": repo_url, "source": "github"},
        "websiteUrl": repo_url,
        "packages": [
            {
                # The registry accepts MCPB artefacts from GitHub and GitLab
                # releases only, and requires the URL to contain "mcp" — the
                # `.mcpb` extension satisfies that.
                "registryType": "mcpb",
                "identifier": download,
                "version": version,
                "fileSha256": sha256,
                "transport": {"type": "stdio"},
            }
        ],
    }


# -- staging and packing ----------------------------------------------------

def _head_commit() -> str:
    out = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


def _commit_timestamp() -> int:
    out = subprocess.run(
        ["git", "log", "-1", "--format=%ct", "HEAD"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    )
    return int(out.stdout.strip())


def _warn_if_head_is_not_what_you_edited() -> None:
    """`git archive HEAD` packs the commit, not the working tree.

    That is what makes the bundle reproducible, and it is also the trap: an
    uncommitted fix is silently absent from the bundle built right after it.
    Say so rather than let the hash look authoritative.
    """
    out = subprocess.run(
        ["git", "status", "--porcelain", "--", *TRACKED_PATHS],
        cwd=ROOT, capture_output=True, text=True, check=True,
    )
    dirty = [line for line in out.stdout.splitlines() if line.strip()]
    if dirty:
        _say("warning: the bundle is packed from HEAD, and these differ from it:")
        for line in dirty:
            _say("  " + line)
        _say("  commit first, or the bundle is not what you just edited.")


# -- the readme, addressed from inside the bundle ---------------------------

# Where a link has to point once the file it names is not next to the reader.
# `blob/` renders a file as a page; an image needs the bytes, and `?raw=true`
# is the redirect GitHub gives for that — a bare `blob/…png` inside `<img>`
# serves HTML and shows a broken image.
BLOB = "/blob/main/"
RAW_QUERY = "?raw=true"

# Where a relative target can appear. Group 1 is the target in every one.
README_TARGETS = (
    ("image", re.compile(r'!\[[^\]]*\]\((?P<t>[^)\s]+)')),
    ("link", re.compile(r'(?<!!)\[[^\]]*\]\((?P<t>[^)\s]+)')),
    ("image", re.compile(r'<img[^>]*?\bsrc="(?P<t>[^"]+)"')),
    ("link", re.compile(r'<a[^>]*?\bhref="(?P<t>[^"]+)"')),
)


def readme_for_bundle(text: str) -> str:
    """Point the readme's relative links at GitHub, for a reader inside the zip.

    The bundle is 36 files: `src/td_atlas/`, and beside it `pyproject.toml`,
    `LICENSE`, `README.md`, the generated `manifest.json` and `server.py`.
    The readme carries 51 links and images, 39 of which name something none
    of that is — every `docs/*.md`, the plugin skill, the sibling
    translations, and the cover image in the header. Inside the zip each of
    those is a dead end, and a host that renders the readme at install time
    renders it with a broken image on the first line (measured 2026-09-12:
    39 broken, 25 distinct targets; after this rewrite, 0).

    Rewriting here rather than shipping a second, bundle-only readme: the
    readme is one text with one owner, and a separate one drifts from it
    within a release. What stays relative is what the bundle actually has —
    `LICENSE` — and anchors, which resolve inside the page either way.
    """
    repo = project_metadata()["urls"]["Repository"].rstrip("/")
    here = {p.relative_to(STAGE).as_posix() for p in STAGE.rglob("*") if p.is_file()}

    def absolute(target: str, kind: str) -> str:
        if target.startswith(("http://", "https://", "mailto:", "#")):
            return target
        path, _, anchor = target.partition("#")
        if not path or path in here:
            return target
        if kind == "image":
            return repo + BLOB + path + RAW_QUERY
        return repo + BLOB + path + (("#" + anchor) if anchor else "")

    for kind, pattern in README_TARGETS:
        def swap(m: re.Match, kind: str = kind) -> str:
            whole = m.group(0)
            head = m.start("t") - m.start()
            tail = m.end("t") - m.start()
            return whole[:head] + absolute(m.group("t"), kind) + whole[tail:]

        text = pattern.sub(swap, text)
    return text


def stage(manifest: dict) -> None:
    """Lay out exactly what will be packed, from tracked files only.

    `git archive` is the whole hygiene argument: it can only emit files git
    tracks, so untracked local state — the index, the token, a venv, caches —
    is absent by construction rather than by exclusion.
    """
    if STAGE.exists():
        shutil.rmtree(STAGE)
    STAGE.mkdir(parents=True)

    archive = subprocess.run(
        ["git", "archive", "--format=tar", "HEAD", "--", *TRACKED_PATHS],
        cwd=ROOT, capture_output=True, check=True,
    )
    subprocess.run(["tar", "-x", "-C", str(STAGE)], input=archive.stdout, check=True)

    readme = STAGE / "README.md"
    readme.write_text(
        readme_for_bundle(readme.read_text(encoding="utf-8")),
        encoding="utf-8",
    )

    (STAGE / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (STAGE / "server.py").write_text(LAUNCHER, encoding="utf-8")
    shutil.copyfile(MCPBIGNORE, STAGE / ".mcpbignore")

    # Zip entries carry mtimes, so the packed bytes depend on them. git
    # archive already stamps every extracted file with the commit time; the
    # two files written above would otherwise carry "now" and make two builds
    # of the same commit differ.
    stamp = _commit_timestamp()
    for path in sorted(STAGE.rglob("*")):
        os.utime(path, (stamp, stamp))
    os.utime(STAGE, (stamp, stamp))


def pack(version: str) -> Path:
    output = DIST / f"td-atlas-{version}.mcpb"
    scratch = DIST / f"td-atlas-{version}.packed.mcpb"
    for path in (output, scratch):
        if path.exists():
            path.unlink()
    _run(["npx", "-y", "@anthropic-ai/mcpb@2", "validate", str(STAGE / "manifest.json")])
    _run(["npx", "-y", "@anthropic-ai/mcpb@2", "pack", str(STAGE), str(scratch)])
    _normalise(scratch, output)
    scratch.unlink()
    return output


def _normalise(packed: Path, output: Path) -> None:
    """Rewrite the archive so two builds of one commit are byte-identical.

    Measured, not assumed: `mcpb pack` stamps every zip entry with the time
    of the build, so packing the same staging tree twice produces two
    different files and the SHA-256 in the registry submission would describe
    neither. This rewrites the same members — the set `mcpb pack` chose,
    `.mcpbignore` already applied — in sorted order with the commit's
    timestamp on each.

    A `.mcpb` is a zip with `manifest.json` at its root, so rewriting it as a
    zip keeps it a valid bundle; `mcpb info` on the result is the check.
    """
    stamp = _commit_timestamp()
    # Zip stores DOS timestamps in local time with two-second granularity.
    from datetime import datetime

    when = datetime.fromtimestamp(stamp)
    date_time = (when.year, when.month, when.day,
                 when.hour, when.minute, when.second // 2 * 2)

    with zipfile.ZipFile(packed) as source:
        members = sorted(source.namelist())
        payloads = {name: source.read(name) for name in members}

    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as target:
        for name in members:
            info = zipfile.ZipInfo(name, date_time=date_time)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            info.create_system = 3
            target.writestr(info, payloads[name])


def _assert_bundle_is_clean(bundle: Path) -> list[str]:
    with zipfile.ZipFile(bundle) as archive:
        names = archive.namelist()

    offenders = []
    for name in names:
        if any(bad in name for bad in FORBIDDEN_SUBSTRINGS):
            offenders.append(f"{name} (forbidden)")
        elif not (name in ALLOWED_IN_BUNDLE or name.startswith(ALLOWED_PREFIX)):
            offenders.append(f"{name} (not on the allowlist)")
    if offenders:
        raise SystemExit(
            "the bundle contains files it must not:\n  " + "\n  ".join(offenders)
        )
    return names


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    meta = project_metadata()
    version = meta["version"]

    manifest = build_manifest()
    MANIFEST.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    _warn_if_head_is_not_what_you_edited()
    DIST.mkdir(exist_ok=True)
    stage(manifest)
    bundle = pack(version)

    names = _assert_bundle_is_clean(bundle)
    digest = _sha256(bundle)

    submission = build_server_json(
        version=version,
        description=meta["description"],
        repo_url=meta["urls"]["Repository"],
        bundle_name=bundle.name,
        sha256=digest,
    )
    (DIST / "server.json").write_text(
        json.dumps(submission, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    # Which commit this bundle came out of. `_warn_if_head_is_not_what_you_edited`
    # only prints; a warning printed here is invisible by the time the owner
    # runs `publish.sh` days later, and that script would then upload a bundle
    # nobody can map back to a commit. Written last, so a build that died
    # part-way leaves no record claiming success.
    (DIST / "build.json").write_text(
        json.dumps(
            {"commit": _head_commit(), "bundle": bundle.name, "version": version},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    _say("")
    _say(f"bundle     {bundle}")
    _say(f"           {len(names)} files, {bundle.stat().st_size:,} bytes")
    _say(f"sha256     {digest}")
    _say(f"manifest   {MANIFEST.relative_to(ROOT)} (regenerated)")
    _say(f"submission {(DIST / 'server.json').relative_to(ROOT)}")
    _say(f"record     {(DIST / 'build.json').relative_to(ROOT)}")
    _say("")
    _say("Nothing was published. To release, see scripts/publish.sh.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
