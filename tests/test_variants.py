"""Branching over the text: save, list, restore, compare.

Two layers, checked separately because they fail differently.

The **store mechanics** — where a variant lands, what its manifest holds, what
it refuses — run on synthetic files with the reader stubbed out, so they need
no TouchDesigner installation and no running instance. That matters more here
than elsewhere: `save` reaches `load_file`, which shells out to `toeexpand`,
and letting that into every test would make the refusal paths untestable on a
machine without the application.

The **circle** — save a real component, restore it, dump both to text and
compare, then diff two variants that genuinely differ — runs on shipped
palette files and skips without an installation, like the other integration
tests in this repository.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from td_atlas.project import variants as store
from td_atlas.project.variants import VariantError


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Point ~/.td-atlas at a scratch directory, so nothing of the user's is read."""
    root = tmp_path / "home"
    root.mkdir()
    monkeypatch.setenv("TD_ATLAS_HOME", str(root))
    return root


class _FakeProject:
    """Enough of a Project for `save`: something to walk and something to print."""

    def __init__(self, marker: str, count: int = 3):
        self.marker = marker
        self.count = count

    def walk(self):
        return iter(range(self.count))


@pytest.fixture
def stub_reader(monkeypatch):
    """Read a .toe without TouchDesigner: the file's own bytes stand in for it.

    The point of the stub is that the text tracks the file, so a test that
    changes the file and re-saves gets a different text — the same relation the
    real reader has, minus the installation.
    """

    def load_file(path, resolver=None, refresh=False):
        return _FakeProject(Path(path).read_text(encoding="utf-8"))

    def to_text(project, path=None):
        return json.dumps(
            {"operators": [project.marker], "path": path or "/"}, indent=1
        ) + "\n"

    monkeypatch.setattr(store, "load_file", load_file)
    monkeypatch.setattr(store, "to_text", to_text)


@pytest.fixture
def project(tmp_path):
    file = tmp_path / "work" / "scene.toe"
    file.parent.mkdir()
    file.write_text("first state", encoding="utf-8")
    return file


# -- where a variant lives --------------------------------------------------


def test_a_variant_lands_under_the_state_directory_not_beside_the_project(
    home, project, stub_reader
):
    """The user's directory gains nothing. That is the boundary, not a preference."""
    before = sorted(p.name for p in project.parent.iterdir())
    variant = store.save(project, "one")

    assert home in variant.directory.parents
    assert sorted(p.name for p in project.parent.iterdir()) == before
    assert variant.text_path.exists() and variant.copy_path.exists()
    assert variant.copy_path.read_text() == "first state"


def test_the_store_key_survives_an_edit_to_the_project(home, project, stub_reader):
    """Variants are grouped by path alone.

    The expansion cache keys on size and mtime as well, which is right for a
    cache and wrong here: saving a state, editing the project and then being
    unable to find the state is exactly the failure the store exists to
    prevent.
    """
    key = store.store_key(project)
    store.save(project, "one")
    project.write_text("second state", encoding="utf-8")

    assert store.store_key(project) == key
    assert [v.label for v in store.variants(project)] == ["one"]


def test_two_projects_with_the_same_name_do_not_share_a_store(
    home, tmp_path, stub_reader
):
    one = tmp_path / "a" / "scene.toe"
    two = tmp_path / "b" / "scene.toe"
    for file in (one, two):
        file.parent.mkdir()
        file.write_text(file.parent.name, encoding="utf-8")

    store.save(one, "x")
    store.save(two, "y")

    assert [v.label for v in store.variants(one)] == ["x"]
    assert [v.label for v in store.variants(two)] == ["y"]


# -- what a save refuses ----------------------------------------------------


def test_a_label_already_in_use_is_refused_rather_than_overwritten(
    home, project, stub_reader
):
    store.save(project, "one")
    project.write_text("second state", encoding="utf-8")

    with pytest.raises(VariantError) as caught:
        store.save(project, "one")
    assert caught.value.key == "variant_label_taken"
    # And the first one is intact: nothing was half-written over it.
    assert store.find(project, "one").copy_path.read_text() == "first state"


