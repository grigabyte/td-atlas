"""The instance registry: who is running, and how a command reaches the right one.

Two TouchDesigner instances used to be indistinguishable — `session.json`
described whichever bridge loaded last and every command went there in
silence. These tests fake a registry directory (no TouchDesigner, no sockets)
and check the three things that silence cost: that both instances are listed,
that `--project`/`--port` land on the intended one, and that a record left
behind by a dead process is never mistaken for a live bridge.
"""

from __future__ import annotations

import errno
import json
import os
import stat
import time
from pathlib import Path

import pytest

from td_atlas import config as cfg
from td_atlas.bridge.client import BridgeClient
from td_atlas.cli import main
from td_atlas.component import handler
from windows_gaps import posix_mode_bits_only

# Captured before any test fakes them, so a test can ask for the real world back.
REAL_PID_ALIVE = cfg.pid_alive
REAL_PORT_LISTENING = cfg.port_listening

LIVE_PID = 4211
OTHER_PID = 4380
DEAD_PID = 5150


@pytest.fixture
def registry(tmp_path, monkeypatch):
    """A scratch ~/.td-atlas plus a fake world: these pids run, these ports listen.

    `pid_alive` and `port_listening` are the only two doors to the operating
    system in this code path, so faking them makes every case — alive, dead,
    a pid recycled by a stranger, a port that cannot be reached — deterministic
    on any machine.
    """
    home = tmp_path / "home"
    (home / "instances").mkdir(parents=True)
    monkeypatch.setenv("TD_ATLAS_HOME", str(home))

    running = {LIVE_PID, OTHER_PID}
    listening = {9977, 9978}
    monkeypatch.setattr(cfg, "pid_alive", lambda pid: int(pid) in running)
    monkeypatch.setattr(cfg, "port_listening", lambda port, timeout=0.25: int(port) in listening)
    home.joinpath("config.json").write_text(json.dumps({"port": 9977, "token": "s3cret"}))
    return home


def write_record(home, port, project, pid=LIVE_PID, folder="/Users/artist/td", **extra):
    record = {
        "port": port,
        "project": project,
        "projectPath": f"{folder}/{project}",
        "build": "2023.11600",
        "pid": pid,
        "component": "/tdatlas",
        "protocol": handler.PROTOCOL_VERSION,
        "updated": time.time(),
    }
    record.update(extra)
    path = home / "instances" / f"{port}.json"
    path.write_text(json.dumps(record, indent=2))
    return path


def two_instances(home):
    write_record(home, 9977, "Vessel.toe", pid=LIVE_PID)
    write_record(home, 9978, "Rehearsal.toe", pid=OTHER_PID)


# -- reading the registry ---------------------------------------------------

def test_both_live_instances_are_listed(registry):
    two_instances(registry)

    found = cfg.read_instances()

    assert [i.port for i in found] == [9977, 9978]
    assert [i.project for i in found] == ["Vessel.toe", "Rehearsal.toe"]
    assert all(i.alive for i in found)
    assert found[0].build == "2023.11600"
    assert found[0].component == "/tdatlas"
    assert found[0].protocol == handler.PROTOCOL_VERSION
    assert found[0].age < 5


def test_a_record_whose_process_is_gone_is_not_live(registry, monkeypatch):
    monkeypatch.setattr(cfg, "port_listening", lambda port, timeout=0.25: True)
    write_record(registry, 9977, "Abandoned.toe", pid=DEAD_PID)

    (record,) = cfg.read_instances(prune=False)

    assert record.alive is False
    assert record.dead_reason == f"no process {DEAD_PID} is running"


def test_a_record_with_nothing_on_its_port_is_not_live(registry):
    """The pid may be alive and be someone else entirely; the port cannot lie."""
    write_record(registry, 9999, "Ghost.toe", pid=LIVE_PID)

    (record,) = cfg.read_instances(prune=False)

    assert record.alive is False
    assert record.dead_reason == "nothing is listening on port 9999"


