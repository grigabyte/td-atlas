"""The td-atlas RPC handler. This module runs *inside* TouchDesigner.

It is installed as the text of a Text DAT wired to a Web Server DAT's Callbacks
parameter, so it executes in TouchDesigner's embedded Python 3.11 with the `td`
globals (op, ops, root, project, ui, ...) already in scope. It must not import
anything that is not in TouchDesigner's standard library.

Every request is handled on TouchDesigner's main thread during a cook, so a
handler that blocks stalls the entire application. Keep work bounded.
"""

import base64
import io
import json
import os
import time
import traceback
from contextlib import redirect_stderr, redirect_stdout

PROTOCOL_VERSION = 1

# The shared secret, read from ~/.td-atlas/config.json when the server starts —
# see _load_token(). It is not baked into this text: a released .tox is one file
# handed to every machine, and there is no install step to bake anything into.
# Empty disables authentication, and _load_token() says so out loud.
AUTH_TOKEN = ""
_TOKEN_LOADED = False

_MAX_REPR = 4000
_MAX_CHILDREN = 2000


# -- authentication ---------------------------------------------------------

def _home():
    return os.environ.get("TD_ATLAS_HOME") or os.path.expanduser("~/.td-atlas")


def _config_path():
    """The config file the host also owns; see src/td_atlas/config.py."""
    return os.path.join(_home(), "config.json")


def _read_token():
    """Return (token, complaint). Never raises.

    A missing, malformed or unreadable config must not stop the bridge from
    coming up — but it must never leave it quietly open either, so every path
    that yields no token hands back a reason for the caller to print.
    """
    path = _config_path()
    try:
        with open(path, "r") as handle:
            config = json.load(handle)
    except IOError as exc:
        return "", "cannot read %s (%s)" % (path, exc)
    except ValueError as exc:
        return "", "malformed JSON in %s (%s)" % (path, exc)
    except Exception as exc:
        return "", "cannot read %s (%s: %s)" % (path, type(exc).__name__, exc)
    if not isinstance(config, dict):
        return "", "%s does not hold a JSON object" % path
    token = config.get("token")
    if not isinstance(token, str) or not token:
        return "", "no token in %s" % path
    return token, None


def _load_token():
    """Populate AUTH_TOKEN once, announcing an unauthenticated bridge.

    Called from onServerStart, and again defensively on the first request in
    case the handler text was replaced without the server restarting. One file
    read per module lifetime, none per request.
    """
    global AUTH_TOKEN, _TOKEN_LOADED
    _TOKEN_LOADED = True
    AUTH_TOKEN, complaint = _read_token()
    if not AUTH_TOKEN:
        print(
            "[td-atlas] WARNING: no auth token (%s) - the bridge accepts "
            "any caller on this machine" % complaint
        )
    return AUTH_TOKEN


# -- the instance registry --------------------------------------------------

# How often the record may be rewritten from inside a request, in seconds.
#
# Everything here runs on TouchDesigner's main thread during a frame, so the
# write is time taken away from the frame it lands in. Measured on this
# machine — host CPython on macOS/APFS, warm cache, 2000 iterations of this
# exact function, not TouchDesigner's embedded 3.11, which is the same class
# of operation but was not benchmarked:
# median 125 us, mean 132 us, p99 255 us, worst 1.6 ms. At 60 fps a frame is
# 16.7 ms, so one write costs 0.75% of a frame typically and can eat 10% of
# one in the tail — nothing as a one-off, a permanent 0.75% tax plus visible
# jitter if it were done every frame. Hence: not every frame, and
# not on a timer either. The record is written the moment the bridge is raised
# (see below on why that is not left to onServerStart alone), then refreshed at
# most once per REGISTRY_INTERVAL and only when a request has already
# interrupted the frame anyway. An idle bridge writes nothing at all.
#
# The price of that choice is a timestamp that goes stale while nobody is
# talking to the bridge. That is safe because the timestamp is not the
# liveness test: the host decides live-or-dead from the port and the recorded
# pid, and shows the age separately as "last seen".
#
# Three doors lead here, because one of them turned out not to be reliable:
#
#   1. bootstrap.py calls the writer itself, right after it raises the server.
#      This is the door that matters for `td-atlas install` and `td-atlas
#      reload`: measured in a running TouchDesigner, flipping the Web Server
#      DAT's `active` parameter from a script did *not* produce an
#      onServerStart callback, and an idle bridge that never registers is the
#      whole failure this registry exists to prevent.
#   2. onServerStart, for the paths that do fire it — a .tox dropped into a
#      network raises its own server with no script involved. Whether that
#      path calls back has not been measured here; the record is written
#      either way because of door 3.
#   3. the first authenticated request, and any request that finds the record
#      missing (someone cleared ~/.td-atlas by hand). This is the net under
#      the other two, and the only one that cannot register an idle bridge.
REGISTRY_INTERVAL = 30.0

