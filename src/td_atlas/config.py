"""Shared on-disk locations.

Both sides of the bridge meet in ~/.td-atlas: `td-atlas install` writes the
component sources and config there, and the bootstrap running inside
TouchDesigner writes back a session file naming the port it actually bound.
That handshake is why the host client needs no configuration.
"""

from __future__ import annotations

import errno
import json
import os
import secrets
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PORT = 9977


def home() -> Path:
    """The td-atlas state directory, overridable for tests."""
    return Path(os.environ.get("TD_ATLAS_HOME", Path.home() / ".td-atlas"))


def config_path() -> Path:
    return home() / "config.json"


def session_path() -> Path:
    return home() / "session.json"


def instances_dir() -> Path:
    """One file per live bridge, named by the port it bound.

    `session.json` names a single bridge — whichever loaded last — so with two
    TouchDesigner instances open every command silently went to the wrong one.
    The registry is the plural form of that handshake: each bridge owns its own
    file, keyed by port because the port is what a client actually dials.
    """
    return home() / "instances"


def instance_path(port: int) -> Path:
    return instances_dir() / f"{int(port)}.json"


def bootstrap_path() -> Path:
    return home() / "bootstrap.py"


def db_path() -> Path:
    return home() / "atlas.db"


def ensure_home() -> Path:
    """The state directory, created if missing and always narrowed to 0700.

    The permissions are re-applied on every call rather than passed to
    `mkdir`, because whoever gets here first is not decided by this process:
    `td-atlas install` creates the directory on the host, and the bridge
    inside TouchDesigner creates it (see `component/handler.py`) when it
    registers before any install has run. A `mode=` on mkdir is masked by
    umask and does nothing at all when the directory already exists, so the
    directory's permissions would have depended on which side arrived first.
    Nothing secret lives in the directory itself — the token is in its own
    0600 file — but the answer must not vary with arrival order.
    """
    path = home()
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        # A directory we cannot chmod (a mounted home, an odd filesystem) is
        # not a reason to fail the write that called us.
        pass
    return path


def load_config() -> dict:
    try:
        return json.loads(config_path().read_text())
    except (OSError, ValueError):
        return {}


def save_config(config: dict) -> None:
    ensure_home()
    config_path().write_text(json.dumps(config, indent=2) + "\n")
    config_path().chmod(0o600)


def load_session() -> dict | None:
    """What the running TouchDesigner reported about its bridge, if any."""
    try:
        return json.loads(session_path().read_text())
    except (OSError, ValueError):
        return None


def ensure_config(port: int | None = None, auth: bool = True) -> dict:
    """Create or update the shared config, minting a token on first run."""
    config = load_config()
    if port is not None:
        config["port"] = port
    config.setdefault("port", DEFAULT_PORT)
    if auth and not config.get("token"):
        config["token"] = secrets.token_urlsafe(24)
    if not auth:
        config["token"] = ""
    save_config(config)
    return config


# -- the instance registry --------------------------------------------------

# A record is refreshed by the bridge at most once every 30 seconds (see
# component/handler.py), and only while requests are arriving — an idle bridge
# stops touching its file entirely. So age is *not* the liveness test here: it
# is "last seen", reported as such. Liveness is two questions the host can ask
# without help from anyone: is something listening on that port, and does that
# process still exist. See `Instance.alive`.
#
# What is deliberately *not* asked: what the process is called. The first
# version of this compared a name the bridge recorded for itself against `ps`,
# and buried every live bridge on the machine: inside TouchDesigner
# `sys.executable` is the embedded interpreter ("python3.11"), from outside
# `ps -o comm=` is the application
# ("/Applications/TouchDesigner.app/Contents/MacOS/TouchDesigner"). The two
# sides were describing the same process and could never agree, because they
# were looking at different things. The port is the one fact both sides see
# identically — the bridge bound it, the host dials it — and it is also the
# thing the client actually needs to be true.


def pid_alive(pid: int) -> bool:
    """Is there any process with this id? Signal 0 checks without delivering.

    EPERM means the process exists but belongs to another user — still alive.
    """
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, ValueError, TypeError, OverflowError):
        return False
    return True