def test_an_unanswerable_probe_counts_as_alive(registry, monkeypatch):
    """A port that neither answers nor refuses is not evidence of death.

    Burying a live bridge is the expensive mistake: it was made once, against a
    running TouchDesigner, by a check whose two sides could not agree.
    """
    monkeypatch.setattr(cfg, "port_listening", lambda port, timeout=0.25: None)
    write_record(registry, 9977, "Vessel.toe", pid=LIVE_PID)

    (record,) = cfg.read_instances(prune=False)

    assert record.alive is True


def test_dead_records_are_deleted_as_they_are_read(registry):
    live = write_record(registry, 9977, "Vessel.toe", pid=LIVE_PID)
    dead = write_record(registry, 9979, "Abandoned.toe", pid=DEAD_PID)

    found = cfg.read_instances()

    assert live.exists()
    assert not dead.exists(), "a false claim about a listening bridge was kept"
    # The removal pass is also the only chance to report it, so it is returned.
    assert [(i.port, i.alive) for i in found] == [(9977, True), (9979, False)]


def test_a_corrupt_record_is_skipped_not_fatal(registry):
    (registry / "instances" / "9977.json").write_text("{ truncated")
    write_record(registry, 9978, "Rehearsal.toe", pid=OTHER_PID)

    assert [i.port for i in cfg.read_instances()] == [9978]


def test_the_real_probes_agree_about_this_process_and_a_real_socket(monkeypatch):
    """One smoke test against the real OS, so the fakes above stay honest.

    Both probes are asked for both answers, and on purpose: neither was
    working on Windows, in two different ways. `port_listening` could not
    return False there — `connect_ex` reports WSAECONNREFUSED (10061), which
    the POSIX `errno.ECONNREFUSED` this compared against does not equal — so
    a record could not be pruned and `alive` could not be False; CI run
    34111353871 read that directly. `pid_alive` was answering a different
    question: `signal.CTRL_C_EVENT` is 0, so `os.kill(pid, 0)` on Windows
    sends a console control event instead of asking whether a process
    exists. That run measured it returning True for a live pid; what it would
    have said about a pid that was gone was never read, because nobody had
    asked it that. Both are fixes in `config.py`, so this test runs on
    Windows rather than skipping there — and the dead-pid assertion below is
    the first reading of the replacement.

    The dead pid is a child that has been waited on, which is the only pid a
    test can be sure about: POSIX has reaped it, Windows still holds an exited
    process object for it, and both must read as "not running".
    """
    import socket
    import subprocess
    import sys

    monkeypatch.undo()
    assert cfg.pid_alive(os.getpid()) is True

    finished = subprocess.Popen([sys.executable, "-c", "pass"])
    finished.wait()
    assert cfg.pid_alive(finished.pid) is False

    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        assert cfg.port_listening(port) is True
    # A raw connect_ex to the port that just closed, reported alongside the
    # verdict: CI run 34114021234 read None here on windows-latest, meaning the
    # code it returns is in neither branch, and no run has printed which code
    # that is. Guessing at the number is the confident wrong answer this project
    # refuses, so the assertion carries the measurement.
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.settimeout(0.25)
    try:
        raw = probe.connect_ex(("127.0.0.1", port))
    finally:
        probe.close()
    assert cfg.port_listening(port) is False, (
        "connect_ex on the closed port returned %r (%s); _REFUSED holds %r"
        % (raw, errno.errorcode.get(raw, "no errno name"), sorted(cfg._REFUSED))
    )


# -- selection --------------------------------------------------------------

def test_project_fragment_picks_the_intended_instance(registry):
    two_instances(registry)

    assert cfg.select_instance(project="Rehear").port == 9978
    assert cfg.select_instance(project="vessel").port == 9977
    # A piece of the path works as well as a piece of the name.
    assert cfg.select_instance(project="artist/td/Rehearsal").port == 9978