_registry_last = 0.0


def _ensure_home():
    """Create ~/.td-atlas (and the registry directory) with 0700 on the home.

    The host's `td-atlas install` narrows the same directory the same way (see
    config.py's `ensure_home`). Both sides do it because neither is guaranteed
    to be first: a TouchDesigner that started before any install creates the
    directory here, and with only `makedirs` its permissions came from
    whatever umask that TouchDesigner happened to inherit. The chmod is
    re-applied every time rather than passed as a mode, since `makedirs` will
    not touch an existing directory.

    POSIX only. On Windows `os.chmod` touches only the read-only attribute,
    so the 0700 below narrows nothing; what keeps other accounts out there is
    the ACL on the user profile directory, which this project neither sets
    nor has measured.
    """
    home = _home()
    os.makedirs(os.path.join(home, "instances"), exist_ok=True)
    try:
        os.chmod(home, 0o700)
    except Exception:
        # Its own try: failing to narrow the directory must not abort — or be
        # reported as — a failure to write the record.
        pass
    return home


def _instance_path(port):
    return os.path.join(_home(), "instances", "%d.json" % int(port))


def _instance_record(port, component_path):
    """What this bridge claims about itself. Never the token.

    The token lives in one 0600 file; copying it into a directory that exists
    to be read by every tool on the machine would spread a bearer credential
    for no gain — the host reads config.json itself.
    """
    return {
        "port": int(port),
        "project": project.name,
        "projectPath": os.path.join(project.folder, project.name),
        "build": app.build,
        "pid": os.getpid(),
        "component": component_path,
        "protocol": PROTOCOL_VERSION,
        # No process *name* here. Seen from inside, this process is the
        # embedded interpreter ("python3.11"); seen from outside it is the
        # application. The host establishes liveness from the port and the pid,
        # which both sides see the same way — see config.py's `port_listening`.
        "updated": time.time(),
    }


def _component_path(dat):
    try:
        return dat.parent().path
    except Exception:
        return ""


def _write_instance(dat, now=None):
    """Publish this bridge's record. Returns it, or None on failure.

    Written to a sibling temporary file and renamed into place: a host reading
    the directory at the wrong moment must see either the old record or the
    new one, never half of either.
    """
    global _registry_last
    # Both outcomes reset the clock: a failing write (a read-only home, say)
    # must not be retried on every single request for the rest of the session.
    _registry_last = time.monotonic() if now is None else now
    try:
        port = int(dat.par.port.eval())
        path = _instance_path(port)
        _ensure_home()
        record = _instance_record(port, _component_path(dat))
        tmp = path + ".tmp"
        with open(tmp, "w") as handle:
            json.dump(record, handle, indent=2)
        os.replace(tmp, path)
    except Exception as exc:
        print("[td-atlas] could not write the instance record: %s" % exc)
        return None
    return record


def _remove_instance(dat):
    """Withdraw the record on an orderly stop, so nothing outlives the bridge."""
    try:
        os.remove(_instance_path(int(dat.par.port.eval())))
    except Exception:
        pass


