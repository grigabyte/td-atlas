"""`td-atlas doctor`: one line per link, and a code that means something.

Every case here is a faked world — a scratch ~/.td-atlas, a synthetic
TouchDesigner tree, a stubbed HTTP transport and a stubbed subprocess — so
none of it needs TouchDesigner installed or running. The case the command was
written for is `test_an_index_from_another_build_is_the_loud_failure`: an
index built from a different build answers every question without error, and
nothing else in this tool compares the two numbers.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import types

import pytest

from td_atlas import cli
from td_atlas import config as cfg
from td_atlas.atoms.store import AtomStore
from td_atlas.bridge import client as client_mod
from td_atlas.cli import main
from td_atlas.install import InstallNotFound, TDInstall

LIVE_PID = 4211
PORT = 9977


# -- the fake world ----------------------------------------------------------

def make_install(tmp_path, version="2025.32460"):
    """A TDInstall whose every index source exists, so nothing is missing."""
    tfs = tmp_path / "TouchDesigner.app" / "tfs"
    (tfs / "Config" / "Help").mkdir(parents=True)
    (tfs / "Config" / "TDParameterHelp.json").write_text("{}")
    (tfs / "Config" / "Help" / "command.help").write_text("")
    (tfs / "Config" / "Help" / "exprhelp").mkdir()
    (tfs / "Samples" / "Learn" / "OfflineHelp").mkdir(parents=True)
    (tfs / "Samples" / "Learn" / "OPSnippets" / "Snippets").mkdir(parents=True)
    return TDInstall(
        root=tmp_path / "TouchDesigner.app", tfs=tfs, version=version, executable=None
    )


def make_index(path, build="2025.32460", install_root="/Applications/TouchDesigner.app",
               probed=True):
    store = AtomStore(path)
    store.create()
    store.set_meta("td_version", build)
    store.set_meta("td_install", install_root)
    store.set_meta("static_pass", "complete")
    if probed:
        store.set_meta("runtime_pass", "complete")
        store.set_meta("runtime_types_probed", "647")
    store.conn.commit()
    store.close()
    return path


def ok_subprocess(*_a, **_kw):
    return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")


def failing_subprocess(*_a, **_kw):
    return subprocess.CompletedProcess(
        args=[],
        returncode=1,
        stdout="",
        stderr="Traceback (most recent call last):\n"
        "ModuleNotFoundError: No module named 'td_atlas'\n",
    )


def install_fake_bridge(monkeypatch, *, protocol=None, reject_token=False):
    """Answer `ping` over a stubbed urlopen: a version, or an auth refusal."""
    if protocol is None:
        protocol = client_mod.EXPECTED_PROTOCOL_VERSION

    class _Response:
        def __init__(self, payload):
            self._body = json.dumps(payload).encode()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return self._body

    def fake_urlopen(request, timeout=None):
        if reject_token:
            return _Response(
                {"ok": False, "error": {"type": "AuthError", "message": "bad token"}}
            )
        return _Response(
            {
                "ok": True,
                "result": {
                    "protocol": protocol,
                    "project": "NewProject.toe",
                    "build": "2025.32460",
                    "fps": 60.0,
                },
            }
        )

    monkeypatch.setattr(client_mod.urllib.request, "urlopen", fake_urlopen)


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A healthy chain: sane environment, an install, a matching probed index,
    one live registered bridge, and an MCP launch line that starts.

    Each test breaks exactly one link and checks that only that link changes.
    """
    home = tmp_path / "home"
    (home / "instances").mkdir(parents=True)
    monkeypatch.setenv("TD_ATLAS_HOME", str(home))
    home.joinpath("config.json").write_text(
        json.dumps({"port": PORT, "token": "s3cret"})
    )

    monkeypatch.setattr(cli, "broken_env_diagnosis", lambda _prefix: None)
    monkeypatch.setattr(
        cli, "broken_editable_install_diagnosis", lambda *a, **k: None
    )

    install = make_install(tmp_path)
    monkeypatch.setattr(cli, "discover", lambda _explicit=None: install)

    db = tmp_path / "atlas.db"
    make_index(db, build=install.version, install_root=str(install.root))

    monkeypatch.setattr(cfg, "pid_alive", lambda pid: int(pid) == LIVE_PID)
    monkeypatch.setattr(
        cfg, "port_listening", lambda port, timeout=0.25: int(port) == PORT
    )
    (home / "instances" / f"{PORT}.json").write_text(
        json.dumps(
            {
                "port": PORT,
                "project": "NewProject.toe",
                "projectPath": "/Users/artist/NewProject.toe",
                "build": "2025.32460",
                "pid": LIVE_PID,
                "component": "/tdatlas",
                "protocol": client_mod.EXPECTED_PROTOCOL_VERSION,
                "updated": 1e12,
            }
        )
    )
    install_fake_bridge(monkeypatch)
    monkeypatch.setattr(subprocess, "run", ok_subprocess)

    return types.SimpleNamespace(
        home=home, install=install, db=db, tmp=tmp_path, monkeypatch=monkeypatch
    )