@pytest.mark.parametrize("label", ["", "with space", "a/b", "..", "over/../out"])
def test_a_label_that_is_not_a_usable_directory_name_is_refused(
    home, project, stub_reader, label
):
    with pytest.raises(VariantError) as caught:
        store.save(project, label)
    assert caught.value.key == "bad_label"
    assert not (store.store_dir()).exists() or not list(store.store_dir().rglob("*.json"))


def test_an_unreadable_project_leaves_nothing_behind(home, tmp_path, stub_reader):
    with pytest.raises(VariantError):
        store.save(tmp_path / "absent.toe", "one")
    assert not store.store_dir().exists()


def test_a_reader_failure_leaves_no_half_made_variant(home, project, monkeypatch):
    """The text is printed before anything is written, on purpose."""

    def explode(*_args, **_kwargs):
        raise RuntimeError("toeexpand said no")

    monkeypatch.setattr(store, "load_file", explode)
    with pytest.raises(RuntimeError):
        store.save(project, "one")
    assert store.variants(project) == []
    # Not merely "no readable variant": no directory at all. An empty one is
    # litter that a later listing has to be taught to skip, and the reason the
    # reader runs before the first mkdir rather than after it.
    assert not store.source_store(project).exists()


# -- listing ----------------------------------------------------------------


def test_the_list_shows_what_was_saved_newest_first(home, project, stub_reader):
    first = store.save(project, "alpha")
    project.write_text("second state", encoding="utf-8")
    second = store.save(project, "beta", note="after the change")
    # `save` stamps wall-clock time; two saves inside one millisecond would
    # otherwise order by label and make the assertion meaningless.
    second.data["saved_at"] = first.saved_at + 10
    (second.directory / store.MANIFEST).write_text(json.dumps(second.data))

    listed = store.variants(project)
    assert [v.label for v in listed] == ["beta", "alpha"]

    rendered = store.render_list(listed)
    assert "alpha" in rendered and "beta" in rendered
    assert "after the change" in rendered
    assert "3 operators" in rendered


def test_a_directory_without_a_manifest_is_not_a_variant(home, project, stub_reader):
    """An interrupted save leaves a directory, not a variant that lies."""
    store.save(project, "one")
    (store.source_store(project) / "half").mkdir()

    assert [v.label for v in store.variants(project)] == ["one"]


def test_listing_every_project_groups_by_source(home, tmp_path, stub_reader):
    one = tmp_path / "a" / "one.toe"
    two = tmp_path / "b" / "two.toe"
    for file in (one, two):
        file.parent.mkdir()
        file.write_text(file.stem, encoding="utf-8")
    store.save(one, "x")
    store.save(two, "y")

    listed = {origin.name: [v.label for v in items] for origin, items in store.sources()}
    assert listed == {"one.toe": ["x"], "two.toe": ["y"]}


# -- drift ------------------------------------------------------------------


def test_drift_reports_the_original_as_unchanged_changed_or_missing(
    home, project, stub_reader
):
    variant = store.save(project, "one")
    assert variant.drift() == "unchanged"

    project.write_text("second state", encoding="utf-8")
    assert variant.drift() == "changed"

    project.unlink()
    assert variant.drift() == "missing"


def test_a_same_length_edit_still_reads_as_changed(home, project, stub_reader):
    """Hashed, not sized: an edit that keeps the byte count is still an edit."""
    variant = store.save(project, "one")
    project.write_text("FIRST STATE", encoding="utf-8")
    assert len(project.read_text()) == 11
    assert variant.drift() == "changed"


def test_a_changed_original_does_not_stop_a_restore(home, project, stub_reader):
    """Drift is information. The restore reads the variant's own copy."""
    store.save(project, "one")
    project.write_text("second state", encoding="utf-8")

    target, variant = store.restore(project, "one", project.parent.parent / "back.toe")
    assert target.read_text() == "first state"
    assert variant.drift() == "changed"


