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

set -euo pipefail

cd "$(dirname "$0")/.."

VERSION="$(.venv/bin/python -c 'import tomllib;print(tomllib.load(open("pyproject.toml","rb"))["project"]["version"])')"
BUNDLE="dist/td-atlas-${VERSION}.mcpb"
SUBMISSION="dist/server.json"
TAG="v${VERSION}"

for required in "$BUNDLE" "$SUBMISSION"; do
  [ -f "$required" ] || {
    echo "missing $required — run: .venv/bin/python scripts/build_mcpb.py" >&2
    exit 1
  }
done

# The hash in the submission has to match the file about to be uploaded, or
# every client that checks it refuses the install.
DECLARED="$(.venv/bin/python -c 'import json;print(json.load(open("dist/server.json"))["packages"][0]["fileSha256"])')"
ACTUAL="$(shasum -a 256 "$BUNDLE" | cut -d' ' -f1)"
[ "$DECLARED" = "$ACTUAL" ] || {
  echo "dist/server.json declares $DECLARED but $BUNDLE hashes to $ACTUAL" >&2
  echo "rebuild both together: .venv/bin/python scripts/build_mcpb.py" >&2
  exit 1
}

for tool in gh mcp-publisher; do
  command -v "$tool" >/dev/null || {
    echo "$tool is not installed" >&2
    [ "$tool" = "mcp-publisher" ] && echo "  brew install mcp-publisher" >&2
    exit 1
  }
done

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
