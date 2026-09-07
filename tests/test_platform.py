"""What the code does on a platform this machine is not.

Nothing here proves td-atlas works on Windows — no TouchDesigner on Windows
has ever run it, and a CI runner has none. What it pins down is the part that
is decidable from a macOS machine: path handling that is written in string
form and would produce a *different* answer under Windows separators, and the
"does this look like an install" check, which is pure filesystem logic and can
be exercised on any OS by building a fake tree and telling `install.py` which
system it is on.

Since 2026-09-07 these same tests also run *on* Windows in CI, which is a
second thing and not the same one: the run reads the code's behaviour there,
never the layout it describes. That is where the empty-TD_ATLAS_HOME case
below first failed — the three sides agreed about the directory and disagreed
about the string that names it.
"""

from __future__ import annotations

import os
from pathlib import Path, PureWindowsPath

import pytest

from td_atlas import install as install_mod
from td_atlas.atoms.extract_static import _page_id
from td_atlas.component.handler import _home as handler_home
from td_atlas.config import home as cfg_home
from td_atlas.install import InstallNotFound, _build, discover
from td_atlas.project.expand import _toc_entry
from td_atlas.project.model import _node_path


@pytest.fixture
def on_windows(monkeypatch):
    """Take the non-Darwin branch of `_build`, on a real local filesystem."""
    monkeypatch.setattr(install_mod.platform, "system", lambda: "Windows")


# -- "looks like an install" is an actual check -----------------------------
#
# The regression: `_build` computed the resource-tree probe and then threw the
# result away (`tfs = root if tfs else root`), so every directory whose name
# merely started with "TouchDesigner" was accepted.

def test_a_directory_without_a_resource_tree_is_not_an_install(
    on_windows, tmp_path
):
    bare = tmp_path / "TouchDesigner.2025.32460"
    (bare / "bin").mkdir(parents=True)
    (bare / "bin" / "TouchDesigner.exe").write_text("")

    assert _build(bare) is None


def test_an_explicit_path_without_a_resource_tree_is_refused(
    on_windows, tmp_path
):
    bare = tmp_path / "TouchDesigner.2025.32460"
    bare.mkdir()

    with pytest.raises(InstallNotFound) as exc:
        discover(bare)
    assert "resource tree" in str(exc.value)


def test_a_directory_with_the_resource_tree_is_an_install(on_windows, tmp_path):
    root = tmp_path / "TouchDesigner.2025.32460"
    (root / "Config").mkdir(parents=True)
    (root / "Samples").mkdir()
    (root / "bin").mkdir()
    exe = root / "bin" / "TouchDesigner.exe"
    exe.write_text("")

    found = _build(root)
    assert found is not None
    assert found.tfs == root
    assert found.version == "2025.32460"
    assert found.executable == exe


def test_config_alone_is_enough(on_windows, tmp_path):
    """Samples/ is optional content; Config/ alone still names an install."""
    root = tmp_path / "TouchDesigner.2023.11880"
    (root / "Config").mkdir(parents=True)

    found = _build(root)
    assert found is not None and found.executable is None


# -- where installs are looked for -----------------------------------------