def test_a_deleted_original_does_not_stop_a_restore(home, project, stub_reader):
    store.save(project, "one")
    project.unlink()

    target, _ = store.restore(project, "one", project.parent.parent / "back.toe")
    assert target.read_text() == "first state"


# -- what a restore refuses -------------------------------------------------


def test_restoring_over_an_existing_file_is_refused(home, project, stub_reader, tmp_path):
    store.save(project, "one")
    taken = tmp_path / "taken.toe"
    taken.write_text("someone's work", encoding="utf-8")

    with pytest.raises(VariantError) as caught:
        store.restore(project, "one", taken)
    assert caught.value.key == "variant_output_exists"
    assert taken.read_text() == "someone's work"


def test_an_unknown_label_names_the_ones_that_exist(home, project, stub_reader):
    store.save(project, "alpha")
    with pytest.raises(VariantError) as caught:
        store.restore(project, "beta", project.parent / "out.toe")
    assert caught.value.key == "variant_unknown"
    assert "alpha" in str(caught.value)


def test_a_corrupt_stored_copy_is_caught_before_anything_is_written(
    home, project, stub_reader, tmp_path
):
    """The refusal that survived the storage choice.

    Restoring cannot be broken by a changed original, so the hash that matters
    is the stored copy's own: if that no longer matches its manifest, the
    variant hands back something other than what was saved.
    """
    variant = store.save(project, "one")
    variant.copy_path.write_text("tampered", encoding="utf-8")

    out = tmp_path / "out.toe"
    with pytest.raises(VariantError) as caught:
        store.restore(project, "one", out)
    assert caught.value.key == "variant_corrupt"
    assert not out.exists()


def test_a_lost_stored_copy_says_the_text_is_still_there(home, project, stub_reader):
    variant = store.save(project, "one")
    variant.copy_path.unlink()

    with pytest.raises(VariantError) as caught:
        store.restore(project, "one", project.parent / "out.toe")
    assert caught.value.key == "variant_corrupt"
    assert str(variant.text_path) in str(caught.value)


def test_restoring_into_a_directory_keeps_the_saved_file_name(
    home, project, stub_reader, tmp_path
):
    """Which is what keeps a dump of the restored file equal to the stored text.

    The dump records the file's own name, so a restore that renamed the file
    would make the two texts differ for a reason that is not the network.
    """
    store.save(project, "one")
    into = tmp_path / "out"
    into.mkdir()

    target, _ = store.restore(project, "one", into)
    assert target == into / "scene.toe"


# -- the refusals as an agent sees them -------------------------------------


def test_every_variant_refusal_carries_its_own_hint(home, project, stub_reader, tmp_path):
    """A reworded message must not drop an agent back onto the unmapped hint."""
    from td_atlas.mcp.hints import HINTS, classify

    store.save(project, "one")
    taken = tmp_path / "taken.toe"
    taken.write_text("x", encoding="utf-8")

    seen = []
    for call in (
        lambda: store.save(project, "one"),
        lambda: store.save(project, "not a label"),
        lambda: store.find(project, "absent"),
        lambda: store.restore(project, "one", taken),
    ):
        with pytest.raises(VariantError) as caught:
            call()
        recovery = classify(caught.value)
        assert recovery is HINTS[caught.value.key]
        assert recovery is not HINTS["unmapped_error"]
        seen.append(caught.value.key)

    assert set(seen) == {
        "variant_label_taken", "bad_label", "variant_unknown", "variant_output_exists"
    }


# -- the CLI ----------------------------------------------------------------


def test_the_cli_saves_lists_restores_and_diffs(
    home, project, stub_reader, tmp_path, capsys
):
    from td_atlas.cli import main

    assert main(["project", "variant", "save", str(project), "--label", "one"]) == 0
    project.write_text("second state", encoding="utf-8")
    assert main(["project", "variant", "save", str(project), "--label", "two"]) == 0

    capsys.readouterr()
    assert main(["project", "variant", "list", str(project)]) == 0
    listed = capsys.readouterr().out
    assert "one" in listed and "two" in listed

    out = tmp_path / "restored.toe"
    assert main(
        ["project", "variant", "restore", str(project), "--label", "one",
         "-o", str(out)]
    ) == 0
    assert out.read_text() == "first state"

    # And the refusal reaches the shell as a non-zero status, not a traceback.
    assert main(
        ["project", "variant", "restore", str(project), "--label", "one",
         "-o", str(out)]
    ) == 1