def _drop_stale_ports(keep_port):
    """Delete records this same process left on other ports.

    Moving the bridge from one port to another leaves a record whose pid is
    still very much alive — the host would have no way to see it is a ghost.
    Only the writing process can settle this, and only when it rebinds, which
    is rare enough to afford a directory scan outside the request path.
    """
    directory = os.path.join(_home(), "instances")
    mine = os.getpid()
    try:
        names = os.listdir(directory)
    except Exception:
        return
    for name in names:
        if not name.endswith(".json"):
            continue
        path = os.path.join(directory, name)
        try:
            with open(path, "r") as handle:
                record = json.load(handle)
            if record.get("pid") == mine and int(record.get("port")) != int(keep_port):
                os.remove(path)
        except Exception:
            continue


def _refresh_instance(dat):
    """Rewrite the record if it is due, or if it has gone missing.

    The existence check is one `stat` per request (measured at 1.5 us, a
    ten-thousandth of a frame) and buys the case where the file was deleted
    under a running bridge — by a host that misjudged it dead, or by hand.
    Without it the bridge stays invisible for up to REGISTRY_INTERVAL.
    """
    now = time.monotonic()
    if now - _registry_last >= REGISTRY_INTERVAL:
        _write_instance(dat, now)
        return
    try:
        port = int(dat.par.port.eval())
    except Exception:
        return
    if not os.path.exists(_instance_path(port)):
        _write_instance(dat, now)


# -- serialisation ----------------------------------------------------------