def test_an_ambiguous_fragment_is_an_error_that_lists_the_candidates(registry):
    write_record(registry, 9977, "Show_A.toe", pid=LIVE_PID)
    write_record(registry, 9978, "Show_B.toe", pid=OTHER_PID)

    with pytest.raises(cfg.InstanceSelectionError) as excinfo:
        cfg.select_instance(project="Show")

    message = str(excinfo.value)
    assert "--port 9977" in message and "--port 9978" in message
    assert "Show_A.toe" in message and "Show_B.toe" in message


def test_a_fragment_matching_nothing_is_an_error(registry):
    two_instances(registry)

    with pytest.raises(cfg.InstanceSelectionError) as excinfo:
        cfg.select_instance(project="Nowhere")

    assert "Vessel.toe" in str(excinfo.value)


def test_a_dead_instance_cannot_be_selected(registry):
    write_record(registry, 9979, "Abandoned.toe", pid=DEAD_PID)

    with pytest.raises(cfg.InstanceSelectionError):
        cfg.select_instance(project="Abandoned")


def test_port_selection_finds_the_record(registry):
    two_instances(registry)

    chosen = cfg.select_instance(port=9978)

    assert chosen.project == "Rehearsal.toe"


def test_an_unregistered_port_is_not_an_error(registry):
    """A bridge too old to register itself still answers on its port."""
    two_instances(registry)

    assert cfg.select_instance(port=9999) is None


# -- what the client does with all this -------------------------------------

def test_the_client_follows_the_project_flag(registry):
    two_instances(registry)

    client = BridgeClient.discover(project="Rehearsal")

    assert client.port == 9978
    assert client.instance.project == "Rehearsal.toe"
    assert client.token == "s3cret", "the token comes from config.json, not the record"


def test_the_client_follows_the_port_flag(registry):
    two_instances(registry)

    assert BridgeClient.discover(port=9978).port == 9978


def test_without_flags_and_without_a_registry_the_session_file_still_rules(registry):
    """The pre-registry path, untouched: session.json first, config second."""
    (registry / "session.json").write_text(
        json.dumps({"port": 9981, "token": "from-session"})
    )

    client = BridgeClient.discover()

    assert client.port == 9981
    assert client.token == "from-session"
    assert client.instance is None
    assert client.ambiguity_warning is None


def test_two_live_instances_and_no_flags_keeps_the_old_target_but_says_so(registry):
    two_instances(registry)
    (registry / "session.json").write_text(json.dumps({"port": 9977, "token": "s3cret"}))

    client = BridgeClient.discover()

    assert client.port == 9977, "existing behaviour must not move"
    assert "2 TouchDesigner instances are running" in client.ambiguity_warning
    assert "--port 9978" in client.ambiguity_warning


# -- the command ------------------------------------------------------------

def test_instances_command_lists_both_and_shows_how_to_target_them(registry, capsys):
    two_instances(registry)

    assert main(["instances"]) == 0

    out = capsys.readouterr().out
    assert "2 running instances" in out
    assert "Vessel.toe" in out and "Rehearsal.toe" in out
    assert "--port 9977" in out and "--port 9978" in out
    assert "2023.11600" in out
    assert "seen 0s ago" in out
    assert "td-atlas --project Rehearsal status" in out


def test_instances_command_reports_and_removes_a_dead_record(registry, capsys):
    write_record(registry, 9977, "Vessel.toe", pid=LIVE_PID)
    dead = write_record(registry, 9979, "Abandoned.toe", pid=DEAD_PID)

    assert main(["instances"]) == 0

    captured = capsys.readouterr()
    assert "1 running instance:" in captured.out
    assert "Abandoned.toe" not in captured.out
    assert (
        "nothing is listening on port 9979, so the record for Abandoned.toe "
        "was removed." in captured.err
    )
    assert not dead.exists()


