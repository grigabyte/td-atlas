"""Tests for the network text the bridge writes beside the .toe on save.

Everything here runs on the host with no TouchDesigner: the parts of item 19
that touch `op`, `project` and `root` are a thin gather layer, and every
decision around it — the switch, the file already in the way, a writer that
raises, the atomic replace — is a plain function taking its side effects as
arguments so that it can be exercised here.

What these tests cannot reach, and what the live run in the report covers
instead: that TouchDesigner calls `onProjectPostSave` at all, and what the
gather layer reads out of a live operator.
"""

from __future__ import annotations

import json
import os
import random

import pytest

from td_atlas.component import handler
from td_atlas.project import release, serialize
from windows_gaps import posix_access_refusal_only


# -- the printer, held in step with the host's ------------------------------
#
# handler.py cannot import td_atlas.project.serialize: it runs inside
# TouchDesigner's embedded interpreter, where the package does not exist. So
# the printer is a copy, and this is what keeps the copy honest.

def _sample_data(seed: int):
    """Data shaped like a serialised project, exercising the printer's rules."""
    rng = random.Random(seed)

    def parm():
        pick = rng.randrange(4)
        if pick == 0:
            return "0.25"
        if pick == 1:
            return {"expr": "absTime.frame*.6", "value": "6531"}
        if pick == 2:
            return {"bind": "op('x').par.y", "value": ""}
        return "a string with \"quotes\", a tab \t and unicode — é"

    def node(depth):
        data = {
            "name": "node%d" % rng.randrange(100),
            "type": "noiseCHOP",
            "family": "CHOP",
            "tile": [50.0, 30.0, 130.0, 90.0],
            "flags": {"parlanguage": "0", "viewer": "1"},
            "color": [0.67, 0.67, 0.67],
            "inputs": [[0, "in1"], [2, "/project1/far"]],
            "parms": {"p%d" % i: parm() for i in range(rng.randrange(4))},
            "custom_parms": {},
            "text": None,
            "table": None,
            "children": [],
        }
        if rng.random() < 0.5:
            data["text"] = ["line one", "", "a very long line " * 12]
        if rng.random() < 0.3:
            data["table"] = [["key", "value"], ["a", "b"], ["c" * 200, ""]]
        if rng.random() < 0.3:
            data["custom_pages"] = ["Settings"]
        if depth:
            data["children"] = [node(depth - 1) for _ in range(rng.randrange(3))]
        return data

    return {
        "source": "scene.toe",
        "build": {"build": "2025.32460", "version": "099"},
        "path": "/",
        "operator_count": 12,
        "operators": [node(3) for _ in range(2)],
    }


@pytest.mark.parametrize("seed", range(12))
def test_the_bridge_printer_and_the_host_printer_agree_byte_for_byte(seed):
    data = _sample_data(seed)
    assert handler.dumps(data) == serialize.dumps(data)


def test_both_printers_share_the_same_block_keys_and_inline_limit():
    # A divergence in either would show up only on some inputs, and this says
    # which knob moved rather than leaving the parity test to guess.
    assert handler._BLOCK_KEYS == serialize._BLOCK_KEYS
    assert handler._INLINE_LIMIT == serialize._INLINE_LIMIT


@pytest.mark.parametrize(
    "text",
    ["", "\n", "one\ntwo", "trailing\n", "\r\n mixed   breaks \x0b\x0c"],
)
def test_split_text_matches_the_host_and_round_trips(text):
    assert handler.split_text(text) == serialize.split_text(text)
    assert "\n".join(handler.split_text(text)) == text


# -- where the text goes ----------------------------------------------------

@pytest.mark.parametrize(
    "project_name,expected",
    [
        ("scene.toe", "scene.network.json"),
        ("scene.1.toe", "scene.network.json"),
        ("scene.12.toe", "scene.network.json"),
        ("atlas-live.3.TOE", "atlas-live.network.json"),
        ("component.tox", "component.network.json"),
        ("no-extension", "no-extension.network.json"),
        ("dotted.name.toe", "dotted.name.network.json"),
        ("", "project.network.json"),
    ],
)
def test_sidecar_name_strips_the_version_touchdesigner_adds(project_name, expected):
    assert handler.sidecar_name(project_name) == expected


