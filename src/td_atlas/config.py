"""Shared on-disk locations.

Both sides of the bridge meet in ~/.td-atlas: `td-atlas install` writes the
component sources and config there, and the bootstrap running inside
TouchDesigner writes back a session file naming the port it actually bound.
That handshake is why the host client needs no configuration.
"""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

DEFAULT_PORT = 9977


def home() -> Path:
    """The td-atlas state directory, overridable for tests."""
    return Path(os.environ.get("TD_ATLAS_HOME", Path.home() / ".td-atlas"))


def config_path() -> Path:
    return home() / "config.json"


def session_path() -> Path:
    return home() / "session.json"


def bootstrap_path() -> Path:
    return home() / "bootstrap.py"


def db_path() -> Path:
    return home() / "atlas.db"


def load_config() -> dict:
    try:
        return json.loads(config_path().read_text())
    except (OSError, ValueError):
        return {}


def save_config(config: dict) -> None:
    home().mkdir(parents=True, exist_ok=True)
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