def test_a_dead_pid_removal_still_names_the_port(registry, capsys, monkeypatch):
    monkeypatch.setattr(cfg, "port_listening", lambda port, timeout=0.25: None)
    write_record(registry, 9977, "Crashed.toe", pid=DEAD_PID)

    assert main(["instances"]) == 0

    assert (
        f"no process {DEAD_PID} is running, so the record for Crashed.toe "
        f"(port 9977) was removed." in capsys.readouterr().err
    )


def test_instances_command_with_an_empty_registry(registry, capsys):
    assert main(["instances"]) == 0
    assert "No running TouchDesigner has registered a bridge." in capsys.readouterr().out


def test_an_ambiguous_project_flag_fails_the_command_instead_of_guessing(registry, capsys):
    write_record(registry, 9977, "Show_A.toe", pid=LIVE_PID)
    write_record(registry, 9978, "Show_B.toe", pid=OTHER_PID)

    assert main(["--project", "Show", "exec", "print(1)"]) == 1

    err = capsys.readouterr().err
    assert "matches more than one running TouchDesigner" in err
    assert "Show_A.toe" in err and "Show_B.toe" in err


# -- the writer inside TouchDesigner ----------------------------------------

class _Par:
    def __init__(self, value):
        self._value = value

    def eval(self):
        return self._value


class _Pars:
    def __init__(self, port):
        self.port = _Par(port)


class _Component:
    path = "/tdatlas"


class _Dat:
    """What a Web Server DAT looks like to the callbacks, minus TouchDesigner."""

    def __init__(self, port=9977):
        self.par = _Pars(port)

    def parent(self):
        return _Component()


@pytest.fixture
def bridge(registry, monkeypatch):
    """The handler wired to fake TouchDesigner globals, in the scratch home."""
    monkeypatch.setattr(handler, "project", _Fakes.project, raising=False)
    monkeypatch.setattr(handler, "app", _Fakes.app, raising=False)
    monkeypatch.setattr(handler, "_registry_last", 0.0)
    monkeypatch.setattr(handler, "AUTH_TOKEN", "")
    monkeypatch.setattr(handler, "_TOKEN_LOADED", False)
    return registry


class _Fakes:
    class project:
        name = "Vessel.toe"
        folder = "/Users/artist/td"

    class app:
        build = "2023.11600"


def _request(method="nope", token="s3cret"):
    request = {"data": json.dumps({"method": method})}
    if token is not None:
        request["X-TD-Atlas-Token"] = token
    return request


def _record_on_disk(home, port=9977):
    return json.loads((home / "instances" / f"{port}.json").read_text())


def test_the_bridge_writes_its_record_when_the_server_starts(bridge, capsys):
    handler.onServerStart(_Dat(9977))

    record = _record_on_disk(bridge)
    assert record["port"] == 9977
    assert record["project"] == "Vessel.toe"
    assert record["projectPath"] == "/Users/artist/td/Vessel.toe"
    assert record["build"] == "2023.11600"
    assert record["pid"] == os.getpid()
    assert record["component"] == "/tdatlas"
    assert record["protocol"] == handler.PROTOCOL_VERSION
    assert "process" not in record, "a name the host cannot reproduce is not identity"
    assert time.time() - record["updated"] < 5


def test_the_record_never_carries_the_token(bridge):
    """The token is a bearer credential in one 0600 file; it stays there."""
    handler.onServerStart(_Dat(9977))
    handler.onHTTPRequest(_Dat(9977), _request(), {})

    files = list((bridge / "instances").iterdir())
    assert files
    for path in files:
        text = path.read_text()
        assert "s3cret" not in text
        assert "token" not in text.lower()


def test_the_record_is_refreshed_by_traffic_but_not_by_every_request(bridge):
    """Every write costs frame time, so requests refresh at most once a window."""
    dat = _Dat(9977)
    handler.onServerStart(dat)
    first = _record_on_disk(bridge)["updated"]

    handler.onHTTPRequest(dat, _request(), {})
    assert _record_on_disk(bridge)["updated"] == first, "wrote twice inside the window"

    # Wind the clock past the interval without waiting for it.
    handler._registry_last -= handler.REGISTRY_INTERVAL + 1
    handler.onHTTPRequest(dat, _request(), {})
    assert _record_on_disk(bridge)["updated"] > first