def test_successive_saves_of_one_project_reuse_one_text_file():
    # The point of stripping the version: TouchDesigner hands back a new
    # project.name on every save, and a name that followed it would leave a
    # new file behind each time and diff against nothing.
    names = {handler.sidecar_name("scene.%d.toe" % n) for n in range(1, 20)}
    assert names == {"scene.network.json"}


# -- never overwriting somebody else's file ---------------------------------

def _text(**overrides):
    data = {
        "source": "scene.toe",
        "build": {"build": "2025.32460"},
        "path": "/",
        "operator_count": 1,
        "operators": [],
    }
    data.update(overrides)
    return handler.dumps(data)


def test_our_own_text_is_recognised():
    assert handler.is_our_text(_text()) is True


def test_a_text_written_by_the_host_command_is_recognised():
    # `td-atlas project text` writes the same format; a file it produced is
    # ours to replace.
    from td_atlas.project.model import Project
    from pathlib import Path

    project = Project(source=Path("scene.toe"), build={"build": "x"})
    assert handler.is_our_text(serialize.to_text(project)) is True


@pytest.mark.parametrize(
    "content",
    [
        "",
        "not json at all",
        "[1, 2, 3]",
        '"a string"',
        "null",
        '{"source": "x"}',
        '{"operators": [], "build": {}, "path": "/", "operator_count": 0}',
        json.dumps({"name": "somebody's package.json", "version": "1.0.0"}),
    ],
)
def test_anything_else_is_not_ours(content):
    assert handler.is_our_text(content) is False


# -- the atomic write -------------------------------------------------------

def test_write_lands_the_whole_text_and_leaves_no_temporary(tmp_path):
    target = tmp_path / "scene.network.json"
    handler.write_text_atomic(str(target), "hello\n")
    assert target.read_text() == "hello\n"
    assert [p.name for p in tmp_path.iterdir()] == ["scene.network.json"]


def test_write_replaces_in_place_without_a_moment_of_absence(tmp_path):
    target = tmp_path / "scene.network.json"
    target.write_text("old\n")
    handler.write_text_atomic(str(target), "new\n")
    assert target.read_text() == "new\n"
    assert len(list(tmp_path.iterdir())) == 1