# -- the circle, on real components -----------------------------------------


def _installed():
    from td_atlas.install import InstallNotFound, discover

    try:
        return discover()
    except InstallNotFound:
        return None


needs_td = pytest.mark.skipif(
    _installed() is None, reason="no TouchDesigner installation"
)


def _palette(name: str) -> Path | None:
    install = _installed()
    if install is None:
        return None
    root = install.root / "Contents" / "Resources" / "tfs" / "Samples" / "Palette"
    if not root.is_dir():
        root = install.root / "Samples" / "Palette"
    found = sorted(root.rglob(name))
    return found[0] if found else None


@needs_td
@pytest.mark.parametrize("name", ["checker.tox", "battery.tox"])
def test_save_then_restore_then_dump_gives_back_the_saved_text(home, tmp_path, name):
    """The circle: save a real component, restore it, dump it, compare.

    Byte identity is the right gate here even though `rebuild.py` cannot claim
    it, because a restore is a copy: if the bytes differ at all, something
    other than the file has been handed back.
    """
    from td_atlas.project.model import load_file
    from td_atlas.project.serialize import to_text

    source = _palette(name)
    if source is None:
        pytest.skip("palette not present")

    variant = store.save(source, "kept")
    into = tmp_path / "out"
    into.mkdir()
    target, _ = store.restore(source, "kept", into)

    assert target.read_bytes() == source.read_bytes()
    assert to_text(load_file(target, refresh=True)) == variant.text()
    assert variant.data["operator_count"] == sum(1 for _ in load_file(source).walk())


@needs_td
def test_the_diff_of_two_variants_finds_the_edit_and_nothing_else(home, tmp_path):
    """A real edit, made through the writer, seen through the existing diff.

    The edit is deliberately a single parameter on a single operator, so
    "nothing else" is checkable: any extra changed node means the store or the
    diff is reporting noise.
    """
    from td_atlas.project import rebuild
    from td_atlas.project.model import load_file
    from td_atlas.project.serialize import dumps, to_text

    source = _palette("checker.tox")
    if source is None:
        pytest.skip("palette not present")

    data = json.loads(to_text(load_file(source)))

    target_path = None
    target_parm = None

    def walk(ops, parent="/"):
        nonlocal target_path, target_parm
        for op in ops:
            path = f"{parent.rstrip('/')}/{op['name']}"
            for parm, value in sorted((op.get("parms") or {}).items()):
                if target_parm is None and isinstance(value, str):
                    op["parms"][parm] = value + " edited"
                    target_path, target_parm = path, parm
            walk(op.get("children") or [], path)

    walk(data["operators"])
    assert target_parm, "the fixture no longer has a plain constant to edit"

    edited = tmp_path / source.name
    changes = rebuild.rebuild(source, dumps(data), edited)
    assert changes.gaps == []

    store.save(source, "before")
    # The edited file is a different path, so it gets its own store; the
    # variant pair has to come from one project, and the edited bytes are what
    # the second variant must hold.
    second = store.save(source, "after")
    second.copy_path.write_bytes(edited.read_bytes())
    second.data["copy_sha256"] = store._sha256(second.copy_path)
    (second.directory / store.MANIFEST).write_text(json.dumps(second.data))

    result, one, two = store.compare(source, "before", "after")
    assert (one.label, two.label) == ("before", "after")
    assert result.added == [] and result.removed == []
    assert [c.path for c in result.changed] == [target_path]
    assert [p.name for p in result.changed[0].params] == [target_parm]

    # And a variant compared with itself finds nothing at all.
    same, _, _ = store.compare(source, "before", "before")
    assert same.empty