def test_windows_search_walks_the_derivative_folder(monkeypatch, tmp_path):
    program_files = tmp_path / "Program Files"
    derivative = program_files / "Derivative"
    (derivative / "TouchDesigner.2025.32460").mkdir(parents=True)
    (derivative / "notes.txt").write_text("")
    monkeypatch.setenv("ProgramFiles", str(program_files))
    monkeypatch.delenv("ProgramW6432", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    assert install_mod._candidates_windows() == [
        derivative / "TouchDesigner.2025.32460"
    ]


def test_an_unknown_system_searches_nowhere(monkeypatch, tmp_path):
    """TouchDesigner ships for macOS and Windows only; Linux is not searched."""
    monkeypatch.setattr(install_mod.platform, "system", lambda: "Linux")
    monkeypatch.delenv("TD_ATLAS_INSTALL", raising=False)
    assert not hasattr(install_mod, "_candidates_linux")

    with pytest.raises(InstallNotFound) as exc:
        discover()
    assert "--install-path" in str(exc.value)


# -- paths that must stay '/'-separated whatever the filesystem uses --------

def test_operator_paths_never_carry_a_backslash():
    """'/project1/noise1' is TouchDesigner notation, not the filesystem's."""
    assert _node_path(PureWindowsPath("project1/noise1")) == "/project1/noise1"
    assert _node_path(PureWindowsPath("moviefilein1")) == "/moviefilein1"


def test_toc_entries_stay_in_the_separator_toeexpand_writes():
    assert _toc_entry(PureWindowsPath("beatCHOP/example1.n")) == (
        "beatCHOP/example1.n"
    )


def test_mirrored_wiki_page_ids_keep_the_title_slash():
    """'TCP/IP DAT' was mirrored as TCP/IP_DAT.htm — a subdirectory."""
    assert _page_id(PureWindowsPath("TCP/IP_DAT.htm")) == "TCP/IP_DAT"
    assert _page_id(PureWindowsPath("Noise_TOP.htm")) == "Noise_TOP"


def test_write_toc_reuses_the_template_ordering(tmp_path):
    """The ordering pass matches template lines against files on disk.

    This runs on POSIX, so it does not by itself prove anything about
    Windows; it pins the invariant the Windows failure would break. With
    str() instead of as_posix() the two sides would be written in different
    separators there, every template line would miss, and this ordering
    would be silently replaced by a directory walk.
    """
    from td_atlas.project.expand import write_toc

    expanded = tmp_path / "beatCHOP.tox.dir"
    (expanded / "beatCHOP").mkdir(parents=True)
    (expanded / ".build").write_text("")
    (expanded / "beatCHOP.n").write_text("")
    (expanded / "beatCHOP" / "example1.n").write_text("")

    template = tmp_path / "template.toc"
    template.write_text(
        "# 4 0 0 0 1\nbeatCHOP/example1.n\nbeatCHOP.n\n.build\n"
    )
    out = tmp_path / "beatCHOP.tox.toc"
    write_toc(expanded, out, template)

    assert out.read_text().splitlines() == [
        "# 4 0 0 0 1",
        "beatCHOP/example1.n",
        "beatCHOP.n",
        ".build",
    ]


# -- one rule for where ~/.td-atlas is ---------------------------------------
#
# Three places answer that question: the host (config.py), the bridge inside
# TouchDesigner (component/handler.py) and the installer that builds it
# (component/bootstrap.py). They must not disagree — a bootstrap reading a
# different directory than the host wrote installs a stale handler.py and
# reports success.

def _component_function(module_name, name):
    """Lift one function out of a component module without importing it.

    bootstrap.py runs install() at module scope, so it cannot be imported on
    the host; the function is compiled on its own instead.
    """
    import ast

    from td_atlas.component import handler as handler_mod

    source = (
        Path(handler_mod.__file__).parent / f"{module_name}.py"
    ).read_text()
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            namespace = {"os": os}
            exec(
                compile(ast.Module([node], []), f"{module_name}.py", "exec"),
                namespace,
            )
            return namespace[name]
    raise AssertionError(f"{module_name}.py has no {name}()")


def test_an_empty_td_atlas_home_counts_as_unset_everywhere(monkeypatch):
    """All three sides, and in the *same* spelling of the same directory.

    The expectation is joined, not expanded from "~/.td-atlas" in one piece,
    because that is what the code does and the difference is the whole point
    of this test on Windows: `expanduser` substitutes only the leading "~", so
    the one-piece form leaves a "/" in the middle of a path the host writes
    with backslashes. That is exactly how this failed in CI (run 34111353871)
    — three sides naming one directory in two separators.
    """
    monkeypatch.setenv("TD_ATLAS_HOME", "")
    default = os.path.join(os.path.expanduser("~"), ".td-atlas")

    assert str(cfg_home()) == default
    assert handler_home() == default
    assert _component_function("bootstrap", "_home")() == default


def test_all_three_sides_follow_td_atlas_home(monkeypatch, tmp_path):
    monkeypatch.setenv("TD_ATLAS_HOME", str(tmp_path / "staged"))

    bootstrap_home = _component_function("bootstrap", "_home")()
    assert str(cfg_home()) == handler_home() == bootstrap_home
    assert bootstrap_home == str(tmp_path / "staged")