def test_an_unauthenticated_caller_cannot_drive_writes(bridge):
    (bridge / "config.json").write_text(json.dumps({"token": "s3cret"}))
    dat = _Dat(9977)
    handler._load_token()
    handler._registry_last = -1e6

    response = handler.onHTTPRequest(dat, _request(token=None), {})

    assert response["statusCode"] == 401
    assert not (bridge / "instances" / "9977.json").exists()


def test_stopping_the_server_withdraws_the_record(bridge):
    dat = _Dat(9977)
    handler.onServerStart(dat)

    handler.onServerStop(dat)

    assert not (bridge / "instances" / "9977.json").exists()


def test_rebinding_to_another_port_drops_this_process_own_stale_record(bridge):
    handler.onServerStart(_Dat(9977))
    handler._registry_last = -1e6

    handler.onServerStart(_Dat(9978))

    assert not (bridge / "instances" / "9977.json").exists()
    assert (bridge / "instances" / "9978.json").exists()


def test_another_process_record_survives_a_rebind(bridge):
    write_record(bridge, 9979, "Elsewhere.toe", pid=OTHER_PID)

    handler.onServerStart(_Dat(9977))

    assert (bridge / "instances" / "9979.json").exists()


def test_a_failing_write_is_reported_and_does_not_break_the_request(bridge, capsys):
    """A read-only home must not take the bridge down with it."""
    handler._registry_last = -1e6

    response = handler.onHTTPRequest(None, _request(), {})

    assert response["statusCode"] == 404, "the request still ran"
    assert "could not write the instance record" in capsys.readouterr().out


def test_the_written_record_reads_back_as_a_live_instance(bridge):
    """End to end, minus the socket: what the bridge writes is what the CLI reads."""
    handler.onServerStart(_Dat(9977))

    (found,) = cfg.read_instances(prune=False)
    assert found.port == 9977
    assert found.project == "Vessel.toe"
    assert found.pid == os.getpid()


def test_a_record_written_by_the_bridge_reads_back_as_live(bridge, monkeypatch):
    """The writer and the reader, end to end, against the real operating system.

    This is the test the first version of this file did not have: every other
    case here feeds the reader a dictionary written by the test itself, so a
    reader that disagrees with the *writer* passes them all. It did — the
    bridge recorded the name of its embedded interpreter, the host asked `ps`
    for the name of the application, and every live bridge on the machine was
    declared dead and deleted. Nothing is faked below.
    """
    import socket

    # The real operating system, but keep the fake TouchDesigner globals.
    monkeypatch.setattr(cfg, "pid_alive", REAL_PID_ALIVE)
    monkeypatch.setattr(cfg, "port_listening", REAL_PORT_LISTENING)
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        handler.onServerStart(_Dat(port))

        (found,) = cfg.read_instances()

    assert found.port == port
    assert found.pid == os.getpid()
    assert found.alive is True, f"the writer's own record was rejected: {found.dead_reason}"
    assert found.path.exists(), "a live bridge's record was deleted"


def test_the_record_is_rewritten_when_it_goes_missing(bridge):
    """A host that wrongly deleted the record must not blind the bridge for 30s."""
    dat = _Dat(9977)
    handler.onServerStart(dat)
    (bridge / "instances" / "9977.json").unlink()

    handler.onHTTPRequest(dat, _request(), {})

    assert (bridge / "instances" / "9977.json").exists()


# -- the bootstrap registers without waiting for a callback ------------------