def test_a_writer_that_dies_mid_text_leaves_the_previous_file_intact(tmp_path, monkeypatch):
    target = tmp_path / "scene.network.json"
    target.write_text("the previous text\n")

    class Halfway(Exception):
        pass

    real_open = open

    def failing_open(path, *args, **kwargs):
        handle = real_open(path, *args, **kwargs)
        if str(path) != str(target):
            original_write = handle.write

            def write(chunk):
                original_write(chunk[: len(chunk) // 2])
                raise Halfway("the disk went away")

            handle.write = write
        return handle

    monkeypatch.setitem(handler.__dict__, "open", failing_open)
    with pytest.raises(Halfway):
        handler.write_text_atomic(str(target), "a much longer new text\n")

    # The old file is untouched, and no stump is left beside it.
    assert target.read_text() == "the previous text\n"
    assert [p.name for p in tmp_path.iterdir()] == ["scene.network.json"]


def test_the_temporary_is_made_in_the_same_directory(tmp_path):
    # A temporary somewhere else would make the replace a copy across
    # filesystems, which is not atomic.
    seen = []
    real_replace = os.replace

    def watching_replace(src, dst):
        seen.append((os.path.dirname(src), os.path.dirname(dst)))
        real_replace(src, dst)

    target = tmp_path / "sub" / "scene.network.json"
    target.parent.mkdir()
    original = os.replace
    os.replace = watching_replace
    try:
        handler.write_text_atomic(str(target), "x\n")
    finally:
        os.replace = original
    assert seen == [(str(tmp_path / "sub"), str(tmp_path / "sub"))]


# -- the decision, with every side effect handed in -------------------------

def _harness(tmp_path, enabled=True, gather=None, write=None):
    """`externalise` wired to a real directory, recording what it did."""
    calls = {"gathered": 0, "wrote": []}

    def default_gather():
        calls["gathered"] += 1
        return {
            "source": "scene.toe",
            "build": {"build": "2025.32460"},
            "path": "/",
            "operator_count": 3,
            "operators": [],
        }

    def default_write(path, text):
        calls["wrote"].append(path)
        return handler.write_text_atomic(path, text)

    outcome = handler.externalise(
        str(tmp_path),
        "scene.4.toe",
        enabled,
        gather or default_gather,
        write or default_write,
        os.path.exists,
        lambda path: open(path, encoding="utf-8", errors="replace").read(),
    )
    return outcome, calls


def test_the_switch_off_writes_nothing_and_reads_nothing(tmp_path):
    outcome, calls = _harness(tmp_path, enabled=False)
    assert outcome == {"wrote": False, "note": "off"}
    assert calls["gathered"] == 0
    assert list(tmp_path.iterdir()) == []


def test_the_switch_on_writes_the_text(tmp_path):
    outcome, calls = _harness(tmp_path)
    assert outcome["wrote"] is True
    assert outcome["operators"] == 3
    written = tmp_path / "scene.network.json"
    assert written.exists()
    assert json.loads(written.read_text())["operator_count"] == 3
    assert outcome["note"] == "scene.network.json  3 ops"


def test_a_second_save_overwrites_our_own_text(tmp_path):
    _harness(tmp_path)
    outcome, _ = _harness(tmp_path)
    assert outcome["wrote"] is True
    assert len(list(tmp_path.iterdir())) == 1


def test_a_foreign_file_in_the_way_is_refused_and_left_alone(tmp_path):
    victim = tmp_path / "scene.network.json"
    victim.write_text("someone else's notes\n")
    outcome, calls = _harness(tmp_path)
    assert outcome["wrote"] is False
    assert "refused" in outcome["note"]
    assert victim.read_text() == "someone else's notes\n"
    # Nothing was even read out of the network for it.
    assert calls["gathered"] == 0


def test_a_gather_that_raises_does_not_escape(tmp_path):
    def exploding():
        raise RuntimeError("an operator went away mid-walk")

    outcome, calls = _harness(tmp_path, gather=exploding)
    assert outcome["wrote"] is False
    assert outcome["note"] == "failed: RuntimeError: an operator went away mid-walk"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "error",
    [
        OSError(13, "Permission denied"),
        MemoryError(),
        KeyboardInterrupt(),
        RecursionError("maximum recursion depth exceeded"),
    ],
)
def test_a_writer_that_fails_any_way_at_all_does_not_escape(tmp_path, error):
    """The failing-writer test the contract names.

    A save must never fail because of this, so the outermost frame catches
    everything a writer can throw — including the ones that are not
    `Exception` subclasses by accident of the hierarchy.
    """
    def exploding(path, text):
        raise error

    outcome, _ = _harness(tmp_path, write=exploding)
    assert outcome["wrote"] is False
    assert outcome["note"].startswith("failed: %s" % type(error).__name__)
    assert list(tmp_path.iterdir()) == []


@posix_access_refusal_only
def test_a_folder_that_cannot_be_written_does_not_escape(tmp_path):
    read_only = tmp_path / "locked"
    read_only.mkdir()
    read_only.chmod(0o500)
    try:
        outcome, _ = _harness(read_only)
    finally:
        read_only.chmod(0o700)
    assert outcome["wrote"] is False
    assert outcome["note"].startswith("failed: ")


def test_a_reader_that_raises_is_treated_as_a_foreign_file(tmp_path):
    (tmp_path / "scene.network.json").write_text(_text())

    def unreadable(path):
        raise OSError(13, "Permission denied")

    outcome = handler.externalise(
        str(tmp_path),
        "scene.4.toe",
        True,
        lambda: {"operator_count": 0},
        lambda path, text: None,
        os.path.exists,
        unreadable,
    )
    assert outcome["wrote"] is False
    assert "refused" in outcome["note"]


# -- the switch -------------------------------------------------------------

def test_the_switch_is_off_when_the_config_says_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("TD_ATLAS_HOME", str(tmp_path))
    (tmp_path / "config.json").write_text(json.dumps({"port": 9977}))
    assert handler.text_on_save_enabled() is False


def test_the_switch_is_off_when_there_is_no_config_at_all(tmp_path, monkeypatch):
    monkeypatch.setenv("TD_ATLAS_HOME", str(tmp_path))
    assert handler.text_on_save_enabled() is False


@pytest.mark.parametrize("content", ["", "{", "[]", '"x"', "null"])
def test_a_broken_config_leaves_the_switch_off(tmp_path, monkeypatch, content):
    monkeypatch.setenv("TD_ATLAS_HOME", str(tmp_path))
    (tmp_path / "config.json").write_text(content)
    assert handler.text_on_save_enabled() is False


@pytest.mark.parametrize("value,expected", [(True, True), (False, False), (1, True), (0, False)])
def test_the_switch_follows_the_config_key(tmp_path, monkeypatch, value, expected):
    monkeypatch.setenv("TD_ATLAS_HOME", str(tmp_path))
    (tmp_path / "config.json").write_text(
        json.dumps({handler.TEXT_ON_SAVE_KEY: value})
    )
    assert handler.text_on_save_enabled() is expected


# -- the panel's fifth line -------------------------------------------------

def test_the_panel_keeps_its_four_lines_until_a_save_has_happened():
    assert len(handler.render_panel({}).splitlines()) == 4


def test_the_panel_grows_a_fifth_line_once_a_save_has_run():
    lines = handler.render_panel(
        {"text": "scene.network.json  12 ops", "textAt": "1700000000"}
    ).splitlines()
    assert len(lines) == 5
    assert lines[4].startswith("text")
    assert "scene.network.json  12 ops" in lines[4]
    assert "  at " in lines[4]


def test_the_panel_says_so_when_the_switch_is_off():
    lines = handler.render_panel({"text": "off"}).splitlines()
    assert lines[4] == handler._panel_line("text", "off")


# -- the subscription itself ------------------------------------------------

def test_the_shim_compiles_and_defines_every_execute_callback():
    scope: dict = {}
    exec(compile(handler.SAVE_SHIM, "onsave", "exec"), scope)
    for name in (
        "onProjectPostSave",
        "onProjectPreSave",
        "onStart",
        "onCreate",
        "onExit",
        "onFrameStart",
        "onFrameEnd",
        "onPlayStateChange",
        "onDeviceChange",
    ):
        assert callable(scope[name]), name


def test_the_shim_only_forwards_to_the_handler():
    """The logic lives in the tested module, not in the DAT nobody imports."""
    calls = []

    class Module:
        def on_project_post_save(self):
            calls.append(True)

    class Parent:
        def op(self, name):
            assert name == "handler"
            return type("DAT", (), {"module": Module()})()

    class Me:
        def parent(self):
            return Parent()

    scope: dict = {"me": Me()}
    exec(compile(handler.SAVE_SHIM, "onsave", "exec"), scope)
    scope["onProjectPostSave"]()
    assert calls == [True]


def test_the_shim_subscribes_to_the_save_and_nothing_else():
    assert dict(handler.SAVE_PARS) == {"active": "1", "projectpostsave": "1"}
    # Pre-save is deliberately not subscribed: the file does not exist yet.
    assert "projectpresave" not in dict(handler.SAVE_PARS)


def test_the_released_tox_carries_the_subscription(tmp_path):
    from pathlib import Path

    from td_atlas.install import TDInstall

    install = TDInstall(Path("/nowhere"), Path("/nowhere/tfs"), "2025.32460", None)
    root = tmp_path / "tree"
    names = release.write_tree(root, 9977, install)

    for suffix in (".n", ".parm", ".text"):
        assert "tdatlas/onsave" + suffix in names

    node = (root / "tdatlas" / "onsave.n").read_text()
    assert node.startswith("DAT:execute\n")
    parm = (root / "tdatlas" / "onsave.parm").read_text()
    assert "projectpostsave 0 1" in parm
    assert "active 0 1" in parm

    from td_atlas.project.formats import read_payload

    payload = read_payload((root / "tdatlas" / "onsave.text").read_bytes())
    assert payload == handler.SAVE_SHIM


def test_the_textport_install_lays_out_the_same_dat_as_the_tox():
    """Both installs read the shape from the handler, so neither can drift."""
    bootstrap = (
        release._HANDLER_SOURCE.parent / "bootstrap.py"
    ).read_text()
    assert "shape.SAVE_DAT" in bootstrap
    assert "shape.SAVE_SHIM" in bootstrap
    assert "shape.SAVE_PARS" in bootstrap
    assert "executeDAT" in bootstrap


def test_the_handler_still_imports_on_the_host_with_no_touchdesigner():
    # handler.py is imported here and by project/release.py; a module-level
    # reference to `op`, `project` or `root` would break both.
    import importlib

    importlib.reload(handler)
    assert handler.TEXT_SUFFIX == ".network.json"


# -- the cap on how much a save is allowed to cost --------------------------

def _capped(tmp_path, total, max_ops):
    calls = {"gathered": 0}

    def gather():
        calls["gathered"] += 1
        return {
            "source": "scene.toe",
            "build": {},
            "path": "/",
            "operator_count": total,
            "operators": [],
        }

    outcome = handler.externalise(
        str(tmp_path),
        "scene.1.toe",
        True,
        gather,
        handler.write_text_atomic,
        os.path.exists,
        lambda path: open(path, encoding="utf-8").read(),
        lambda: total,
        max_ops,
    )
    return outcome, calls


def test_a_network_over_the_cap_is_skipped_before_anything_is_read(tmp_path):
    outcome, calls = _capped(tmp_path, total=4093, max_ops=1000)
    assert outcome["wrote"] is False
    # Both numbers, so the artist can see what to raise the cap to.
    assert outcome["note"] == "skipped: 4093 ops over the 1000 cap"
    assert calls["gathered"] == 0
    assert list(tmp_path.iterdir()) == []


def test_a_network_at_the_cap_is_still_written(tmp_path):
    outcome, _ = _capped(tmp_path, total=1000, max_ops=1000)
    assert outcome["wrote"] is True


def test_one_operator_over_the_cap_is_already_too_many(tmp_path):
    # The cap is a limit, not a hint: the pair of tests around it pins both
    # sides of the boundary, which "at the cap" alone does not.
    outcome, _ = _capped(tmp_path, total=1001, max_ops=1000)
    assert outcome["wrote"] is False
    assert outcome["note"] == "skipped: 1001 ops over the 1000 cap"


def test_no_cap_means_no_counting_at_all(tmp_path):
    outcome, calls = _capped(tmp_path, total=100000, max_ops=0)
    assert outcome["wrote"] is True
    assert calls["gathered"] == 1


def test_the_cap_defaults_to_two_thousand_operators(tmp_path, monkeypatch):
    monkeypatch.setenv("TD_ATLAS_HOME", str(tmp_path))
    assert handler.text_max_ops() == handler.DEFAULT_MAX_OPS == 2000


@pytest.mark.parametrize(
    "value,expected", [(5000, 5000), ("2500", 2500), (0, 0), (-1, -1)]
)
def test_the_cap_follows_the_config_key(tmp_path, monkeypatch, value, expected):
    monkeypatch.setenv("TD_ATLAS_HOME", str(tmp_path))
    (tmp_path / "config.json").write_text(
        json.dumps({handler.TEXT_MAX_OPS_KEY: value})
    )
    assert handler.text_max_ops() == expected


@pytest.mark.parametrize("value", ["lots", None, [], {}])
def test_a_nonsense_cap_falls_back_to_the_default(tmp_path, monkeypatch, value):
    monkeypatch.setenv("TD_ATLAS_HOME", str(tmp_path))
    (tmp_path / "config.json").write_text(
        json.dumps({handler.TEXT_MAX_OPS_KEY: value})
    )
    assert handler.text_max_ops() == handler.DEFAULT_MAX_OPS