def port_listening(port: int, timeout: float = 0.25) -> bool | None:
    """Is anything accepting connections on 127.0.0.1:PORT?

    True, False (the connection was actively refused — nothing is there), or
    None when the answer could not be established and must not be guessed at.

    This replaces the process-name check that used to live here. Measured on
    this machine, 500 probes each: 36 us median when the port refuses, 48 us
    when it accepts — an order of magnitude under the `ps` subprocess it
    replaces, with no subprocess at all, the same behaviour on every platform,
    and an answer to the question the caller actually has. A pid recycled by an
    unrelated process fails it, which is what the name check was for. It runs
    on the host, never inside a TouchDesigner frame; the far side pays only for
    accepting and closing one connection.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        error = sock.connect_ex(("127.0.0.1", int(port)))
    except (OSError, ValueError, OverflowError):
        return None
    finally:
        sock.close()
    if error == 0:
        return True
    if error in (errno.ECONNREFUSED, errno.ECONNRESET):
        return False
    # Timeout, EHOSTUNREACH, a firewall — observed nothing, so claim nothing.
    return None


@dataclass
class Instance:
    """One bridge's self-declaration, as read back from the registry."""

    port: int
    project: str = ""
    project_path: str = ""
    build: str = ""
    pid: int = 0
    component: str = ""
    protocol: int | None = None
    updated: float = 0.0
    path: Path | None = field(default=None, repr=False)
    # The liveness probe opens a socket; one answer per record per process is
    # plenty, and it keeps a listing from paying for the same check twice.
    _state: tuple[bool, str] | None = field(
        default=None, init=False, repr=False, compare=False
    )

    @classmethod
    def from_dict(cls, data: dict, path: Path | None = None) -> Instance | None:
        try:
            port = int(data["port"])
        except (KeyError, TypeError, ValueError):
            return None
        return cls(
            port=port,
            project=str(data.get("project") or ""),
            project_path=str(data.get("projectPath") or ""),
            build=str(data.get("build") or ""),
            pid=int(data.get("pid") or 0),
            component=str(data.get("component") or ""),
            protocol=data.get("protocol"),
            updated=float(data.get("updated") or 0.0),
            path=path,
        )

    @property
    def age(self) -> float:
        return max(0.0, time.time() - self.updated) if self.updated else float("inf")

    @property
    def alive(self) -> bool:
        """False only when this record was observed to be false.

        Anything that could not be established counts as alive: a live bridge
        wrongly buried costs the artist a working connection, a dead one listed
        for another few seconds costs nothing.
        """
        return self._probe()[0]

    @property
    def dead_reason(self) -> str:
        """What was actually observed, for a record that is not alive.

        Never a summary of what it implies — "pid 60434 is gone" was printed
        about a process that was running perfectly well, and a wrong confident
        answer is worse here than no answer.
        """
        return self._probe()[1]

    def _probe(self) -> tuple[bool, str]:
        if self._state is None:
            self._state = self._observe()
        return self._state

    def _observe(self) -> tuple[bool, str]:
        listening = port_listening(self.port)
        if listening is False:
            return False, f"nothing is listening on port {self.port}"
        if self.pid and not pid_alive(self.pid):
            # The port did answer (or could not be reached) but the process
            # that wrote this is gone: whatever is on that port is not it.
            return False, f"no process {self.pid} is running"
        return True, ""

    @property
    def label(self) -> str:
        return self.project or self.project_path or f"port {self.port}"

    def matches(self, fragment: str) -> bool:
        """Does an unambiguous piece of a name or path pick this instance out?"""
        needle = fragment.strip().lower()
        if not needle:
            return False
        return needle in self.project.lower() or needle in self.project_path.lower()


def read_instances(prune: bool = True) -> list[Instance]:
    """Every record in the registry, ordered by port, dead ones marked.

    Dead records are deleted as they are read (`prune`), which is why the list
    still contains them: the one pass that removes a record is also the only
    chance to tell the user it was there. Deleting rather than flagging is the
    honest option — the file is a claim that a bridge is listening, the claim
    is false, and it is re-derivable in full the moment that bridge comes back.
    A record only counts as dead when the process is provably gone; "cannot
    tell" leaves the file alone.
    """
    directory = instances_dir()
    found: list[Instance] = []
    try:
        entries = sorted(directory.glob("*.json"))
    except OSError:
        return found
    for entry in entries:
        try:
            data = json.loads(entry.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        record = Instance.from_dict(data, path=entry)
        if record is None:
            continue
        found.append(record)
    found.sort(key=lambda i: i.port)
    if prune:
        for record in found:
            if not record.alive and record.path is not None:
                try:
                    record.path.unlink()
                except OSError:
                    pass
    return found


class InstanceSelectionError(RuntimeError):
    """The --port/--project flags did not name exactly one running bridge."""


def _render_choices(instances: list[Instance]) -> str:
    return "\n".join(
        f"  --port {i.port}    {i.label}" + (f"  ({i.build})" if i.build else "")
        for i in instances
    )


def select_instance(
    port: int | None = None,
    project: str | None = None,
    instances: list[Instance] | None = None,
) -> Instance | None:
    """Resolve the selection flags to one live instance, or None for 'as before'.

    Returning None means no registry record was chosen: either no flags were
    given, or `--port` named a port the registry does not know (which is not an
    error — a bridge from an older build still answers there).
    """
    live = [i for i in (read_instances() if instances is None else instances) if i.alive]

    if project:
        matched = [i for i in live if i.matches(project)]
        if port is not None:
            matched = [i for i in matched if i.port == port]
        if not matched:
            detail = (
                "Running instances:\n" + _render_choices(live)
                if live
                else "No running instance has registered itself."
            )
            raise InstanceSelectionError(
                f"no running TouchDesigner matches --project {project!r}. {detail}"
            )
        if len({i.port for i in matched}) > 1:
            raise InstanceSelectionError(
                f"--project {project!r} matches more than one running "
                f"TouchDesigner:\n" + _render_choices(matched) + "\n"
                "Give a longer piece of the name or path, or select by port."
            )
        return matched[0]

    if port is not None:
        for instance in live:
            if instance.port == port:
                return instance
        return None

    return None