def _bootstrap_function(name):
    """Lift one function out of bootstrap.py, which runs install() on import."""
    import ast

    source = (Path(handler.__file__).parent / "bootstrap.py").read_text()
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            namespace = {"os": os, "_HOME": "/unused"}
            exec(compile(ast.Module([node], []), "bootstrap.py", "exec"), namespace)
            return namespace[name]
    raise AssertionError(f"bootstrap.py has no {name}()")


def test_the_bootstrap_registers_through_the_handler_module(bridge):
    """`install()` must not depend on onServerStart, which does not fire for it.

    Measured in a running TouchDesigner: flipping the Web Server DAT's `active`
    parameter from a script — what the bootstrap does — produced no
    onServerStart callback, and the bridge stayed unregistered until something
    sent it a request.
    """
    class _HandlerDat:
        module = handler

    register = _bootstrap_function("_register")

    record = register(_Dat(9977), _HandlerDat(), 9977)

    assert record["port"] == 9977
    assert (bridge / "instances" / "9977.json").exists()


def test_the_bootstrap_reports_a_registration_it_could_not_make(bridge, capsys):
    class _Broken:
        @property
        def module(self):
            raise RuntimeError("no module for this DAT")

    register = _bootstrap_function("_register")

    assert register(_Dat(9977), _Broken(), 9977) is None
    assert "could not register this instance" in capsys.readouterr().out


def test_install_calls_the_registration():
    """A guard on the call site itself: the wiring above is worthless if dropped."""
    source = (Path(handler.__file__).parent / "bootstrap.py").read_text()
    install = source[source.index("def install():"):]
    assert "_register(server, handler, port)" in install
    assert install.index("server.par.active = True") < install.index("_register(")


# -- the writer and the reader agree about every field ----------------------

# The contract between `handler._instance_record` (inside TouchDesigner) and
# `config.Instance.from_dict` (on the host): the key one writes, the attribute
# the other reads it back as. Asserted through a real record on disk, so a
# rename on either side breaks the test — the writer's keys are compared
# against this map, and every value is checked to arrive intact rather than as
# the reader's empty default.
RECORD_FIELDS = {
    "port": "port",
    "project": "project",
    "projectPath": "project_path",
    "build": "build",
    "pid": "pid",
    "component": "component",
    "protocol": "protocol",
    "updated": "updated",
}


def test_every_field_the_bridge_writes_is_read_back_under_the_same_name(bridge):
    """Nothing here is a dictionary the test wrote: writer to disk to reader.

    The earlier version of this file asserted three fields of eight through
    the real chain and the rest against its own `write_record` fixture, so a
    key renamed on one side alone still passed.
    """
    handler.onServerStart(_Dat(9977))

    raw = _record_on_disk(bridge)
    (found,) = cfg.read_instances(prune=False)

    assert set(raw) == set(RECORD_FIELDS), "the bridge's field set moved"
    for key, attribute in RECORD_FIELDS.items():
        assert raw[key], f"{key} is empty, so a rename could not be detected"
        assert getattr(found, attribute) == raw[key], f"{key} did not survive"


# -- the path taken when there is no registry and no session file -----------

def test_without_a_registry_or_a_session_the_port_comes_from_the_config(registry):
    """The oldest path of all, and the last one still uncovered.

    9983, not the default: a client that ignored config.json and fell back to
    DEFAULT_PORT would pass this test if the config named 9977.
    """
    (registry / "config.json").write_text(
        json.dumps({"port": 9983, "token": "from-config"})
    )
    assert not (registry / "session.json").exists()
    assert list((registry / "instances").iterdir()) == []

    client = BridgeClient.discover()

    assert client.port == 9983
    assert client.token == "from-config"
    assert client.instance is None
    assert client.ambiguity_warning is None


# -- the state directory's permissions do not depend on who arrives first ---

@posix_mode_bits_only
def test_the_bridge_creates_the_state_directory_narrowed(bridge, tmp_path, monkeypatch):
    """TouchDesigner starting before any install must not widen ~/.td-atlas."""
    fresh = tmp_path / "never-installed"
    monkeypatch.setenv("TD_ATLAS_HOME", str(fresh))

    handler.onServerStart(_Dat(9977))

    assert (fresh / "instances" / "9977.json").exists()
    assert stat.S_IMODE(fresh.stat().st_mode) == 0o700