def run_doctor(world, argv=()):
    return main(["--db", str(world.db), "doctor", *argv])


def checks(world) -> dict[str, cli.Check]:
    args = argparse.Namespace(
        db=str(world.db), install_path=None, port=None, project=None
    )
    return {c.link: c for c in cli.doctor_checks(args)}


LINKS = {"environment", "touchdesigner", "index", "index build", "probe",
         "bridge", "mcp server"}


# -- the failure this command exists for -------------------------------------

def test_an_index_from_another_build_is_the_loud_failure(world, capsys):
    make_index(world.db, build="2023.11600", install_root="/old/TouchDesigner.app")

    code = run_doctor(world)
    out = capsys.readouterr().out

    assert code == 1
    result = checks(world)
    assert result["index build"].status == cli.FAIL
    # Both builds named, and the reason it is dangerous stated: no error is
    # raised anywhere downstream.
    assert "2023.11600" in result["index build"].detail
    assert "2025.32460" in result["index build"].detail
    assert "Nothing will raise" in result["index build"].detail
    assert "td-atlas build" in result["index build"].fix
    # The index itself is intact, so only the comparison fails.
    assert result["index"].status == cli.OK
    assert "MISMATCH" in out


def test_a_matching_build_passes(world):
    result = checks(world)
    assert result["index build"].status == cli.OK
    assert world.install.version in result["index build"].detail


def test_a_build_that_cannot_be_compared_says_so_instead_of_passing(world):
    def missing(_explicit=None):
        raise InstallNotFound("No TouchDesigner installation found.")

    world.monkeypatch.setattr(cli, "discover", missing)

    result = checks(world)
    assert result["touchdesigner"].status == cli.FAIL
    assert "TD_ATLAS_INSTALL" in result["touchdesigner"].fix
    # The comparison could not be made — reported, not skipped, not a pass.
    assert result["index build"].status == cli.UNKNOWN
    assert "no installation was found" in result["index build"].detail


# -- index and probe ---------------------------------------------------------

def test_a_missing_index_fails_and_leaves_two_links_unknown(world):
    world.db.unlink()

    result = checks(world)
    assert result["index"].status == cli.FAIL
    assert result["index"].fix == "td-atlas build"
    assert result["index build"].status == cli.UNKNOWN
    assert result["probe"].status == cli.UNKNOWN
    assert run_doctor(world) == 1


def test_an_unprobed_index_warns_and_names_what_is_missing(world):
    make_index(world.db, build=world.install.version, probed=False)

    result = checks(world)
    assert result["probe"].status == cli.WARN
    for phrase in ("defaults", "ranges", "menu options"):
        assert phrase in result["probe"].detail
    assert "td-atlas probe" in result["probe"].fix
    # A known gap is not a breakage: the chain still works offline.
    assert run_doctor(world) == 0


def test_an_index_that_is_not_a_database_fails_rather_than_raising(world):
    world.db.write_bytes(b"not a database at all")

    result = checks(world)
    assert result["index"].status == cli.FAIL
    assert result["index build"].status == cli.UNKNOWN


# -- bridge ------------------------------------------------------------------

def test_an_empty_registry_with_a_silent_port_is_a_state_not_a_failure(world, monkeypatch):
    (world.home / "instances" / f"{PORT}.json").unlink()
    monkeypatch.setattr(cfg, "port_listening", lambda port, timeout=0.25: False)

    result = checks(world)
    assert result["bridge"].status == cli.ABSENT
    assert "not a fault" in result["bridge"].detail
    assert run_doctor(world) == 0


def test_an_unregistered_bridge_that_answers_is_not_reported_absent(world):
    """A bridge older than the registry writes no record but still answers.

    Declaring absence from the registry alone would call a working bridge
    missing — the port is the fact both sides agree on, so it gets dialled.
    """
    (world.home / "instances" / f"{PORT}.json").unlink()

    result = checks(world)
    assert result["bridge"].status == cli.OK
    assert "older than the instance registry" in result["bridge"].detail
    assert run_doctor(world) == 0


def test_a_host_where_the_bridge_was_never_staged_warns(world):
    (world.home / "instances" / f"{PORT}.json").unlink()
    (world.home / "config.json").unlink()

    result = checks(world)
    assert result["bridge"].status == cli.WARN
    assert result["bridge"].fix == "td-atlas install"
    assert run_doctor(world) == 0


def test_a_bridge_speaking_an_unsupported_protocol_fails(world):
    install_fake_bridge(world.monkeypatch, protocol=0)

    result = checks(world)
    assert result["bridge"].status == cli.FAIL
    assert "protocol 0" in result["bridge"].detail
    assert "minimum" in result["bridge"].detail
    assert run_doctor(world) == 1


def test_a_bridge_one_version_behind_warns_without_failing(world, monkeypatch):
    monkeypatch.setattr(client_mod, "MIN_PROTOCOL_VERSION", 1)
    monkeypatch.setattr(client_mod, "EXPECTED_PROTOCOL_VERSION", 3)
    install_fake_bridge(world.monkeypatch, protocol=2)

    result = checks(world)
    assert result["bridge"].status == cli.WARN
    assert "older" in result["bridge"].detail
    assert run_doctor(world) == 0