def _jsonable(value, depth=0):
    """Coerce a TouchDesigner value into something json.dumps can handle."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (bytes, bytearray)):
        return base64.b64encode(bytes(value)).decode("ascii")
    if depth > 4:
        return _clip(repr(value))
    if isinstance(value, dict):
        return {str(k): _jsonable(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v, depth + 1) for v in value]
    # Operator *types* (td.noiseTOP and friends, as found in `families`) are
    # classes, and their `path`/`OPType` attributes are unbound descriptors —
    # so classes must be handled before the instance check below.
    if isinstance(value, type):
        return value.__name__
    # OP instances serialise to their path so the agent can address them again.
    path = getattr(value, "path", None)
    if isinstance(path, str) and hasattr(value, "OPType"):
        return {"__op__": path, "type": value.OPType}
    try:
        return _clip(str(value))
    except Exception:
        return _clip(repr(value))


def _clip(text):
    text = str(text)
    return text if len(text) <= _MAX_REPR else text[:_MAX_REPR] + "...[clipped]"


def _resolve(path):
    """Look up an operator, raising a clear error rather than returning None."""
    if not path:
        raise ValueError("an operator path is required")
    target = op(path)
    if target is None:
        raise LookupError("no operator at path '%s'" % path)
    return target


def _par_info(par):
    """Everything about one parameter that an agent might need."""
    info = {
        "name": par.name,
        "label": par.label,
        "style": par.style,
        "mode": str(par.mode).rsplit(".", 1)[-1],
        "value": _jsonable(par.eval()),
        "expr": par.expr or None,
        "enabled": bool(par.enable),
        "readOnly": bool(par.readOnly),
        "isDefault": bool(par.isDefault),
    }
    if par.isMenu:
        info["menuNames"] = list(par.menuNames or [])
        info["menuLabels"] = list(par.menuLabels or [])
    if par.isNumber:
        info["min"] = par.normMin
        info["max"] = par.normMax
    return info


def _op_summary(target, include_pars=False):
    """A structured description of a single operator."""
    out = {
        "path": target.path,
        "name": target.name,
        "type": target.OPType,
        "family": target.family,
        "valid": bool(target.valid),
        "inputs": [c.path for c in target.inputs],
        "numChildren": (
            len(target.children) if hasattr(target, "children") else 0
        ),
    }
    try:
        out["errors"] = target.errors(recurse=False) or None
        out["warnings"] = target.warnings(recurse=False) or None
    except Exception:
        out["errors"] = None
        out["warnings"] = None
    if include_pars:
        pars = {}
        for par in target.pars():
            try:
                pars[par.name] = _par_info(par)
            except Exception as exc:
                pars[par.name] = {"name": par.name, "error": str(exc)}
        out["pars"] = pars
    return out


# -- methods ----------------------------------------------------------------

def m_ping(_params):
    return {
        "protocol": PROTOCOL_VERSION,
        # app.version is the branch ('099'); app.build is the actual build.
        "build": app.build,
        "version": app.version,
        "product": app.product,
        "project": project.name,
        "projectFolder": project.folder,
        "fps": me.time.rate,
        "frame": absTime.frame,
    }


def m_exec(params):
    """Run arbitrary Python, capturing output and the value of a final expression.

    The scope is a copy of this module's globals, which TouchDesigner populates
    with the full `td` namespace — op, ops, root, project, ui, app, absTime,
    the operator type objects (baseCOMP, textDAT, ...) and the `families`
    registry. Copying rather than sharing keeps executed code from clobbering
    the handler itself.
    """
    code = params.get("code")
    if not code:
        raise ValueError("exec requires 'code'")
    scope = dict(globals())
    out, err = io.StringIO(), io.StringIO()
    result = None
    with redirect_stdout(out), redirect_stderr(err):
        try:
            # Prefer eval so a trailing expression returns its value; fall back
            # to exec for statements.
            compiled = compile(code, "<td-atlas>", "eval")
            result = eval(compiled, scope)
        except SyntaxError:
            exec(compile(code, "<td-atlas>", "exec"), scope)
            result = scope.get("result")
    return {
        "stdout": out.getvalue(),
        "stderr": err.getvalue(),
        "result": _jsonable(result),
    }


def m_op_info(params):
    target = _resolve(params.get("path"))
    return _op_summary(target, include_pars=params.get("pars", True))


def m_network(params):
    """Walk a component, describing children and how they are wired."""
    target = _resolve(params.get("path") or "/")
    depth = int(params.get("depth", 1))
    include_pars = bool(params.get("pars", False))

    def walk(comp, level):
        if not hasattr(comp, "children"):
            return []
        nodes = []
        for child in list(comp.children)[:_MAX_CHILDREN]:
            entry = _op_summary(child, include_pars=include_pars)
            if level < depth and getattr(child, "children", None):
                entry["children"] = walk(child, level + 1)
            nodes.append(entry)
        return nodes

    return {
        "path": target.path,
        "type": target.OPType,
        "children": walk(target, 1),
    }


def m_op_create(params):
    """Create an operator, optionally setting parameters and wiring an input."""
    parent_comp = _resolve(params.get("parent") or "/")
    op_type = params.get("type")
    if not op_type:
        raise ValueError("op_create requires 'type'")
    created = parent_comp.create(op_type, params.get("name"))

    position = params.get("position")
    if position:
        created.nodeX, created.nodeY = float(position[0]), float(position[1])

    if params.get("pars"):
        _apply_pars(created, params["pars"])

    for wiring in params.get("connect") or []:
        source = _resolve(wiring["from"])
        index = int(wiring.get("index", 0))
        source.outputConnectors[0].connect(created.inputConnectors[index])

    return _op_summary(created, include_pars=False)


def m_op_delete(params):
    paths = params.get("paths") or [params.get("path")]
    removed = []
    for path in paths:
        target = _resolve(path)
        removed.append(target.path)
        target.destroy()
    return {"deleted": removed}


def m_op_connect(params):
    source = _resolve(params.get("from"))
    target = _resolve(params.get("to"))
    index = int(params.get("index", 0))
    source.outputConnectors[0].connect(target.inputConnectors[index])
    return {"from": source.path, "to": target.path, "index": index}


def m_op_disconnect(params):
    target = _resolve(params.get("path"))
    index = int(params.get("index", 0))
    target.inputConnectors[index].disconnect()
    return {"path": target.path, "index": index}


def _apply_pars(target, values):
    """Set parameters, accepting constants, expressions and bindings.

    A plain value sets constant mode; {'expr': ...} sets expression mode;
    {'bind': ...} sets a bind expression; {'pulse': true} pulses.
    """
    applied = {}
    for name, value in values.items():
        par = getattr(target.par, name, None)
        if par is None:
            raise AttributeError(
                "%s (%s) has no parameter '%s'" % (target.path, target.OPType, name)
            )
        if isinstance(value, dict):
            if "expr" in value:
                par.expr = value["expr"]
            elif "bind" in value:
                par.bindExpr = value["bind"]
            elif value.get("pulse"):
                par.pulse()
            else:
                raise ValueError(
                    "parameter '%s' got an object with no expr/bind/pulse" % name
                )
        else:
            par.val = value
        applied[name] = _jsonable(par.eval())
    return applied


def m_par_set(params):
    target = _resolve(params.get("path"))
    return {"path": target.path, "applied": _apply_pars(target, params["pars"])}


def m_par_get(params):
    target = _resolve(params.get("path"))
    names = params.get("names")
    pars = target.pars(*names) if names else target.pars()
    return {
        "path": target.path,
        "pars": {p.name: _par_info(p) for p in pars},
    }


def m_render(params):
    """Return a TOP's image so the agent can see what it built."""
    target = _resolve(params.get("path"))
    if target.family != "TOP":
        raise TypeError(
            "render needs a TOP, but %s is a %s" % (target.path, target.family)
        )
    fmt = params.get("format", ".png")
    if not fmt.startswith("."):
        fmt = "." + fmt

    source = target
    scratch = None
    width, height = params.get("width"), params.get("height")
    try:
        if width or height:
            # Given only one dimension, derive the other from the source so a
            # thumbnail is not stretched.
            src_w = max(1, target.width)
            src_h = max(1, target.height)
            if width and not height:
                height = max(1, int(round(src_h * (float(width) / src_w))))
            elif height and not width:
                width = max(1, int(round(src_w * (float(height) / src_h))))

            # Resize through a scratch Resolution TOP so the original is
            # untouched; agents ask for thumbnails far more often than
            # full-resolution frames.
            scratch = target.parent().create("resolutionTOP", "tdatlas_resize")
            scratch.par.outputresolution = "custom"
            scratch.par.resolutionw = int(width)
            scratch.par.resolutionh = int(height)
            scratch.inputConnectors[0].connect(target)
            source = scratch
        source.cook(force=True)
        data = source.saveByteArray(fmt)
        return {
            "path": target.path,
            "format": fmt,
            "width": source.width,
            "height": source.height,
            "encoding": "base64",
            "data": base64.b64encode(bytes(data)).decode("ascii"),
        }
    finally:
        if scratch is not None:
            scratch.destroy()


