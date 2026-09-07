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
PANEL_TOP = "panel"
DEFAULT_PORT = 9977



def _home():
    """Where the host staged this bridge. Same rule as handler.py's `_home`.

    Read on every call rather than frozen at exec time, and honouring
    TD_ATLAS_HOME, because the host may point elsewhere: with a stale
    ~/.td-atlas beside a live TD_ATLAS_HOME, a constant here read the old
    handler.py and installed a previous version of the bridge silently — the
    install looked fine and only surfaced later as a protocol mismatch. An
    empty value counts as unset, as it does in handler.py and config.py, and
    the default is joined rather than expanded in one piece for the reason
    handler.py's `_home` records: expanding "~/.td-atlas" leaves the "/" in
    place, which on Windows spells one directory in two separators.
    """
    return os.environ.get("TD_ATLAS_HOME") or os.path.join(
        os.path.expanduser("~"), ".td-atlas"
    )


def _load_config():
    path = os.path.join(_home(), "config.json")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return {}


def _read_handler_source():
    """The handler's text, as the file on disk actually holds it.

    UTF-8 is named rather than left to the locale, in all three `open` calls in
    this file. Without it Windows reads cp1252: CI run 34111353871 measured
    exactly that on `read_text()` elsewhere in the project, where one em dash
    (`e2 80 94`) came back as three characters. `handler.py` is full of them, so
    this read would install a mangled handler — the text is executed in a DAT,
    where the damage lands in comments and would surface later as something
    else. The write below is the same keyword for a different reason: it is safe
    today only because `json.dump` escapes non-ASCII by default, and that is a
    default, not a guarantee this file makes.

    Note that the textport line in this module's own docstring — and the copy
    of it `td-atlas install` prints — still reads this file with the locale
    encoding. Same defect, one step earlier, and not fixed here.
    """
    path = os.path.join(_home(), "handler.py")
    with open(path, "r", encoding="utf-8") as handle:
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
        home = _home()
        os.makedirs(home, exist_ok=True)
        path = os.path.join(home, "session.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(session, handle, indent=2)
        # The token is a bearer credential for a socket on this machine.
        # POSIX only: on Windows this sets the read-only attribute and does
        # not keep other accounts out — see config.py's `ensure_home`.
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

    # Read-only, and it never leaves this COMP: no interaction, no keyboard,
    # no timer. It is repainted by the handler at the end of each request.
    # Its shape comes from the handler rather than from a second copy here:
    # project/release.py lays out the same node for the released .tox from the
    # same constants, and a panel that differs between the two installs would
    # be a difference nobody would think to look for.
    shape = handler.module
    panel = container.op(PANEL_TOP)
    if panel is None:
        panel = container.create(textTOP, PANEL_TOP)
        panel.nodeX, panel.nodeY = 0, 200
        panel.par.text = shape.PANEL_PLACEHOLDER
    for name, value in shape.PANEL_PARS:
        setattr(panel.par, name, value)

    # The save subscription. An Execute DAT with `projectpostsave` on, whose
    # text is a shim that calls the handler beside it — the shape, like the
    # panel's, comes from the handler so that this install and the released
    # .tox cannot drift apart. It is created switched on; whether it writes
    # anything is decided at save time by config.json, not here.
    onsave = container.op(shape.SAVE_DAT)
    if onsave is None:
        onsave = container.create(executeDAT, shape.SAVE_DAT)
        onsave.nodeX, onsave.nodeY = 200, 200
    onsave.text = shape.SAVE_SHIM
    for name, value in shape.SAVE_PARS:
        setattr(onsave.par, name, value)

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
