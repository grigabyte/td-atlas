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
import traceback
from contextlib import redirect_stderr, redirect_stdout

PROTOCOL_VERSION = 1

# Populated by bootstrap.py; empty disables authentication.
AUTH_TOKEN = ""

_MAX_REPR = 4000
_MAX_CHILDREN = 2000


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
    "batch": m_batch,
    "undo": m_undo,
    "redo": m_redo,
    "save": m_save,
    "save_tox": m_save_tox,
    "op_types": m_op_types,
    "perf": m_perf,
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
        if AUTH_TOKEN:
            supplied = _header(request, "X-TD-Atlas-Token") or ""
            if supplied != AUTH_TOKEN:
                return _reply(
                    response,
                    {"ok": False, "error": {"type": "Unauthorized",
                                            "message": "bad or missing token"}},
                    401,
                )

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
    print("[td-atlas] bridge listening on port %s" % dat.par.port.eval())


def onServerStop(dat):
    print("[td-atlas] bridge stopped")