_CAPTURE_KEY = "tdatlas_capture"


def m_capture(params):
    """Grab one frame of a TOP into a buffer for later tiling.

    A single request runs inside one cook, so time cannot advance while it
    executes — a strip of frames has to be collected by the host calling this
    repeatedly, letting TouchDesigner run in between. Frames are kept as numpy
    arrays in component storage because that is where numpy lives.
    """
    target = _resolve(params.get("path"))
    if target.family != "TOP":
        raise TypeError("capture needs a TOP, got a %s" % target.family)

    holder = me.parent()
    frames = holder.fetch(_CAPTURE_KEY, None)
    if frames is None or params.get("reset"):
        frames = []
        holder.store(_CAPTURE_KEY, frames)

    target.cook(force=True)
    frames.append(target.numpyArray())
    return {"frames": len(frames), "path": target.path, "frame": absTime.frame}


def m_contact_sheet(params):
    """Tile the captured frames into one image and clear the buffer."""
    import numpy

    holder = me.parent()
    frames = holder.fetch(_CAPTURE_KEY, None) or []
    if not frames:
        raise ValueError("no frames captured; call capture first")

    columns = max(1, int(params.get("columns", 3)))
    width = int(params.get("width", 320))
    rows = (len(frames) + columns - 1) // columns

    # numpyArray() hands back float RGBA indexed [h, w]; downsample by
    # striding rather than interpolating, which is enough for a proof sheet
    # and avoids pulling in a resampler.
    tiles = []
    for frame in frames:
        h, w = frame.shape[0], frame.shape[1]
        step = max(1, w // width)
        tiles.append(frame[::step, ::step, :3])
    th = min(t.shape[0] for t in tiles)
    tw = min(t.shape[1] for t in tiles)
    tiles = [t[:th, :tw] for t in tiles]

    sheet = numpy.zeros((rows * th, columns * tw, 3), dtype=tiles[0].dtype)
    for index, tile in enumerate(tiles):
        r, c = divmod(index, columns)
        sheet[r * th : (r + 1) * th, c * tw : (c + 1) * tw] = tile

    # numpyArray() is bottom-up relative to how images are normally read.
    sheet = numpy.flipud(sheet)
    eight_bit = (numpy.clip(sheet, 0.0, 1.0) * 255).astype(numpy.uint8)

    holder.unstore(_CAPTURE_KEY)
    return {
        "count": len(tiles),
        "columns": columns,
        "rows": rows,
        "width": int(eight_bit.shape[1]),
        "height": int(eight_bit.shape[0]),
        "encoding": "raw-rgb8-base64",
        "data": base64.b64encode(eight_bit.tobytes()).decode("ascii"),
    }


def m_errors(_params):
    """Every operator currently reporting an error or warning."""
    found = []
    for target in root.findChildren(depth=None):
        try:
            errors = target.errors(recurse=False)
            warnings = target.warnings(recurse=False)
        except Exception:
            continue
        if errors or warnings:
            found.append(
                {
                    "path": target.path,
                    "type": target.OPType,
                    "errors": errors or None,
                    "warnings": warnings or None,
                }
            )
    return {"count": len(found), "nodes": found}


def m_batch(params):
    """Run several operations as one undoable, all-or-nothing block.

    TouchDesigner's own undo stack is the rollback mechanism, which means a
    failed batch leaves no partial network behind *and* an artist can undo the
    agent's work with Ctrl+Z like any other edit.
    """
    steps = params.get("ops") or []
    name = params.get("undo_name") or "td-atlas batch"
    results = []
    ui.undo.startBlock(name)
    try:
        for index, step in enumerate(steps):
            method = METHODS.get(step.get("method"))
            if method is None:
                raise ValueError(
                    "step %d: unknown method '%s'" % (index, step.get("method"))
                )
            results.append(method(step.get("params") or {}))
    except Exception:
        ui.undo.endBlock()
        try:
            ui.undo.undo()
        except Exception:
            pass
        raise
    ui.undo.endBlock()
    return {"applied": len(results), "results": results}


def m_undo(_params):
    ui.undo.undo()
    return {"undoStack": list(ui.undo.undoStack)}


def m_redo(_params):
    ui.undo.redo()
    return {"redoStack": list(ui.undo.redoStack)}


def m_save(params):
    """Save the session. project.save() returns only a bool, so the path the
    caller asked for is echoed back to save them guessing."""
    path = params.get("path")
    saved = project.save(path) if path else project.save()
    return {"saved": bool(saved), "path": path, "name": project.name}


def m_op_types(params):
    """Resolve operator paths to their types in a single round trip."""
    out = {}
    for path in params.get("paths") or []:
        target = op(path)
        out[path] = target.OPType if target is not None else None
    return out


def m_save_tox(params):
    target = _resolve(params.get("path"))
    return {"saved": target.save(params.get("file"), createFolders=True)}


# Operators holding a shader the GPU has to compile. Measured on build
# 2025.32460 with a deliberately broken pixel shader on a glslTOP:
# `errors(recurse=False)` was empty, `warnings()` said only "The GLSL Shader
# has compile errors (Use Info DAT to see details)", and the compiler's actual
# text — the DAT path and the line number — was in `compileResult` alone. A
# successful compile also returns non-empty text ("Compiled Successfully"), so
# the presence of text is not a verdict; the host reads it, see bridge/health.py.
#
# glslPOP is listed here too, but its class page (GlslPOP_Class) documents no
# `compileResult` and the live read confirmed the attribute is absent, so the
# read below stays silent rather than inventing a result.
_GLSL_TYPES = ("glslTOP", "glslmultiTOP", "glslMAT", "glslPOP")


def m_health_sample(params):
    """One snapshot of the state a silent failure shows up in.

    Nothing here is an error as far as TouchDesigner is concerned, which is
    exactly the problem: a network that never cooks, an output device switched
    off, or a CPU-bound operator dragging the frame rate all look fine to
    `errors`. Two samples taken a moment apart are enough to tell a live node
    from a dormant one.

    A failed shader compile and a traceback from a script or callback are the
    same kind of blind spot, and neither is visible through `errors()`: they
    are read from `compileResult` and `scriptErrors` respectively.
    """
    root_path = params.get("path") or "/project1"
    target = _resolve(root_path)

    # A traceback raised in a DAT callback, a Replicator callback or an
    # extension is recorded per operator and never reaches errors(): measured
    # on build 2025.32460, an Execute DAT whose onFrameStart raised showed up in
    # scriptErrors while errors() stayed empty. The node path rides inside the
    # message, in brackets at the end, and 'Error:' and 'Warning:' sections are
    # mixed in one string — hence the per-operator attribution below.
    #
    # Known gap, same measurement: a Script CHOP whose onCook raises during a
    # cook that Python asked for (op.cook(force=True)) hands the traceback back
    # to the caller and writes nothing here, while totalCooks still advances.
    # Script-operator onCook is therefore *not* covered.
    #
    # One recursive call answers whether the subtree holds any at all (0.02 ms
    # on a 32-node project, and near-independent of node count), so a healthy
    # project pays one call and only a broken one pays a walk.
    script_errors_root = ""
    try:
        script_errors_root = target.scriptErrors(recurse=True) or ""
    except Exception:
        pass
    walk_scripts = bool(script_errors_root)

    script_errors = {}
    nodes = []
    for child in target.findChildren(depth=None):
        try:
            entry = {
                "path": child.path,
                "type": child.OPType,
                "family": child.family,
                "cooks": child.totalCooks,
                "cookTime": round(child.cookTime, 3),
                "bypass": bool(child.bypass),
                "errors": child.errors(recurse=False) or None,
                "warnings": child.warnings(recurse=False) or None,
            }
            # Output-ish operators that quietly do nothing when switched off.
            for flag in ("active", "record", "play"):
                par = getattr(child.par, flag, None)
                if par is not None:
                    entry[flag] = bool(par.eval())
            # Read inside this walk rather than in a pass of its own: a
            # separate getattr pass over the same 32 nodes cost 1.90 ms, and
            # 0.84 ms with this type filter, against a getattr on the handful
            # of GLSL nodes here. (Both measured on a 32-node project; a
            # network of thousands of nodes was not available to time.)
            if child.OPType in _GLSL_TYPES:
                result = getattr(child, "compileResult", None)
                if result:
                    entry["compileResult"] = _clip(result)
            if walk_scripts:
                # Its own try: a read that fails on some operator class must
                # cost the attribution, not the node — everything already in
                # `entry` (errors, warnings, cook state) is a surface that
                # worked before this one was added.
                try:
                    message = child.scriptErrors(recurse=False)
                    if message:
                        script_errors[child.path] = _clip(message)
                except Exception:
                    pass
            nodes.append(entry)
        except Exception:
            continue

    # The root's own extensions and callbacks belong to nobody in the walk
    # above, because findChildren excludes the subtree root.
    if walk_scripts:
        try:
            message = target.scriptErrors(recurse=False)
            if message:
                script_errors[target.path] = _clip(message)
        except Exception:
            pass

    licence = {}
    try:
        licence = {
            "type": str(getattr(licenses, "type", "")),
            "commercial": bool(getattr(licenses, "commercial", False)),
        }
    except Exception:
        pass

    return {
        "frame": absTime.frame,
        "fpsTarget": me.time.rate,
        "playing": bool(me.time.play),
        "realTime": bool(project.realTime),
        "rootCookTime": round(root.cookTime, 3),
        "license": licence,
        "product": app.product,
        "nodes": nodes,
        # Attributed per node where the per-operator read agrees with the
        # recursive one, and kept raw as well: if attribution comes back empty
        # while the recursive read did not, the errors are still reported —
        # unattributed beats unmentioned.
        "scriptErrors": script_errors,
        "scriptErrorsRaw": _clip(script_errors_root),
    }


def m_perf(_params):
    return {
        "fps": me.time.rate,
        "frame": absTime.frame,
        "cookTime": root.cookTime,
        "childrenCookTime": root.childrenCookTime,
        "cookRate": me.time.rate,
    }


METHODS = {
    "ping": m_ping,
    "exec": m_exec,
    "op_info": m_op_info,
    "network": m_network,
    "op_create": m_op_create,
    "op_delete": m_op_delete,
    "op_connect": m_op_connect,
    "op_disconnect": m_op_disconnect,
    "par_get": m_par_get,
    "par_set": m_par_set,
    "render": m_render,
    "errors": m_errors,
    "capture": m_capture,
    "contact_sheet": m_contact_sheet,
    "batch": m_batch,
    "undo": m_undo,
    "redo": m_redo,
    "save": m_save,
    "save_tox": m_save_tox,
    "op_types": m_op_types,
    "perf": m_perf,
    "health_sample": m_health_sample,
}


# -- Web Server DAT callbacks -----------------------------------------------

def _header(request, name):
    """Case-insensitive header lookup.

    HTTP header names are case-insensitive and clients normalise them
    differently — urllib, for one, sends 'X-td-atlas-token' no matter how the
    header was spelled — so an exact-key lookup would reject valid requests.
    """
    target = name.lower()
    for key, value in request.items():
        if isinstance(key, str) and key.lower() == target:
            return value
    return None


def _reply(response, payload, status=200):
    body = json.dumps(payload)
    response["statusCode"] = status
    response["statusReason"] = "OK" if status == 200 else "Error"
    response["content-type"] = "application/json"
    response["data"] = body
    return response


def onHTTPRequest(dat, request, response):
    try:
        if not _TOKEN_LOADED:
            _load_token()
        if AUTH_TOKEN:
            supplied = _header(request, "X-TD-Atlas-Token") or ""
            if supplied != AUTH_TOKEN:
                return _reply(
                    response,
                    {"ok": False, "error": {"type": "Unauthorized",
                                            "message": "bad or missing token"}},
                    401,
                )
        # Only authenticated callers keep the record warm: an unauthenticated
        # stranger must not be able to drive writes to disk.
        _refresh_instance(dat)

        raw = request.get("data") or "{}"
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8")
        payload = json.loads(raw) if raw.strip() else {}

        name = payload.get("method", "ping")
        method = METHODS.get(name)
        if method is None:
            return _reply(
                response,
                {
                    "ok": False,
                    "error": {
                        "type": "UnknownMethod",
                        "message": "no method '%s'" % name,
                        "available": sorted(METHODS),
                    },
                },
                404,
            )

        result = method(payload.get("params") or {})
        return _reply(response, {"ok": True, "result": result})

    except Exception as exc:
        return _reply(
            response,
            {
                "ok": False,
                "error": {
                    "type": type(exc).__name__,
                    "message": str(exc),
                    "traceback": traceback.format_exc(limit=12),
                },
            },
            500,
        )


def onWebSocketOpen(dat, client, uri):
    return


def onWebSocketClose(dat, client):
    return


def onWebSocketReceiveText(dat, client, data):
    return


def onWebSocketReceiveBinary(dat, client, data):
    return


def onServerStart(dat):
    """Called by the Web Server DAT when it starts listening — when it is.

    Not the only registration path: see the note above REGISTRY_INTERVAL.
    """
    _load_token()
    record = _write_instance(dat)
    if record:
        _drop_stale_ports(record["port"])
    print("[td-atlas] bridge listening on port %s" % dat.par.port.eval())


def onServerStop(dat):
    _remove_instance(dat)
    print("[td-atlas] bridge stopped")
