"""Build the td-atlas bridge inside a running TouchDesigner.

Run this from TouchDesigner's textport or a Text DAT:

    exec(open('/Users/you/.td-atlas/bootstrap.py').read())

`td-atlas install` copies this file, handler.py and config.json into
~/.td-atlas, so the line above stays the same across upgrades.

Building the network from a script rather than shipping a .tox keeps the bridge
readable and diffable, lets it be re-derived from source at any time, and makes
re-running this an in-place upgrade: existing nodes are reused and only the
handler text is replaced.
"""

import json
import os

COMPONENT_NAME = "tdatlas"
HANDLER_DAT = "handler"
SERVER_DAT = "bridge"
DEFAULT_PORT = 9977

_HOME = os.path.expanduser("~/.td-atlas")


def _load_config():
    path = os.path.join(_HOME, "config.json")
    try:
        with open(path, "r") as handle:
            return json.load(handle)
    except Exception:
        return {}


def _read_handler_source():
    path = os.path.join(_HOME, "handler.py")
    with open(path, "r") as handle:
        return handle.read()


def _write_session(port, token, component_path):
    """Record where the bridge is, so the host client needs no configuration."""
    session = {
        "port": port,
        "token": token,
        "component": component_path,
        "build": app.build,
        "project": project.name,
        "projectFolder": project.folder,
        "pid": os.getpid(),
    }
    try:
        os.makedirs(_HOME, exist_ok=True)
        path = os.path.join(_HOME, "session.json")
        with open(path, "w") as handle:
            json.dump(session, handle, indent=2)
        # The token is a bearer credential for a socket on this machine.
        os.chmod(path, 0o600)
    except Exception as exc:
        print("[td-atlas] could not write session file: %s" % exc)
    return session


def install():
    config = _load_config()
    port = int(config.get("port", DEFAULT_PORT))
    token = config.get("token", "")

    container = op("/" + COMPONENT_NAME)
    if container is None:
        container = root.create(baseCOMP, COMPONENT_NAME)
        container.nodeX, container.nodeY = -400, 400
    container.par.externaltox = ""

    handler = container.op(HANDLER_DAT)
    if handler is None:
        handler = container.create(textDAT, HANDLER_DAT)
        handler.nodeX, handler.nodeY = 0, 0

    source = _read_handler_source()
    if token:
        # Bake the shared secret into the module rather than reading it from
        # disk on every request.
        source = source.replace(
            'AUTH_TOKEN = ""', 'AUTH_TOKEN = %r' % token, 1
        )
    handler.text = source

    server = container.op(SERVER_DAT)
    if server is None:
        server = container.create(webserverDAT, SERVER_DAT)
        server.nodeX, server.nodeY = 200, 0

    server.par.callbacks = handler
    server.par.port = port
    server.par.active = False
    server.par.active = True

    session = _write_session(port, token, container.path)
    print(
        "[td-atlas] bridge ready at %s on port %d (auth: %s)"
        % (container.path, port, "token" if token else "DISABLED")
    )
    print("[td-atlas] TouchDesigner %s, project '%s'" % (app.build, project.name))
    return session


install()
