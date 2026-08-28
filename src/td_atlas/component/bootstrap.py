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


def _register(server, handler, port):
    """Publish this bridge's registry record, now, without waiting for a callback.

    The handler owns the record's shape, and the Text DAT holding it can be
    imported as a module — so this calls the same writer the running bridge
    uses rather than keeping a second copy of the format here.

    Why it is called at all: measured in a running TouchDesigner, flipping
    `active` off and on from a script (exactly what this function's caller does
    above) produced no onServerStart callback, so a bridge installed from the
    textport stayed out of `~/.td-atlas/instances` until something sent it a
    request. An instance nobody has talked to yet is precisely the one the
    artist needs to find, so registration cannot depend on traffic.
    """
    try:
        module = handler.module
        record = module._write_instance(server)
        if record is None:
            return None
        module._drop_stale_ports(port)
        # Ask the writer where it put the file rather than rebuilding the path
        # here: the two would disagree the moment TD_ATLAS_HOME is set.
        where = module._instance_path(port)
    except Exception as exc:
        print("[td-atlas] could not register this instance: %s" % exc)
        return None
    print("[td-atlas] registered as %s" % where)
    return record


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

    # Installed verbatim: the handler reads the token from config.json itself,
    # so the text here is byte for byte the text a released .tox carries.
    handler.text = _read_handler_source()

    server = container.op(SERVER_DAT)
    if server is None:
        server = container.create(webserverDAT, SERVER_DAT)
        server.nodeX, server.nodeY = 200, 0

    server.par.callbacks = handler
    server.par.port = port
    server.par.active = False
    server.par.active = True

    _register(server, handler, port)
    session = _write_session(port, token, container.path)
    print(
        "[td-atlas] bridge ready at %s on port %d (auth: %s)"
        % (container.path, port, "token" if token else "DISABLED")
    )
    print("[td-atlas] TouchDesigner %s, project '%s'" % (app.build, project.name))
    return session


install()
