#!/usr/bin/env bash
#
# Release the bundle and list it in the official MCP Registry.
#
#   ./scripts/publish.sh
#
# This is the only script in the repository that sends anything outward, and
# it is deliberately not run by the build. `scripts/build_mcpb.py` stops at
# "built and checked"; everything past that point is the owner's decision.
#
# Three outward steps, in this order, because each depends on the last:
#
#   1. `gh release create` attaches the .mcpb to a GitHub release. Until this
#      exists, the download URL in server.json points at nothing.
#   2. `mcp-publisher login github` proves the io.github.grigabyte namespace.
#   3. `mcp-publisher publish` submits server.json.
#
# The registry accepts MCPB artefacts from GitHub releases, which is why this
# route works at all: `td-atlas` is not on PyPI, and nothing here asks it to
# be.
#
# The SHA-256 in dist/server.json describes the file built alongside it.
# Clients verify that hash before installing, so the asset uploaded in step 1
# must be that exact file — rebuild and re-run this script together, never one
# without the other.
#
# Everything before the first outward step is a gate. Five of them, cheapest
# first, with the only one that needs the network last. Cheapest first because
# a release cannot be taken back: a published tag and a registry entry are
# permanent, and the one release this repository nearly made would have
# shipped a bundle built five commits back, speaking bridge protocol 3 against
# sources on 5. The gates are what makes "rebuild and re-run together" a rule
# rather than a hope.

set -euo pipefail

cd "$(dirname "$0")/.."

VERSION="$(.venv/bin/python -c 'import tomllib;print(tomllib.load(open("pyproject.toml","rb"))["project"]["version"])')"
BUNDLE="dist/td-atlas-${VERSION}.mcpb"
SUBMISSION="dist/server.json"
RECORD="dist/build.json"
TAG="v${VERSION}"

# 1. A clean working tree. Untracked files count as dirt on purpose: the
#    bundle is packed by `git archive HEAD`, so a file that exists only in
#    the working tree is absent from it, and that is exactly the shape of a
#    forgotten `git add`.
DIRT="$(git status --porcelain)"
[ -z "$DIRT" ] || {
  echo "the working tree is not clean; the bundle is packed from HEAD, so" >&2
  echo "anything below is absent from it:" >&2
  echo "$DIRT" >&2
  echo "commit or stash, rebuild, then publish" >&2
  exit 1
}

for required in "$BUNDLE" "$SUBMISSION" "$RECORD"; do
  [ -f "$required" ] || {
    echo "missing $required — run: .venv/bin/python scripts/build_mcpb.py" >&2
    exit 1
  }
done

# 2. The bundle was packed from the commit that is checked out now. The tree
#    being clean says nothing about *when* the bundle was built; build_mcpb.py
#    records the HEAD it archived beside the bundle for this comparison.
BUILT_FROM="$(.venv/bin/python -c 'import json;print(json.load(open("dist/build.json"))["commit"])')"
HEAD_NOW="$(git rev-parse HEAD)"
[ "$BUILT_FROM" = "$HEAD_NOW" ] || {
  echo "$BUNDLE was built from $BUILT_FROM but HEAD is $HEAD_NOW" >&2
  echo "rebuild: .venv/bin/python scripts/build_mcpb.py" >&2
  exit 1
}

# 3. The tag is free *here*. `gh release create` on an existing tag fails
#    halfway through, after the prompt has already been answered. The remote
#    is asked separately, in gate 5 — this one costs nothing and rules the
#    release out before the tests are run.
if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
  echo "$TAG already exists — bump the version in pyproject.toml, or delete" >&2
  echo "the tag if this release was never published" >&2
  exit 1
fi

# The hash in the submission has to match the file about to be uploaded, or
# every client that checks it refuses the install.
DECLARED="$(.venv/bin/python -c 'import json;print(json.load(open("dist/server.json"))["packages"][0]["fileSha256"])')"
ACTUAL="$(shasum -a 256 "$BUNDLE" | cut -d' ' -f1)"
[ "$DECLARED" = "$ACTUAL" ] || {
  echo "dist/server.json declares $DECLARED but $BUNDLE hashes to $ACTUAL" >&2
  echo "rebuild both together: .venv/bin/python scripts/build_mcpb.py" >&2
  exit 1
}

# 4. The tests pass. Last of the local gates because it is the slowest, and
#    the three above can rule the release out in under a second.
echo "running the tests before anything goes out..." >&2
.venv/bin/python -m pytest -q || {
  echo "tests are not green; nothing published" >&2
  exit 1
}

for tool in gh mcp-publisher; do
  command -v "$tool" >/dev/null || {
    echo "$tool is not installed" >&2
    [ "$tool" = "mcp-publisher" ] && echo "  brew install mcp-publisher" >&2
    exit 1
  }
done

# 5. The tag is free on the remote too. Gate 3 reads the local ref store, and
#    a tag deleted locally but still on origin passes it — which is exactly
#    the state a re-run after a half-finished release leaves behind. This is
#    the only gate that touches the network, so it stands last, after
#    everything that can rule the release out offline.
#
#    `git ls-remote --exit-code` exits 0 when the ref is there and 2 when it
#    is not; anything else (no `origin`, no network, a rejected credential)
#    is 128, and that must fail the gate rather than read as "absent" — the
#    whole point is to not publish over a tag we could not check for.
REMOTE_TAG_STATUS=0
git ls-remote --exit-code --tags origin "refs/tags/$TAG" >/dev/null 2>&1 \
  || REMOTE_TAG_STATUS=$?
case "$REMOTE_TAG_STATUS" in
  0)
    echo "$TAG already exists on origin, even though it is not here — a" >&2
    echo "release under that tag was already made, or half-made. Bump the" >&2
    echo "version in pyproject.toml and rebuild; deleting a published tag" >&2
    echo "breaks every client that resolved it" >&2
    exit 1
    ;;
  2)
    ;;
  *)
    echo "could not ask origin whether $TAG exists (git ls-remote exited" >&2
    echo "$REMOTE_TAG_STATUS). Not publishing: an unchecked tag is the one" >&2
    echo "this gate is here to catch" >&2
    exit 1
    ;;
esac

echo "About to publish $TAG:"
echo "  release  $BUNDLE  ($ACTUAL)"
echo "  registry $(.venv/bin/python -c 'import json;print(json.load(open("dist/server.json"))["name"])')"
read -r -p "Proceed? [y/N] " reply
[ "$reply" = "y" ] || { echo "nothing published"; exit 1; }

gh release create "$TAG" "$BUNDLE" \
  --title "td-atlas $VERSION" \
  --notes "MCP bundle for td-atlas $VERSION. Open the .mcpb to install."

mcp-publisher login github
mcp-publisher publish "$SUBMISSION"
