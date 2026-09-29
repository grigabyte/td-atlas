"""A project whose file name is not ASCII still opens and still writes back.

`toeexpand` and `toecollapse` read their argument's UTF-8 bytes as Latin-1
(measured, macOS, build 2025.32460: "Error opening file: ÐºÑƒÐ±Ñ‹ ...tox"), so
an artist who names files in Cyrillic could not read one of them. The tools
only ever see a copy, and the copy is what gets an ASCII name.

The tools are faked the way `test_expand_cache.py` fakes them, and the fake
refuses a non-ASCII argument the way the real ones do.
"""

from __future__ import annotations

import importlib
import os

import pytest

expand_mod = importlib.import_module("td_atlas.project.expand")


@pytest.fixture
def seen(monkeypatch, tmp_path):
    names: list[str] = []

    def fake_run(argv, cwd=None, **kwargs):
        name = argv[1]
        names.append(name)
        failed = type("Completed", (), {
            "stdout": "", "stderr": "Error opening file", "returncode": 1})()
        if not name.isascii():
            return failed
        if name.endswith(".dir"):
            with open(os.path.join(str(cwd), name[: -len(".dir")]), "wb") as fh:
                fh.write(b"collapsed")
        else:
            target = os.path.join(str(cwd), f"{name}.dir")
            os.makedirs(target, exist_ok=True)
            with open(os.path.join(target, "root.n"), "w") as handle:
                handle.write("n\n")
        return failed

    monkeypatch.setattr(expand_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(expand_mod, "_tool", lambda install, name: tmp_path / name)
    monkeypatch.setattr(
        expand_mod, "discover", lambda: type("I", (), {"root": tmp_path})()
    )
    return names


def test_a_cyrillic_file_name_is_expanded_through_an_ascii_copy(tmp_path, seen):
    source = tmp_path / "кубы — 20 с.tox"
    source.write_bytes(b"tox")

    expansion = expand_mod.expand(source)

    assert seen == ["project.tox"]
    assert (expansion.root / "root.n").exists()
    assert expansion.source == source.resolve()


def test_an_ascii_file_name_is_handed_over_unchanged(tmp_path, seen):
    source = tmp_path / "cubes.tox"
    source.write_bytes(b"tox")

    expand_mod.expand(source)

    assert seen == ["cubes.tox"]


def test_a_cyrillic_tree_is_collapsed_from_an_ascii_copy(tmp_path, seen):
    tree = tmp_path / "кубы.tox.dir"
    tree.mkdir()
    (tree / "root.n").write_text("n\n")
    output = tmp_path / "кубы обратно.tox"

    assert expand_mod.collapse(tree, output) == output

    assert seen == ["project.tox.dir"]
    assert output.read_bytes() == b"collapsed"
    # The staging copy is gone, and the caller's tree was not renamed.
    assert tree.is_dir()
    assert not [p for p in expand_mod.cache_dir().iterdir()
                if p.name.startswith(expand_mod.WORK_PREFIX)]
