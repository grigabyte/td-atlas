"""The expansion cache has a ceiling, and reaching it evicts by last use.

Every .toe or .tox read unpacks into ~/.td-atlas/cache and nothing ever removed
an entry: on the machine this was found on the cache had reached 2209
expansions and 4.9 GB. A test that watches the cache across two ordinary test
runs proves nothing — the suite expands almost nothing — so the ceiling is set
low here and the entries are counted directly.

`toeexpand` is faked: the point under test is the cache, and the real tool
needs a TouchDesigner installation.
"""

from __future__ import annotations

import argparse
import importlib
import os
import time

import pytest

from td_atlas import cli

# `td_atlas.project` re-exports the `expand` function under that name, so the
# module has to be asked for by path.
expand_mod = importlib.import_module("td_atlas.project.expand")


@pytest.fixture
def fake_toeexpand(monkeypatch, tmp_path):
    """`expand()` end to end, with the unpacking replaced by a mkdir."""

    def fake_run(argv, cwd=None, **kwargs):
        target = os.path.join(str(cwd), f"{argv[1]}.dir")
        os.makedirs(target, exist_ok=True)
        with open(os.path.join(target, "root.n"), "w") as handle:
            handle.write("n\n")
        return type("Completed", (), {"stdout": "", "stderr": "", "returncode": 1})()

    monkeypatch.setattr(expand_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(expand_mod, "_tool", lambda install, name: tmp_path / name)
    monkeypatch.setattr(
        expand_mod, "discover", lambda: type("I", (), {"root": tmp_path})()
    )


def _source(tmp_path, name):
    path = tmp_path / f"{name}.tox"
    path.write_bytes(b"not really a tox, but a distinct file\n" + name.encode())
    return path


def test_expanding_evicts_the_oldest_once_the_cache_is_over_its_ceiling(
    monkeypatch, tmp_path, fake_toeexpand
):
    monkeypatch.setattr(expand_mod, "_MAX_CACHED_EXPANSIONS", 3)

    for index in range(6):
        expand_mod.expand(_source(tmp_path, f"p{index}"))
        # Distinct mtimes: the eviction order is the only thing under test and
        # a same-second tie would decide it by luck.
        time.sleep(0.01)

    names = sorted(p.name.split("-")[0] for p in expand_mod.cached_expansions())
    assert names == ["p3", "p4", "p5"]


def test_one_expansion_never_deletes_more_than_its_share(monkeypatch, tmp_path):
    """A backlog is trimmed across calls, not in one long freeze inside a read."""
    monkeypatch.setattr(expand_mod, "_MAX_EVICTIONS_PER_CALL", 2)
    cache = expand_mod.cache_dir()
    cache.mkdir(parents=True, exist_ok=True)
    for index in range(10):
        (cache / f"old{index}-0000000000000000").mkdir()
        time.sleep(0.002)

    assert expand_mod.evict(keep=1) == 2
    assert len(expand_mod.cached_expansions()) == 8
    assert expand_mod.evict(keep=1) == 2
    assert len(expand_mod.cached_expansions()) == 6


def test_a_cache_hit_is_a_use_so_eviction_is_by_last_use_not_by_age(
    monkeypatch, tmp_path, fake_toeexpand
):
    monkeypatch.setattr(expand_mod, "_MAX_CACHED_EXPANSIONS", 2)

    first = _source(tmp_path, "first")
    expand_mod.expand(first)
    time.sleep(0.01)
    expand_mod.expand(_source(tmp_path, "second"))
    time.sleep(0.01)

    # Re-reading `first` hits the cache; that has to count as a use.
    again = expand_mod.expand(first)
    assert again.cached is True
    time.sleep(0.01)

    expand_mod.expand(_source(tmp_path, "third"))

    names = sorted(p.name.split("-")[0] for p in expand_mod.cached_expansions())
    assert names == ["first", "third"]


def test_a_rebuild_in_progress_is_never_evicted(monkeypatch, tmp_path, fake_toeexpand):
    monkeypatch.setattr(expand_mod, "_MAX_CACHED_EXPANSIONS", 1)
    work = expand_mod.cache_dir()
    work.mkdir(parents=True, exist_ok=True)
    in_progress = work / f"{expand_mod.WORK_PREFIX}abc123"
    in_progress.mkdir()

    for index in range(3):
        expand_mod.expand(_source(tmp_path, f"p{index}"))
        time.sleep(0.01)

    assert in_progress.is_dir()
    assert in_progress not in expand_mod.cached_expansions()


def test_a_second_process_recreating_the_directory_is_not_an_error(
    monkeypatch, tmp_path, fake_toeexpand
):
    """The race the old code raised FileExistsError on, outside ExpandError.

    Standing in for the loser of the race: the directory is still there after
    the rmtree, because the other process put it back.
    """
    source = _source(tmp_path, "raced")
    expand_mod.expand(source)
    source.write_bytes(b"changed, so the key changes and the tree is rebuilt")
    monkeypatch.setattr(expand_mod.shutil, "rmtree", lambda *a, **k: None)
    target = expand_mod.cache_dir() / expand_mod._cache_key(source.resolve())
    target.mkdir(parents=True, exist_ok=True)

    result = expand_mod.expand(source)

    assert result.root.is_dir()


def test_clear_cache_removes_every_entry(monkeypatch, tmp_path, fake_toeexpand):
    for index in range(3):
        expand_mod.expand(_source(tmp_path, f"p{index}"))

    removed = expand_mod.clear_cache()

    assert removed == 3
    assert expand_mod.cached_expansions() == []


def test_doctor_can_empty_the_cache_and_says_how_big_it_is(
    monkeypatch, tmp_path, capsys, fake_toeexpand
):
    """`clear_cache` had no caller anywhere in the repository."""
    for index in range(2):
        expand_mod.expand(_source(tmp_path, f"p{index}"))
    monkeypatch.setattr(
        cli, "doctor_checks",
        lambda args, run=None: [cli.Check("bridge", cli.ABSENT, "not running")],
    )
    args = argparse.Namespace(
        db=None, install_path=None, port=None, project=None, clear_cache=True
    )

    code = cli.cmd_doctor(args)
    text = capsys.readouterr().out

    assert code == 0
    assert "removed 2 cached expansion(s)" in text
    assert "0 expansion(s) cached" in text
    assert expand_mod.cached_expansions() == []


def test_doctor_leaves_the_cache_alone_unless_asked(
    monkeypatch, tmp_path, capsys, fake_toeexpand
):
    expand_mod.expand(_source(tmp_path, "kept"))
    monkeypatch.setattr(
        cli, "doctor_checks",
        lambda args, run=None: [cli.Check("bridge", cli.ABSENT, "not running")],
    )
    args = argparse.Namespace(
        db=None, install_path=None, port=None, project=None, clear_cache=False
    )

    cli.cmd_doctor(args)
    text = capsys.readouterr().out

    assert "removed" not in text
    assert "1 expansion(s) cached" in text
    assert len(expand_mod.cached_expansions()) == 1


def test_the_doctor_parser_accepts_the_flag():
    parser = cli.build_parser()
    args = parser.parse_args(["doctor", "--clear-cache"])

    assert args.clear_cache is True