def test_a_bridge_that_rejects_the_token_is_told_apart_from_a_silent_one(world):
    install_fake_bridge(world.monkeypatch, reject_token=True)

    result = checks(world)
    assert result["bridge"].status == cli.FAIL
    assert "answers but rejected the request" in result["bridge"].detail
    assert "token" in result["bridge"].detail
    assert "td-atlas reload" in result["bridge"].fix
    assert run_doctor(world) == 1


def test_a_registered_bridge_whose_port_is_silent_fails(world, monkeypatch):
    # The record is still there and its process still runs, but nothing
    # answers the port: a different repair from a rejected token.
    monkeypatch.setattr(cfg, "port_listening", lambda port, timeout=0.25: None)

    def refuse(request, timeout=None):
        raise client_mod.urllib.error.URLError("Connection refused")

    monkeypatch.setattr(client_mod.urllib.request, "urlopen", refuse)

    result = checks(world)
    assert result["bridge"].status == cli.FAIL
    assert "Cannot reach TouchDesigner" in result["bridge"].detail
    assert run_doctor(world) == 1


def test_a_healthy_bridge_reports_its_protocol_against_the_expected_one(world):
    result = checks(world)
    assert result["bridge"].status == cli.OK
    assert f"/{client_mod.EXPECTED_PROTOCOL_VERSION} expected" in result["bridge"].detail


# -- environment and the MCP launch line -------------------------------------

def test_a_broken_environment_fails_with_the_command_that_repairs_it(world):
    world.monkeypatch.setattr(
        cli, "broken_env_diagnosis", lambda _prefix: "the base interpreter is gone"
    )

    result = checks(world)
    assert result["environment"].status == cli.FAIL
    assert "uv venv" in result["environment"].fix
    assert run_doctor(world) == 1


def test_the_mcp_launch_line_is_measured_not_assumed(world, monkeypatch):
    seen = {}

    def record(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["kwargs"] = kwargs
        return ok_subprocess()

    monkeypatch.setattr(subprocess, "run", record)
    result = checks(world)

    assert result["mcp server"].status == cli.OK
    # Launched the way an MCP client does: the printed interpreter, no
    # PYTHONPATH, a working directory that is not this project's.
    assert seen["cmd"][0] == cli.mcp_command()[0]
    assert "PYTHONPATH" not in seen["kwargs"]["env"]
    assert seen["kwargs"]["cwd"]


def test_an_unimportable_package_fails_the_mcp_link(world, monkeypatch):
    monkeypatch.setattr(subprocess, "run", failing_subprocess)

    result = checks(world)
    assert result["mcp server"].status == cli.FAIL
    assert "ModuleNotFoundError" in result["mcp server"].detail
    assert "--reinstall" in result["mcp server"].fix
    assert run_doctor(world) == 1


def test_a_launcher_that_cannot_be_started_fails_rather_than_raising(world, monkeypatch):
    def explode(*_a, **_kw):
        raise OSError("no such file")

    monkeypatch.setattr(subprocess, "run", explode)

    result = checks(world)
    assert result["mcp server"].status == cli.FAIL


# -- the shape of the report -------------------------------------------------

def test_a_healthy_chain_reports_every_link_and_exits_zero(world, capsys):
    code = run_doctor(world)
    out = capsys.readouterr().out

    assert code == 0
    assert "every link checked out." in out
    result = checks(world)
    assert set(result) == LINKS
    assert all(c.status == cli.OK for c in result.values())


def test_no_link_is_ever_silent(world):
    """Every link reports something even when nothing can be established."""
    world.db.unlink()
    (world.home / "instances" / f"{PORT}.json").unlink()
    world.monkeypatch.setattr(
        cli, "discover", lambda _e=None: (_ for _ in ()).throw(InstallNotFound("gone"))
    )

    result = checks(world)
    assert set(result) == LINKS
    assert all(c.detail for c in result.values())


def test_every_failed_link_carries_a_command_to_run(world):
    world.db.unlink()
    install_fake_bridge(world.monkeypatch, reject_token=True)
    world.monkeypatch.setattr(subprocess, "run", failing_subprocess)
    world.monkeypatch.setattr(
        cli, "broken_env_diagnosis", lambda _p: "the base interpreter is gone"
    )

    failures = [c for c in checks(world).values() if c.status == cli.FAIL]
    assert failures
    for check in failures:
        assert check.fix, f"{check.link} fails without saying what to run"
        assert "td-atlas" in check.fix or "uv " in check.fix


def test_the_summary_names_the_broken_links(world, capsys):
    world.db.unlink()
    world.monkeypatch.setattr(subprocess, "run", failing_subprocess)

    code = run_doctor(world)
    out = capsys.readouterr().out

    assert code == 1
    assert "2 broken links: index, mcp server" in out


def test_unresolved_links_are_summarised_without_failing(world, capsys):
    make_index(world.db, build=world.install.version, probed=False)

    code = run_doctor(world)
    out = capsys.readouterr().out

    assert code == 0
    assert "unresolved: probe (warn)" in out
