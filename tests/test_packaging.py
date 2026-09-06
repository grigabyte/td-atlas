"""The bundle manifest and the plugin manifests, checked without building.

Three kinds of check, and the drift check is the point of the file.

`packaging/manifest.json` is committed so a reviewer can read it, but it is
generated: every field it shares with the package — version, description,
author, repository, keywords, licence, the Python floor, the platforms — is
read out of `pyproject.toml`. A committed generated file is worth nothing
unless something notices when it stops matching its source, so the drift test
regenerates it here and compares.
That is what stops a version bump in `pyproject.toml` from shipping a bundle
that still claims the old one.

The other two are rules the project already holds elsewhere, applied to the
files a packaging change is most likely to break them in: no install route
through a package that does not exist (`td-atlas` is not on PyPI), and no
platform claimed that nobody has run.

Nothing here shells out — no `npx`, no `git`, no network. The pack itself is
gated at build time by `scripts/build_mcpb.py`.
"""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import build_mcpb  # noqa: E402


@pytest.fixture(scope="module")
def committed() -> dict:
    return json.loads((ROOT / "packaging" / "manifest.json").read_text())


@pytest.fixture(scope="module")
def pyproject() -> dict:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)["project"]


# -- one source ------------------------------------------------------------

def test_the_committed_manifest_is_what_the_builder_would_write(committed):
    """The whole reason the manifest is generated rather than maintained."""
    assert build_mcpb.build_manifest() == committed, (
        "packaging/manifest.json is stale — run scripts/build_mcpb.py"
    )


@pytest.mark.parametrize(
    "field", ["version", "description"]
)
def test_the_manifest_repeats_no_metadata_it_could_get_wrong(field, committed, pyproject):
    assert committed[field] == pyproject[field]


def test_the_python_floor_comes_from_the_package(committed, pyproject):
    assert committed["compatibility"]["runtimes"]["python"] == pyproject["requires-python"]


def test_the_manifest_names_no_tool_it_would_have_to_keep_in_step(committed):
    """A static tool list was written first and then removed on purpose.

    It goes stale the moment a tool is added or a docstring's first line is
    reworded, and an install dialog naming a tool the bundle does not ship is
    read before anything else runs. `tools_generated` sends the host to the
    server, which cannot disagree with itself.
    """
    assert "tools" not in committed
    assert committed["tools_generated"] is True


# -- claims the project has already decided it will not make ----------------

def _packaging_text() -> str:
    parts = [
        (ROOT / "packaging" / "manifest.json").read_text(),
        (ROOT / "plugin" / ".claude-plugin" / "plugin.json").read_text(),
        (ROOT / ".claude-plugin" / "marketplace.json").read_text(),
        (ROOT / "scripts" / "build_mcpb.py").read_text(),
        (ROOT / "scripts" / "publish.sh").read_text(),
    ]
    return "\n".join(parts)


def test_nothing_in_the_packaging_offers_a_route_through_pypi():
    """`td-atlas` is not on PyPI (checked: 404), so `uvx td-atlas` and
    `pip install td-atlas` are instructions that fail for everyone who
    follows them. The same rule `test_recovery_hints` holds for hints."""
    text = _packaging_text()
    assert "uvx td-atlas" not in text
    assert "pip install td-atlas" not in text
    assert "pip install td_atlas" not in text


def test_the_manifest_claims_only_platforms_the_package_claims(committed, pyproject):
    """Windows is unverified and Linux unsupported; the classifiers were
    trimmed to say so. The manifest must not quietly say otherwise."""
    assert committed["compatibility"]["platforms"] == ["darwin"]
    assert not any(
        line.startswith("Operating System :: Microsoft")
        or line.startswith("Operating System :: POSIX")
        for line in pyproject["classifiers"]
    )


# -- the launch string ------------------------------------------------------