@posix_mode_bits_only
def test_a_state_directory_that_already_exists_is_narrowed_too(bridge, tmp_path, monkeypatch):
    """The chmod is re-applied, because mkdir does nothing to an existing one."""
    loose = tmp_path / "loose"
    loose.mkdir()
    loose.chmod(0o755)
    monkeypatch.setenv("TD_ATLAS_HOME", str(loose))

    handler.onServerStart(_Dat(9977))

    assert stat.S_IMODE(loose.stat().st_mode) == 0o700


@posix_mode_bits_only
def test_the_host_narrows_the_state_directory_as_well(tmp_path, monkeypatch):
    """Both sides do it, so the answer does not depend on arrival order."""
    home = tmp_path / "host-first"
    monkeypatch.setenv("TD_ATLAS_HOME", str(home))

    assert cfg.ensure_home() == home
    assert stat.S_IMODE(home.stat().st_mode) == 0o700

    home.chmod(0o755)
    cfg.save_config({"port": 9977, "token": "s3cret"})
    assert stat.S_IMODE(home.stat().st_mode) == 0o700


# -- the same facts on the MCP surface --------------------------------------
#
# The registry was built because two open projects made every command go to
# one of them in silence. Over MCP the silence was still complete: no listing,
# and the ambiguity warning the CLI prints never reached the agent.

def _server():
    from td_atlas.mcp import server

    return server


def test_the_mcp_tool_lists_both_instances_and_marks_the_one_in_use(registry):
    two_instances(registry)
    (registry / "session.json").write_text(json.dumps({"port": 9977, "token": "s3cret"}))

    text = _server().td_instances()

    assert "2 running instances" in text
    assert "Vessel.toe" in text and "Rehearsal.toe" in text
    assert "port 9977" in text and "port 9978" in text
    assert "2023.11600" in text
    lines = [line for line in text.splitlines() if "talk to this one" in line]
    assert len(lines) == 1 and "9977" in lines[0]


def test_the_mcp_tool_reports_an_empty_registry_without_pretending(registry):
    text = _server().td_instances()

    assert "No running TouchDesigner has registered a bridge" in text


def test_the_mcp_tool_says_a_stale_record_was_removed(registry):
    write_record(registry, 9977, "Vessel.toe", pid=LIVE_PID)
    write_record(registry, 9979, "Abandoned.toe", pid=DEAD_PID)

    text = _server().td_instances()

    assert "Abandoned.toe" in text and "was removed" in text
    assert not (registry / "instances" / "9979.json").exists()


def test_the_ambiguity_warning_reaches_the_agent(registry):
    """The failure the registry exists to prevent, on the MCP surface."""
    two_instances(registry)
    (registry / "session.json").write_text(json.dumps({"port": 9977, "token": "s3cret"}))
    server = _server()

    client = BridgeClient.discover()
    prefix = server._warn(client)

    assert client.ambiguity_warning
    assert prefix.startswith("warning: ")
    assert "2 TouchDesigner instances are running" in prefix
    assert "td_instances" in prefix, "the agent is not told how to look"


def test_both_warnings_can_be_carried_at_once(registry):
    two_instances(registry)
    (registry / "session.json").write_text(json.dumps({"port": 9977, "token": "s3cret"}))
    server = _server()

    client = BridgeClient.discover()
    client.version_warning = "bridge protocol 0 is older than this client's 1."

    prefix = server._warn(client)

    assert prefix.count("warning: ") == 2
    assert "protocol 0" in prefix and "instances are running" in prefix


def test_a_single_instance_raises_no_warning(registry):
    write_record(registry, 9977, "Vessel.toe", pid=LIVE_PID)

    assert _server()._warn(BridgeClient.discover()) == ""
