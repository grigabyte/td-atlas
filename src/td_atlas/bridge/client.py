"""Host-side client for the in-TouchDesigner bridge.

Deliberately dependency-free: the CLI and the probe both use it, and neither
should need the MCP stack installed.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from ..config import DEFAULT_PORT, load_config, load_session


class BridgeError(RuntimeError):
    """A call reached TouchDesigner but the handler reported a failure."""

    def __init__(self, error: dict, method: str):
        self.type = error.get("type", "Error")
        self.message = error.get("message", "")
        self.traceback = error.get("traceback")
        self.method = method
        super().__init__(f"{self.type} in {method}: {self.message}")


class BridgeUnavailable(RuntimeError):
    """TouchDesigner is not reachable at all."""


@dataclass
class BridgeClient:
    """JSON-RPC over HTTP to a TouchDesigner running the td-atlas bridge."""

    port: int = DEFAULT_PORT
    token: str = ""
    host: str = "127.0.0.1"
    timeout: float = 30.0

    @classmethod
    def discover(cls, timeout: float = 30.0) -> BridgeClient:
        """Build a client from the session file, falling back to config."""
        session = load_session() or {}
        config = load_config()
        return cls(
            port=int(session.get("port") or config.get("port") or DEFAULT_PORT),
            token=session.get("token") or config.get("token") or "",
            timeout=timeout,
        )

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/rpc"

    # -- transport ---------------------------------------------------------

    def call(
        self, method: str, timeout: float | None = None, **params: Any
    ) -> Any:
        """Invoke a bridge method, raising on transport or handler failure."""
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
                    f"HTTP {exc.code} from {self.url}: {exc.reason}"
                ) from exc
        except urllib.error.URLError as exc:
            raise BridgeUnavailable(
                f"Cannot reach TouchDesigner at {self.url} ({exc.reason}). "
                "Is TouchDesigner running with the td-atlas bridge installed? "
                "Run 'td-atlas install' for the one-line bootstrap."
            ) from exc
        except TimeoutError as exc:
            raise BridgeUnavailable(
                f"TouchDesigner did not respond within "
                f"{timeout or self.timeout}s. A long-running script blocks "
                f"TouchDesigner's main thread."
            ) from exc

        if not payload.get("ok"):
            raise BridgeError(payload.get("error") or {}, method)
        return payload.get("result")

    def available(self) -> bool:
        try:
            self.call("ping", timeout=3.0)
            return True
        except (BridgeUnavailable, BridgeError):
            return False

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

    def errors(self) -> dict:
        return self.call("errors")

    def batch(self, ops: list[dict], undo_name: str = "td-atlas batch") -> dict:
        return self.call("batch", ops=ops, undo_name=undo_name)