def test_the_bundle_launches_the_same_module_the_cli_prints():
    """`cli.mcp_command()` settled how this server is started, and measured
    why. A bundle cannot reuse its interpreter path — there is no venv of the
    user's to point at — but it must not invent a different entry point."""
    from td_atlas.cli import mcp_command

    args = build_mcpb.build_manifest()["server"]["mcp_config"]["args"]
    assert args[-3:] == mcp_command()[1:]
    assert args[0] == "run" and "${__dirname}" in args


def test_the_entry_point_is_a_file_that_can_actually_be_run_as_one():
    """For the uv server type `mcp_config` is optional, so a host is entitled
    to run `entry_point` directly. `src/td_atlas/cli.py` was the obvious
    candidate and is the wrong one: it opens with a relative import and dies
    before argparse. The generated launcher goes through `cli.main` so both
    paths stay on the code path `mcp_command()` measured."""
    manifest = build_mcpb.build_manifest()
    assert manifest["server"]["entry_point"] == "server.py"
    assert "from td_atlas.cli import main" in build_mcpb.LAUNCHER
    assert '"mcp"' in build_mcpb.LAUNCHER


def test_the_bundle_ships_no_resolved_environment():
    """The uv server type forbids it, and a resolved environment is exactly
    where a machine-specific artefact would hide."""
    ignored = (ROOT / "packaging" / ".mcpbignore").read_text()
    for pattern in (".venv/", "server/lib/", "server/venv/", "*.db", "config.json"):
        assert pattern in ignored


# -- the plugin -------------------------------------------------------------

PLUGIN = ROOT / "plugin"


def test_the_skill_sits_where_a_plugin_looks_for_it():
    """`skills/<name>/SKILL.md` is the default location a plugin loads from,
    relative to the *plugin* root; the manifest names no custom path, so this
    layout is the contract."""
    assert (PLUGIN / "skills" / "touchdesigner" / "SKILL.md").is_file()
    plugin = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text())
    assert "skills" not in plugin


def test_the_plugin_root_holds_the_skill_and_nothing_else():
    """The plugin root is a subdirectory, not the repository root.

    While `marketplace.json` said `"source": "./"`, installing the
    `touchdesigner` plugin copied every tracked file in the repository —
    `memory-bank/` with the owner's verbatim notes, `tests/`, `src/`. Set
    equality rather than a few absence checks: a file added under `plugin/`
    by accident has to fail here, and naming only the directories one
    happens to think of would let the next one through.
    """
    present = {
        path.relative_to(PLUGIN).as_posix()
        for path in PLUGIN.rglob("*")
        if path.is_file()
        and path.name != ".DS_Store"
        and "__pycache__" not in path.parts
    }
    assert present == {
        ".claude-plugin/plugin.json",
        "skills/touchdesigner/SKILL.md",
        "skills/touchdesigner/references/gotchas.md",
        "skills/touchdesigner/references/tools.md",
    }
    for kept_out in ("memory-bank", "tests", "src", "scripts", "packaging", "docs"):
        assert not (PLUGIN / kept_out).exists()


def test_the_marketplace_offers_the_plugin_beside_it():
    marketplace = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    plugin = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text())
    entries = marketplace["plugins"]
    assert len(entries) == 1
    assert entries[0]["name"] == plugin["name"]
    # The marketplace lives at the repository root because that is where a
    # `/plugin marketplace add <repo>` looks; the plugin it offers is the
    # subdirectory, so installing it brings the skill and not the repository.
    assert entries[0]["source"] == "./plugin"


def test_the_plugin_version_tracks_the_package(pyproject):
    plugin = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text())
    assert plugin["version"] == pyproject["version"], (
        "the plugin manifest is versioned by hand — bump it with pyproject"
    )


def test_the_server_announces_our_version_not_the_library_s():
    """A bundle published as 0.1.0 whose server says 1.29.1 is a quiet lie.

    FastMCP takes no version, and the low-level server left at None reports
    the `mcp` library's own version to every client — which is what it did
    until this was set.
    """
    from importlib.metadata import version

    from td_atlas.mcp.server import mcp

    options = mcp._mcp_server.create_initialization_options()
    assert options.server_version == version("td-atlas")
    assert options.server_version != version("mcp")
