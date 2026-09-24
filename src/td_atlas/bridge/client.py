"""Host-side client for the in-TouchDesigner bridge.

Deliberately dependency-free: the CLI and the probe both use it, and neither
should need the MCP stack installed.
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from .. import journal
from ..component import handler as _handler
from ..config import (
    DEFAULT_PORT,
    Instance,
    load_config,
    load_session,
    read_instances,
    select_instance,
)

# Oldest bridge protocol this client still talks to. Held equal to the
# expected version by the owner's decision of 2026-09-06, renewed at 6 on
# 2026-09-07: no version back is supported, so an older bridge is refused
# outright rather than accepted with a warning. Accepting it only postponed
# the failure to the first method whose presence differs, which came back as
# `UnknownMethod` from a call the agent had no reason to think would fail — a
# refusal at connect time names the fix once instead. Written as a literal so
# the two bounds stay separate knobs; lowering it re-opens the warning band
# below.
MIN_PROTOCOL_VERSION = 7

# The version this client was built against. This is *imported*, not copied,
# from `component/handler.py` — the single owner of PROTOCOL_VERSION — so the
# two sides cannot drift apart silently by someone editing one file and
# forgetting the other. Importing it is safe on the host: handler.py's
# module-level code is plain stdlib (base64/io/json/traceback), and every
# reference to TouchDesigner's injected globals (`op`, `app`, `me`, ...) lives
# inside function bodies that only run when the bridge itself calls them, not
# at import time.
EXPECTED_PROTOCOL_VERSION = _handler.PROTOCOL_VERSION

_UPGRADE_BRIDGE = (
    "Update the bridge: re-run the 'td-atlas install' bootstrap line in "
    "TouchDesigner's textport, or run 'td-atlas reload'."
)
_UPGRADE_HOST = "Update the td-atlas package on this host to match."

# -- reading the process on a timeout ----------------------------------------
#
# `ps` answers in one spawn on macOS, where TouchDesigner runs and where the
# asleep case was met. Windows has no equally cheap reading (`tasklist` gives
# memory, not CPU; a CPU figure needs two WMI samples a second apart), so
# there the old message stands, as the owner asked, rather than a guess.
_CAN_INSPECT_PROCESS = sys.platform != "win32"

# Thresholds on `ps`'s %CPU. Estimates, not measurements of this decision:
# the one asleep reading on record is 0.0% in state S (ПРОЧТИ-БОЛИ-АГЕНТА-2,
# 2026-09-24), and one awake, answering TouchDesigner on this machine read
# 115.3% and, minutes later, 52.7% the same day. A script holding the main
# thread keeps one core busy, so it reads near 100% or more. Between the two
# bounds nothing is concluded.
_IDLE_CPU = 5.0
_BUSY_CPU = 50.0


def _ps(args: list[str]) -> str:
    """`ps` with these arguments, its stdout, or "" on any failure.

    A seam for the tests as much as a helper: they replace it so no process
    is spawned. Bounded at two seconds, since it runs after a timeout the
    caller has already waited out.
    """
    try:
        done = subprocess.run(
            ["ps", *args], capture_output=True, text=True, timeout=2.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout if done.returncode == 0 else ""


def _touchdesigner_processes(pid: int = 0) -> list[tuple[int, float, str]]:
    """(pid, %CPU, state) for the bridge's own process, or every TouchDesigner.

    The registered pid is preferred because it is the bridge's; without one,
    processes are matched by the executable's name, and more than one match is
    returned as such for the caller to refuse to pick from.
    """
    # The name is checked on the registered pid too: a registry record can
    # outlive its process, and a recycled pid would otherwise be read as
    # TouchDesigner's.
    if pid:
        listing = _ps(["-o", "pid=,pcpu=,stat=,comm=", "-p", str(pid)])
    else:
        listing = _ps(["-axo", "pid=,pcpu=,stat=,comm="])
    found: list[tuple[int, float, str]] = []
    for line in listing.splitlines():
        fields = line.split(None, 3)
        if len(fields) < 4:
            continue
        name = fields[3].strip().rsplit("/", 1)[-1]
        if not name.lower().startswith("touchdesigner"):
            continue
        try:
            found.append((int(fields[0]), float(fields[1]), fields[2]))
        except ValueError:
            continue
    return found


class BridgeError(RuntimeError):
    """A call reached TouchDesigner but the handler reported a failure."""

    def __init__(self, error: dict, method: str):
        self.type = error.get("type", "Error")
        self.message = error.get("message", "")
        self.traceback = error.get("traceback")
        self.method = method
        super().__init__(f"{self.type} in {method}: {self.message}")


class BridgeUnavailable(RuntimeError):
    """TouchDesigner is not reachable, or its bridge cannot be used as-is.

    A protocol mismatch outside the supported range is raised as this too:
    from the caller's point of view a bridge speaking an incompatible
    protocol is exactly as unusable as one that never answered.

    `reason` separates the five ways this happens, because they need five
    different repairs and the message text is the wrong thing to recognise
    them by — a reworded message would silently change which advice a caller
    gives. The values are the keys of the recovery table in
    `mcp/hints.py`; an unspecified one falls back to the same entry as
    'never answered', which is what an unexplained unavailability looks like.
    """

    def __init__(self, message: str, reason: str = "bridge_unreachable"):
        super().__init__(message)
        self.reason = reason


@dataclass
class BridgeClient:
    """JSON-RPC over HTTP to a TouchDesigner running the td-atlas bridge."""

    port: int = DEFAULT_PORT
    token: str = ""
    host: str = "127.0.0.1"
    timeout: float = 30.0
    # `td-atlas reload` sets this to False, and nothing else does. Its whole
    # job is to replace the handler a version check would refuse to talk to,
    # so enforcing the check there makes the one command that repairs an
    # out-of-date bridge the one command that cannot run against it. It is
    # safe because reload rides on `exec`, which has been in the bridge's
    # method table since protocol 1 (verified against the first commit of
    # component/handler.py), so the request shape is one any bridge
    # understands. Mismatches are still reported, as `version_warning`
    # instead of a refusal.
    enforce_protocol: bool = True
    # Set once, on the first call this instance makes (see
    # `_ensure_protocol_checked`); never re-checked on later calls.
    version_warning: str | None = field(default=None, init=False, repr=False)
    _protocol_checked: bool = field(default=False, init=False, repr=False)
    # The registry record this client was aimed at, when one picked it.
    instance: Instance | None = field(default=None, init=False, repr=False)
    # Set when several bridges are running and none was named; the CLI prints
    # it so a two-project session cannot go to the wrong one in silence.
    ambiguity_warning: str | None = field(default=None, init=False, repr=False)

    @classmethod
    def discover(
        cls,
        timeout: float = 30.0,
        port: int | None = None,
        project: str | None = None,
    ) -> BridgeClient:
        """Aim a client at one bridge.

        With `--port`/`--project` (or neither, and exactly one bridge running)
        the registry decides. With neither flag the old path is kept exactly:
        session.json first, config second — so a single-project setup, and any
        bridge too old to register itself, behave as they always did. Raises
        `InstanceSelectionError` when the flags name no bridge or more than one.
        """
        config = load_config()
        instances = read_instances()
        chosen = select_instance(port=port, project=project, instances=instances)

        if chosen is not None:
            client = cls(
                port=chosen.port,
                # Never from the record: the registry holds no credentials.
                token=config.get("token") or "",
                timeout=timeout,
            )
            client.instance = chosen
            return client

        if port is not None:
            # A port nobody registered — dial it anyway; a pre-registry bridge
            # is still a bridge.
            return cls(port=port, token=config.get("token") or "", timeout=timeout)

        session = load_session() or {}
        client = cls(
            port=int(session.get("port") or config.get("port") or DEFAULT_PORT),
            token=session.get("token") or config.get("token") or "",
            timeout=timeout,
        )
        live = [i for i in instances if i.alive]
        for record in live:
            if record.port == client.port:
                client.instance = record
        if len(live) > 1:
            picked = client.instance.label if client.instance else f"port {client.port}"
            others = ", ".join(
                f"{i.label} (--port {i.port})" for i in live if i.port != client.port
            )
            client.ambiguity_warning = (
                f"{len(live)} TouchDesigner instances are running; using "
                f"{picked}. Target another with --project or --port: {others}. "
                f"Run 'td-atlas instances' to see them all."
            )
        return client

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/rpc"

    # -- transport ---------------------------------------------------------

    def _ensure_protocol_checked(self, timeout: float | None = None) -> None:
        """Verify the bridge's protocol version, once, before real traffic.

        Runs on the first call this instance makes, for any method — the
        guard flag is set *before* the recursive `ping` call below, so that
        call re-enters here, finds itself already checked, and proceeds
        straight to the transport instead of looping. If the ping itself
        fails (TouchDesigner unreachable), the flag is put back to unchecked:
        a transport failure is not a version check, and this instance may
        well be asked again later once TouchDesigner is up — that later call
        must still get its one real check, not silently skip it forever. The
        failure itself is left for the real call to raise, so the caller
        sees the ordinary connectivity error rather than a confusing one
        from this side check.
        """
        if self._protocol_checked:
            return
        self._protocol_checked = True
        try:
            info = self.call("ping", timeout=timeout)
        except (BridgeUnavailable, BridgeError):
            self._protocol_checked = False
            return
        self._evaluate_protocol(info.get("protocol"))

    def _evaluate_protocol(self, version: Any) -> None:
        # `version` comes straight off the wire: a corrupted or outdated
        # handler could send anything JSON allows in this field, not just an
        # int — a string, a float, a list. Treat anything that isn't a plain
        # number the same as a missing version rather than let `<` raise
        # TypeError. bool is an int subclass but isn't a protocol number, so
        # it's excluded explicitly.
        if isinstance(version, bool) or not isinstance(version, (int, float)):
            version = None
        if version is None or version < MIN_PROTOCOL_VERSION:
            reported = "no protocol version" if version is None else f"protocol {version}"
            self._refuse(
                f"The running bridge reports {reported}, below the minimum "
                f"{MIN_PROTOCOL_VERSION} this client supports. {_UPGRADE_BRIDGE}"
            )
            return
        if version > EXPECTED_PROTOCOL_VERSION:
            self._refuse(
                f"The running bridge speaks protocol {version}, newer than "
                f"the {EXPECTED_PROTOCOL_VERSION} this client expects. "
                f"{_UPGRADE_HOST}"
            )
            return
        # The warn-don't-refuse band between the two bounds. Empty while
        # MIN == EXPECTED, and kept because the bounds are separate knobs:
        # the day a bridge one version back is genuinely usable, lowering
        # MIN is the whole change.
        if version < EXPECTED_PROTOCOL_VERSION:
            self.version_warning = (
                f"bridge protocol {version} is older than this client's "
                f"{EXPECTED_PROTOCOL_VERSION}. {_UPGRADE_BRIDGE}"
            )

    def _refuse(self, message: str) -> None:
        """Refuse a bridge on protocol grounds — unless the caller is reload.

        Both directions are lifted, not just the too-old one: a bridge newer
        than this host is replaced by the same command, and refusing to run it
        would leave the only repair unreachable from either side.
        """
        if self.enforce_protocol:
            raise BridgeUnavailable(message, reason="bridge_protocol")
        self.version_warning = message

    def call(
        self, method: str, timeout: float | None = None, **params: Any
    ) -> Any:
        """Invoke a bridge method, raising on transport or handler failure.

        Every call passes through here, which is why the journal is written
        here and not in each of the tools: one place to keep honest, and the
        duration it records is the one the caller actually waited, transport
        included. See `td_atlas/journal.py` for why the record lives on the
        host rather than inside TouchDesigner.
        """
        started = time.perf_counter()
        try:
            result = self._call(method, timeout=timeout, **params)
        except BridgeError as exc:
            self._journal(method, params, started, exc,
                          error_type=exc.type, message=exc.message)
            raise
        except BridgeUnavailable as exc:
            self._journal(method, params, started, exc,
                          error_type="BridgeUnavailable",
                          reason=exc.reason, message=str(exc))
            raise
        self._journal(method, params, started, None, result=result)
        return result

    def _journal(
        self,
        method: str,
        params: dict,
        started: float,
        exc: BaseException | None,
        error_type: str = "",
        reason: str = "",
        message: str = "",
        result: Any = None,
    ) -> None:
        # The reply goes along because it is the only source of an old value
        # the journal may record: `flags_set` reads each flag before writing
        # it and says so. The journal takes what it names and nothing else.
        journal.record(
            method,
            result=result,
            ok=exc is None,
            seconds=time.perf_counter() - started,
            params=params,
            port=self.port,
            error_type=error_type,
            error_reason=reason,
            error_message=message,
            # Belt as well as braces: the token this client holds is scrubbed
            # even if the config on disk has since been rewritten.
            token=self.token,
        )

    def _call(
        self, method: str, timeout: float | None = None, **params: Any
    ) -> Any:
        self._ensure_protocol_checked(timeout=timeout or self.timeout)
        body = json.dumps({"method": method, "params": params}).encode()
        request = urllib.request.Request(
            self.url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        if self.token:
            request.add_header("X-TD-Atlas-Token", self.token)

        try:
            with urllib.request.urlopen(
                request, timeout=timeout or self.timeout
            ) as response:
                payload = json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            # The handler reports its own errors with a non-200 status and a
            # JSON body; anything else is a genuine transport problem.
            try:
                payload = json.loads(exc.read().decode())
            except Exception:
                raise BridgeUnavailable(
                    f"HTTP {exc.code} from {self.url}: {exc.reason}",
                    reason="bridge_http",
                ) from exc
        except urllib.error.URLError as exc:
            raise BridgeUnavailable(
                f"Cannot reach TouchDesigner at {self.url} ({exc.reason}). "
                "Is TouchDesigner running with the td-atlas bridge installed? "
                "Run 'td-atlas install' for the one-line bootstrap.",
                reason="bridge_unreachable",
            ) from exc
        except TimeoutError as exc:
            message, reason = self._diagnose_timeout(timeout or self.timeout)
            raise BridgeUnavailable(message, reason=reason) from exc

        if not payload.get("ok"):
            raise BridgeError(payload.get("error") or {}, method)
        return payload.get("result")

    def _diagnose_timeout(self, waited: float) -> tuple[str, str]:
        """The timeout's message and reason, after one look at the process.

        A timeout used to say, every time, that a long script was blocking the
        main thread, and to retry in smaller pieces. On 2026-09-24 no script
        was running: the process sat at 0% CPU in state S, put to sleep by
        macOS overnight, and the advice sent the agent the wrong way. So the
        process is read once, here and only here, and what the reading can
        support is all that is said. Where it cannot be read, the old message
        stands unchanged.
        """
        head = f"TouchDesigner did not respond within {waited}s."
        old = f"{head} A long-running script blocks TouchDesigner's main thread."
        if not _CAN_INSPECT_PROCESS:
            return old, "bridge_timeout"
        pid = self.instance.pid if self.instance is not None else 0
        found = _touchdesigner_processes(pid)
        if not found:
            where = f" (pid {pid} is gone)" if pid else ""
            return (
                f"{head} Found no TouchDesigner process on this host{where}, "
                f"so nothing can be said about why it did not answer.",
                "bridge_timeout",
            )
        if len(found) > 1:
            listed = ", ".join(
                f"pid {p} at {cpu:.1f}% CPU, state {stat}" for p, cpu, stat in found
            )
            return (
                f"{head} Several TouchDesigner processes are running ({listed}) "
                f"and none is known to be this bridge's, so which one is "
                f"stuck cannot be told.",
                "bridge_timeout",
            )
        (p, cpu, stat), = found
        seen = f"pid {p} at {cpu:.1f}% CPU, state {stat}"
        if cpu < _IDLE_CPU:
            return (
                f"{head} TouchDesigner ({seen}) is doing almost nothing, so no "
                f"script is holding it: it looks asleep (macOS App Nap or "
                f"display sleep), minimised, or waiting on a modal dialog. "
                f"Bring it to the front and close any open dialog.",
                "bridge_asleep",
            )
        if cpu >= _BUSY_CPU:
            return (
                f"{head} TouchDesigner ({seen}) is busy: a long-running script "
                f"or a heavy cook holds its main thread. Wait for it.",
                "bridge_timeout",
            )
        return (
            f"{head} TouchDesigner ({seen}) is neither clearly working nor "
            f"clearly idle, and this reading cannot tell a stuck script from "
            f"a stalled window.",
            "bridge_timeout",
        )

    # -- convenience -------------------------------------------------------

    def ping(self) -> dict:
        return self.call("ping")

    def exec(self, code: str, timeout: float | None = None) -> dict:
        return self.call("exec", code=code, timeout=timeout)

    def op_info(self, path: str, pars: bool = True) -> dict:
        return self.call("op_info", path=path, pars=pars)

    def network(self, path: str = "/", depth: int = 1, pars: bool = False):
        return self.call("network", path=path, depth=depth, pars=pars)

    def render(
        self,
        path: str,
        fmt: str = ".png",
        width: int | None = None,
        height: int | None = None,
    ) -> tuple[bytes, dict]:
        """Return a TOP's pixels plus metadata, decoded from base64."""
        result = self.call(
            "render", path=path, format=fmt, width=width, height=height
        )
        return base64.b64decode(result.pop("data")), result

    def errors(self, path: str = "/project1") -> dict:
        # Default matches the handler's, and both are the project rather than
        # `/`: a breadth-first walk from the root spends its node budget on
        # TouchDesigner's own /ui and /sys before it reaches the project.
        return self.call("errors", path=path)

    def batch(
        self,
        ops: list[dict],
        undo_name: str = "td-atlas batch",
        owner: str = "",
    ) -> dict:
        # `owner` names the scope claim this batch writes under, and the bridge
        # carries it into every step. Without it an agent that claimed an area
        # is refused by its own claim on the very tool meant for multi-step
        # edits.
        return self.call("batch", ops=ops, undo_name=undo_name, owner=owner)
