"""A file-synced Text DAT is reloaded before exec, render and a timeline walk.

TouchDesigner polls a synced file: measured 2026-09-29 on 2025.32460, an edit
reached the DAT 0.68-0.71 s later. A frame taken inside that window ran the
old code (agent report 4, point 8). Setting `.text` writes the file at once,
so a file that differs was edited outside and reloading it is safe.
"""

from __future__ import annotations

from td_atlas.component import handler
from td_atlas.mcp.server import _resynced_note


class Par:
    def __init__(self, value):
        self.value = value
        self.pulsed = 0

    def eval(self):
        return self.value

    def pulse(self):
        self.pulsed += 1


class Pars:
    pass


class Dat:
    def __init__(self, path, text, file, sync=True):
        self.path, self.text = path, text
        self.par = Pars()
        self.par.syncfile = Par(sync)
        self.par.file = Par(str(file))
        self.par.loadonstartpulse = Par(None)


def _root(monkeypatch, dats):
    class Root:
        @staticmethod
        def findChildren(type=None):
            return dats

    monkeypatch.setattr(handler, "root", Root, raising=False)
    monkeypatch.setattr(handler, "textDAT", object(), raising=False)


def test_a_dat_behind_its_file_is_reloaded_and_named(tmp_path, monkeypatch):
    file = tmp_path / "sim.py"
    file.write_text("x = 2\n")
    behind = Dat("/p/sim", "x = 1\n", file)
    current = Dat("/p/cfg", "x = 2\n", file)
    unsynced = Dat("/p/free", "other\n", file, sync=False)
    _root(monkeypatch, [behind, current, unsynced])
    assert handler._resync_files() == ["/p/sim"]
    assert behind.par.loadonstartpulse.pulsed == 1
    assert current.par.loadonstartpulse.pulsed == 0
    assert unsynced.par.loadonstartpulse.pulsed == 0


def test_line_endings_alone_are_not_a_difference(tmp_path, monkeypatch):
    file = tmp_path / "sim.py"
    file.write_bytes(b"x = 1\r\n")
    _root(monkeypatch, [Dat("/p/sim", "x = 1\n", file)])
    assert handler._resync_files() == []


def test_a_missing_file_is_skipped_not_raised(tmp_path, monkeypatch):
    _root(monkeypatch, [Dat("/p/sim", "x", tmp_path / "gone.py")])
    assert handler._resync_files() == []


def test_the_host_says_it_in_one_line():
    assert _resynced_note({}) == ""
    assert "/p/sim" in _resynced_note({"resynced": ["/p/sim"]})
