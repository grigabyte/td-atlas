"""MCP server exposing the atom index and the live bridge.

Two layers of tools. The index tools answer offline and cost no round trip to
TouchDesigner — an agent should consult them before it builds anything. The
bridge tools act on a running instance, and validate against the index first,
so a mistyped parameter comes back as "did you mean" rather than as a
TouchDesigner traceback.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP, Image

from .. import config as cfg
from ..atoms.store import AtomStore
from ..atoms.validate import validate_params
from ..bridge.client import BridgeClient, BridgeError, BridgeUnavailable
from .hints import IndexMissing, failure, guarded, hint

mcp = FastMCP(
    "td-atlas",
    instructions=(
        "Control TouchDesigner. Search the index for the right operator and "
        "its exact parameter names before building; the index is offline and "
        "free to query. Use td_build for multi-step edits so they land as one "
        "undoable block, and td_render to see what you made."
    ),
)

_store: AtomStore | None = None


def store() -> AtomStore:
    global _store
    if _store is None:
        _store = AtomStore(cfg.db_path())
        if not _store.exists():
            raise IndexMissing(
                "No atom index yet. Run 'td-atlas build' (and 'td-atlas probe' "
                "with TouchDesigner open) to create it."
            )
    return _store


def bridge() -> BridgeClient:
    # Deliberately not cached (unlike store() above): discover() re-reads the
    # session file every time, which is how a tool call notices TouchDesigner
    # having restarted on a different port. A cached client would also cache
    # its protocol check forever — exactly the silent staleness this
    # contract exists to catch, since the process this runs in is long-lived
    # and an artist can reopen a project with an older bridge at any time.
    # The "once per client, not per call" cache lives on BridgeClient itself
    # (bridge/client.py), scoped to the one instance each call gets here.
    return BridgeClient.discover()


def _warn(client: BridgeClient) -> str:
    """The 'warning: ...' lines to prefix onto a tool's text result, or ''.

    Plain text, not part of any structured field, so it cannot break a
    caller's parsing of the rest of the response.

    Two warnings can be raised by the same call and both belong here. The
    ambiguity one is the reason the instance registry exists: with two
    TouchDesigners open, every tool below goes to whichever bridge the
    session file names, and without this line the agent would have no way to
    learn that its edits landed in the other project.
    """
    lines = []
    if client.ambiguity_warning:
        lines.append(
            f"{client.ambiguity_warning} From here, td_instances lists them; "
            f"selecting one is a host-side flag."
        )
    if client.version_warning:
        lines.append(client.version_warning)
    return "".join(f"warning: {line}\n" for line in lines)


def _fmt_param(row: dict[str, Any]) -> str:
    bits = [row["style"] or row["par_type"] or "?"]
    if row.get("default") is not None:
        bits.append(f"default={row['default']!r}")
    if row.get("menu_names"):
        bits.append("options=" + json.dumps(row["menu_names"]))
    elif row.get("is_number"):
        lo, hi = row.get("norm_min"), row.get("norm_max")
        if lo is not None and hi is not None:
            bits.append(f"slider={lo:g}..{hi:g}")
    if row.get("read_only"):
        bits.append("READ-ONLY")
    line = f"  {row['name']} — {row['label'] or ''} [{' '.join(bits)}]"
    if row.get("summary"):
        line += f"\n      {row['summary'][:300]}"
    return line


# -- index tools ------------------------------------------------------------

@mcp.tool()
@guarded
def td_search_operators(query: str, family: str = "", limit: int = 12) -> str:
    """Find TouchDesigner operators by what they do.

    Searches names, labels, summaries and full documentation. Use this first,
    in plain language ("blur an image", "read a MIDI device", "instance
    geometry"). `family` optionally narrows to TOP, CHOP, SOP, DAT, MAT, COMP
    or POP.
    """
    rows = store().search_ops(query, family=family, limit=limit)
    if not rows:
        return f"No operators match {query!r}.\n{hint('no_match')}"
    lines = []
    for row in rows:
        summary = (row["summary"] or "").split(". ")[0]
        lines.append(f"{row['type']} ({row['family']}) — {row['label']}: {summary}")
    return "\n".join(lines)


@mcp.tool()
@guarded
def td_operator_schema(
    op_type: str, page: str = "", include_hidden: bool = False
) -> str:
    """Every parameter of an operator: exact names, defaults, menu options, ranges.

    Read this before creating or configuring an operator — it gives the names
    TouchDesigner actually accepts. Note that TouchDesigner's own documentation
    describes parameter *groups* (such as 't' for Translate) while the settable
    parameters are the members ('tx', 'ty', 'tz'); this returns the members.
    `page` filters to one parameter page.
    """
    db = store()
    row = db.conn.execute(
        "SELECT * FROM ops WHERE type = ?", (op_type,)
    ).fetchone()
    if row is None:
        close = db.conn.execute(
            "SELECT type FROM ops WHERE type LIKE ? LIMIT 8",
            (f"%{op_type.rstrip('0123456789')}%",),
        ).fetchall()
        suggestion = (
            " Did you mean: " + ", ".join(r["type"] for r in close) if close else ""
        )
        return f"No operator type '{op_type}'.{suggestion}\n{hint('unknown_op_type')}"

    out = [f"{row['type']} ({row['family']}) — {row['label']}"]
    if row["summary"]:
        out.append(row["summary"])
    if row["probed"]:
        out.append(
            f"inputs {row['min_inputs']}–{row['max_inputs']}, "
            f"outputs {row['num_outputs']}"
        )
    else:
        out.append(
            "(not probed: no defaults, ranges or menu options — run "
            "'td-atlas probe')"
        )
    if row["snippet_path"]:
        out.append(f"example network: {row['snippet_path']}")

    pars = [
        p
        for p in db.parameters(op_type)
        if p["settable"] and (include_hidden or not p["hidden"])
    ]
    if page:
        pars = [p for p in pars if (p["page"] or "").lower() == page.lower()]

    current = object()
    for par in pars:
        if par["page"] != current:
            current = par["page"]
            out.append(f"\n[{current or 'ungrouped'}]")
        out.append(_fmt_param(par))
    return "\n".join(out)


@mcp.tool()
@guarded
def td_search_parameters(query: str, limit: int = 20) -> str:
    """Find which operators have a parameter matching a description.

    Useful when you know the effect you want but not which operator provides
    it — "feedback amount", "sample rate", "instancing".
    """
    rows = store().conn.execute(
        "SELECT op_type, name, label, summary FROM params_fts "
        "WHERE params_fts MATCH ? "
        "ORDER BY bm25(params_fts, 1.0, 8.0, 10.0, 4.0) LIMIT ?",
        (store().match(query), limit),
    ).fetchall()
    if not rows:
        return f"No parameters match {query!r}.\n{hint('no_match')}"
    return "\n".join(
        f"{r['op_type']}.{r['name']} — {r['label']}: "
        f"{(r['summary'] or '')[:160]}"
        for r in rows
    )


@mcp.tool()
@guarded
def td_python_api(name: str, query: str = "") -> str:
    """Python members and methods available on a class, with inherited ones.

    `name` is an operator type ('noiseTOP') or a class ('TOP', 'OP', 'Par',
    'UI'). `query` filters the member list. Signatures and return types come
    from the reference shipped with this TouchDesigner build.
    """
    db = store()
    class_name = name
    row = db.conn.execute(
        "SELECT name, inherits, doc FROM py_classes WHERE name = ? "
        "OR op_type = ? LIMIT 1",
        (name, name),
    ).fetchone()
    chain = [class_name]
    if row is not None:
        class_name = row["name"]
        chain = [class_name] + json.loads(row["inherits"] or "[]")

    placeholders = ", ".join("?" * len(chain))
    sql = (
        f"SELECT class_name, kind, name, signature, returns, read_only, doc "
        f"FROM py_members WHERE class_name IN ({placeholders})"
    )
    params: list[Any] = list(chain)
    if query:
        sql += " AND (name LIKE ? OR doc LIKE ?)"
        params += [f"%{query}%", f"%{query}%"]
    sql += " ORDER BY kind, name"
    rows = db.conn.execute(sql, params).fetchall()
    if not rows:
        return f"Nothing found for '{name}'.\n{hint('no_match')}"

    out = [f"{class_name} (inherits: {', '.join(chain[1:]) or 'none'})"]
    if row is not None and row["doc"]:
        out.append(row["doc"][:400])
    for member in rows:
        marker = " [read-only]" if member["read_only"] else ""
        returns = f" -> {member['returns']}" if member["returns"] else ""
        out.append(
            f"\n{member['signature']}{returns}{marker}  "
            f"(from {member['class_name']})"
        )
        if member["doc"]:
            out.append(f"    {member['doc'][:300]}")
    return "\n".join(out)


@mcp.tool()
@guarded
def td_docs(query: str, page: str = "", limit: int = 5) -> str:
    """Search or read TouchDesigner's documentation, mirrored offline.

    Pass `page` to read one article in full (for example 'Write_a_GLSL_Material'
    or 'Noise_TOP'); otherwise `query` searches all 2000-odd pages and returns
    matching excerpts. Covers concepts and guides, not just operators.
    """
    db = store()
    if page:
        row = db.conn.execute(
            "SELECT page, title, text FROM articles WHERE page = ?", (page,)
        ).fetchone()
        if row is None:
            return f"No article '{page}'.\n{hint('no_match')}"
        return f"# {row['title']}\n\n{row['text'][:20000]}"

    rows = db.conn.execute(
        "SELECT page, title, snippet(articles_fts, 2, '**', '**', ' … ', 40) s "
        "FROM articles_fts WHERE articles_fts MATCH ? "
        "ORDER BY bm25(articles_fts, 4.0, 10.0, 1.0) LIMIT ?",
        (store().match(query), limit),
    ).fetchall()
    if not rows:
        return f"No documentation matches {query!r}.\n{hint('no_match')}"
    return "\n\n".join(
        f"## {r['title']}  (page: {r['page']})\n{r['s']}" for r in rows
    )


@mcp.tool()
@guarded
def td_expression_help(query: str, limit: int = 10) -> str:
    """Look up TouchDesigner expression and command syntax."""
    rows = store().conn.execute(
        "SELECT kind, name, signature, text FROM expressions "
        "WHERE name LIKE ? OR text LIKE ? LIMIT ?",
        (f"%{query}%", f"%{query}%", limit),
    ).fetchall()
    if not rows:
        return f"No expression or command matches {query!r}.\n{hint('no_match')}"
    return "\n\n".join(
        f"[{r['kind']}] {r['signature']}\n{(r['text'] or '')[:500]}" for r in rows
    )


# -- bridge tools -----------------------------------------------------------

@mcp.tool()
@guarded
def td_status() -> str:
    """Whether TouchDesigner is reachable, and what project it has open."""
    db_note = "index: missing"
    try:
        stats = store().stats()
        db_note = (
            f"index: {stats['ops']} operators, {stats['params']} parameters, "
            f"{stats['ops_probed']} probed (build "
            f"{store().get_meta('td_version', '?')})"
        )
    except RuntimeError as exc:
        db_note = failure(exc, head=f"index: {exc}")

    client = bridge()
    try:
        info = client.ping()
    except (BridgeUnavailable, BridgeError) as exc:
        return f"{db_note}\nbridge: unavailable — {exc}"
    return (
        f"{_warn(client)}{db_note}\nbridge: connected to {info['product']} "
        f"{info['build']}, project '{info['project']}' in "
        f"{info['projectFolder']}, {info['fps']} fps, frame {info['frame']}"
    )


@mcp.tool()
@guarded
def td_instances() -> str:
    """Which TouchDesigner instances are running, and which one these tools reach.

    Reach for this whenever the artist may have more than one project open —
    and always before believing that an edit went where you meant. Every
    other bridge tool here dials a single bridge chosen on this host (the
    session file, or a `--port`/`--project` flag given to the td-atlas CLI);
    with two TouchDesigners running, the one it picks may not be the one the
    conversation is about, and nothing in a successful result would say so.
    This lists all of them — project, port, build, pid and when each was last
    seen — and marks the one the other tools are talking to. Aiming at a
    different one is not possible from here (the CLI's `--port`/`--project`
    have no MCP equivalent yet): name the port to the user and let them
    decide, rather than assuming the edit landed where they meant.
    """
    records = cfg.read_instances()
    live = [record for record in records if record.alive]
    removed = [record for record in records if not record.alive]

    session = cfg.load_session() or {}
    default_port = int(
        session.get("port") or cfg.load_config().get("port") or cfg.DEFAULT_PORT
    )
    try:
        default_port = BridgeClient.discover().port
    except (cfg.InstanceSelectionError, OSError, ValueError):
        # Falls back to the session/config port worked out above: failing to
        # resolve the default is not a reason to withhold the listing.
        pass

    lines = []
    if not live:
        lines.append(
            "No running TouchDesigner has registered a bridge. Either none is "
            "open, or the one that is predates the instance registry — the "
            "other tools will still reach it on port "
            f"{default_port} if it is there."
        )
    else:
        word = "instance" if len(live) == 1 else "instances"
        lines.append(f"{len(live)} running {word}:")
        for record in live:
            mark = "  <- these tools talk to this one" if record.port == default_port else ""
            lines.append(
                f"  port {record.port}  {record.label}  build "
                f"{record.build or '?'}  pid {record.pid}  protocol "
                f"{record.protocol}  last seen {int(record.age)}s ago{mark}"
            )
            lines.append(f"      {record.project_path or '(unsaved project)'}")
    for record in removed:
        lines.append(
            f"({record.dead_reason}, so the stale record for {record.label} "
            f"was removed)"
        )
    return "\n".join(lines)


@mcp.tool()
@guarded
def td_doctor() -> str:
    """Check the whole chain — install, index, probe pass, bridge, this server.

    Run this when something is wrong and it is not obvious which link broke,
    or before trusting a long build session. The trap it exists for is the
    one that raises nothing: an index built from a different TouchDesigner
    build still answers every question, with defaults and menu options that
    describe the other build, so an agent configures parameters that may not
    exist and the only symptom is a network that quietly does not work. It
    also separates 'not running' (a state) from 'registered but silent' and
    'answering but refusing the token', which need different repairs. Each
    line names the command that fixes it.
    """
    import argparse

    from ..cli import doctor_checks, render_checks

    args = argparse.Namespace(install_path=None, db=None, port=None, project=None)
    try:
        checks = doctor_checks(args)
    except Exception as exc:  # never an opaque ToolError; see AGENTS.md
        return (
            f"error: could not run the checks ({type(exc).__name__}: {exc})\n"
            f"{hint('doctor_failed')}"
        )
    report = render_checks(checks)
    broken = [check.link for check in checks if check.broken]
    if broken:
        return f"{report}\n\nbroken: {', '.join(broken)}"
    return f"{report}\n\nevery link checked out."


@mcp.tool()
@guarded
def td_network(path: str = "/project1", depth: int = 1) -> str:
    """List the operators inside a component and how they are wired."""
    client = bridge()
    try:
        result = client.network(path=path, depth=depth)
    except (BridgeUnavailable, BridgeError) as exc:
        return failure(exc)

    lines = [f"{result['path']} ({result['type']})"]

    def walk(nodes: list[dict], indent: int) -> None:
        for node in nodes:
            wiring = (
                "  <- " + ", ".join(node["inputs"]) if node["inputs"] else ""
            )
            flag = ""
            if node.get("errors"):
                flag = f"  ERROR: {node['errors']}"
            elif node.get("warnings"):
                flag = f"  warning: {node['warnings']}"
            lines.append(
                f"{' ' * indent}{node['name']} ({node['type']}){wiring}{flag}"
            )
            if node.get("children"):
                walk(node["children"], indent + 2)

    walk(result["children"], 2)
    return _warn(client) + "\n".join(lines)


@mcp.tool()
@guarded
def td_op_info(path: str) -> str:
    """Inspect one operator in the running project: type, wiring, live parameter values."""
    client = bridge()
    try:
        info = client.op_info(path)
    except (BridgeUnavailable, BridgeError) as exc:
        return failure(exc)

    lines = [
        f"{info['path']} ({info['type']}, {info['family']})",
        f"inputs: {info['inputs'] or 'none'}",
    ]
    if info.get("errors"):
        lines.append(f"ERRORS: {info['errors']}")
    if info.get("warnings"):
        lines.append(f"warnings: {info['warnings']}")
    for name, par in (info.get("pars") or {}).items():
        if par.get("error"):
            continue
        mode = par.get("mode", "")
        value = par.get("expr") if mode == "EXPRESSION" else par.get("value")
        marker = "" if par.get("isDefault") else "  *"
        lines.append(f"  {name} = {value!r} ({mode.lower()}){marker}")
    return _warn(client) + "\n".join(lines)


@mcp.tool()
@guarded
def td_build(
    operations: list[dict], undo_name: str = "agent edit", owner: str = ""
) -> str:
    """Apply several edits to the project as one atomic, undoable block.

    Prefer this over separate calls: if any step fails the whole batch is rolled
    back, so the project never ends up half-modified, and a successful batch is
    a single Ctrl+Z for the person using TouchDesigner.

    Each operation is {"method": ..., "params": {...}} where method is one of
    op_create, op_delete, op_connect, op_disconnect, par_set.

    op_create takes parent, type, name, optional pars {name: value}, optional
    position [x, y], and optional connect [{"from": path, "index": 0}].
    Values may be a constant, {"expr": "..."} for an expression, {"bind": "..."}
    or {"pulse": true}. Parameter names are validated against the index first.

    Pass the same `owner` you claimed the area with — it carries into every
    step, and without it your own claim refuses the batch.
    """
    db = None
    try:
        db = store()
    except RuntimeError:
        pass  # validation is a convenience; proceed without an index

    if db is not None:
        # par_set targets an existing node, so its type has to be looked up.
        # One call resolves every such path at once, which is worth it: a bad
        # name caught here costs nothing, while one caught by TouchDesigner
        # costs the whole batch.
        unknown = [
            (step.get("params") or {}).get("path")
            for step in operations
            if step.get("method") == "par_set"
            and (step.get("params") or {}).get("pars")
            and not (step.get("params") or {}).get("type")
        ]
        resolved: dict[str, str | None] = {}
        if [p for p in unknown if p]:
            try:
                resolved = bridge().call(
                    "op_types", paths=[p for p in unknown if p]
                )
            except (BridgeUnavailable, BridgeError):
                resolved = {}

        problems: list[str] = []
        for i, step in enumerate(operations):
            params = step.get("params") or {}
            pars = params.get("pars")
            if not pars:
                continue
            op_type = params.get("type") or resolved.get(params.get("path", ""))
            if not op_type:
                continue
            check = validate_params(db, op_type, pars)
            if not check.ok:
                problems.append(f"step {i} ({op_type}):\n" + check.render())
        if problems:
            return (
                "Refusing to apply — these would fail in TouchDesigner:\n\n"
                + "\n\n".join(problems)
                + f"\n\n{hint('params_refused')}"
            )

    client = bridge()
    try:
        result = client.batch(operations, undo_name=undo_name, owner=owner)
    except BridgeError as exc:
        return failure(
            exc,
            head=(
                f"batch failed and was rolled back — {exc.type}: "
                f"{exc.message}\n{exc.traceback or ''}"
            ),
        )
    except BridgeUnavailable as exc:
        return failure(exc)

    lines = [f"applied {result['applied']} operation(s) as '{undo_name}'"]
    for item in result["results"]:
        if isinstance(item, dict) and "path" in item:
            lines.append(f"  {item['path']} ({item.get('type', '')})")
    return _warn(client) + "\n".join(lines)


@mcp.tool()
@guarded
def td_set_params(
    path: str, pars: dict, op_type: str = "", owner: str = ""
) -> str:
    """Set parameters on an existing operator, checked against the index first.

    Values may be a constant, {"expr": "..."} for an expression, {"bind": "..."}
    or {"pulse": true}. Pass `op_type` to have the names validated locally
    before anything is sent. Pass the same `owner` you claimed the area with,
    or your own claim refuses this write.
    """
    if op_type:
        try:
            check = validate_params(store(), op_type, pars)
            if not check.ok:
                return f"{check.render()}\n\n{hint('params_refused')}"
        except RuntimeError:
            pass
    client = bridge()
    try:
        result = client.call("par_set", path=path, pars=pars, owner=owner)
    except (BridgeUnavailable, BridgeError) as exc:
        return failure(exc)
    applied = ", ".join(f"{k}={v!r}" for k, v in result["applied"].items())
    return f"{_warn(client)}{result['path']}: {applied}"


@mcp.tool()
@guarded
def td_render(path: str, width: int = 512, height: int = 0):
    """Render a TOP and return the image, so you can see what you built.

    TouchDesigner is a visual tool: check your work with this rather than
    inferring it from parameter values. `height` defaults to preserving the
    TOP's aspect ratio.
    """
    # Deliberately unannotated: this returns an Image on success and an error
    # string otherwise, and a union of the two cannot be expressed in the
    # output schema FastMCP derives from the annotation.
    try:
        data, _meta = bridge().render(
            path, fmt=".png", width=width or None, height=height or None
        )
    except (BridgeUnavailable, BridgeError) as exc:
        return failure(exc)
    return Image(data=data, format="png")


@mcp.tool()
@guarded
def td_health(path: str = "/project1", interval: float = 1.0) -> str:
    """Find what is quietly broken — the failures nothing reports.

    Run this after building anything, and whenever a composition "looks fine
    but does nothing". td_errors only covers what TouchDesigner calls an
    error; this additionally catches:

    - operators that never cook, because a branch nothing displays or records
      is never pulled and therefore is not running at all
    - output operators switched off (audio device, movie recorder, MIDI, OSC)
      which produce nothing and report nothing
    - GLSL operators whose shader failed to compile, quoting the compiler's own
      line: TouchDesigner only warns that an Info DAT would show the details
    - tracebacks raised inside callbacks and extensions (Execute DAT,
      Replicator, component callbacks), which are kept apart from the error
      list. A Script operator's onCook raising during a cook Python asked for
      is not covered — that traceback goes back to the caller instead
    - operators costing more than half a frame to cook, and the resulting
      frame rate collapse
    - bypassed operators, and the current licence
    """
    from ..bridge.health import check

    client = bridge()
    try:
        result = check(client, path=path, interval=interval).render()
    except (BridgeUnavailable, BridgeError) as exc:
        return failure(exc)
    return _warn(client) + result


@mcp.tool()
@guarded
def td_palette(query: str = "", category: str = "", limit: int = 20) -> str:
    """Search the ready-made components TouchDesigner ships in its palette.

    277 finished tools — projection mappers, corner-pinners, colour pickers,
    audio analysers, UI widgets. Check here before building something from
    scratch, then install the one you want with td_palette_load.
    """
    db = store()
    sql = "SELECT name, category, path, summary FROM palette"
    params: list[Any] = []
    order = " ORDER BY category, name"
    if query:
        # Weight the component's own name and its palette folder far above the
        # summary: searching "projection mapping" should surface the Mapping
        # folder, not an article that happens to describe a fractal as "a
        # mapping of the Julia set".
        sql = (
            "SELECT p.name, p.category, p.path, p.summary FROM palette_fts f "
            "JOIN palette p ON p.name = f.name AND p.category = f.category "
            "WHERE palette_fts MATCH ?"
        )
        params.append(db.match(query))
        order = " ORDER BY bm25(palette_fts, 12.0, 8.0, 1.0)"
    if category:
        sql += " AND" if query else " WHERE"
        sql += " category LIKE ?"
        params.append(f"%{category}%")
    sql += order + " LIMIT ?"
    params.append(limit)

    try:
        rows = db.conn.execute(sql, params).fetchall()
    except Exception as exc:
        return failure(exc)
    if not rows:
        return f"No palette component matches {query or category!r}.\n{hint('no_match')}"
    lines = []
    for row in rows:
        lines.append(f"{row['name']} [{row['category']}]  {row['path']}")
        if row["summary"]:
            lines.append(f"    {row['summary'][:220]}")
    return "\n".join(lines)


@mcp.tool()
@guarded
def td_palette_load(
    name: str,
    parent: str = "/project1",
    rename: str = "",
    position: list | None = None,
    category: str = "",
    owner: str = "",
) -> str:
    """Install one of TouchDesigner's palette components into the project.

    Reach for this the moment td_palette shows a component that does what you
    were about to build by hand — it is one call, where the alternative is
    td_exec with a `loadTox` path you have to get exactly right, on a machine
    whose TouchDesigner may not be installed where you assume.

    `name` is the component name td_palette reports. The .tox path comes from
    the index and is checked on disk before anything is sent, so a stale index
    fails here rather than as a TouchDesigner traceback. Fourteen palette names
    exist in two folders each (the Ableton set, `operatorPath`,
    `vrRenderToMovie`): those are refused with the candidates listed until you
    narrow them with `category`.

    The node's final name is reported back rather than assumed: TouchDesigner
    names a loaded component after its file, and numbers it (`checker1`,
    `checker2`) when a sibling already holds that name. `rename` is stricter —
    a name already taken is refused outright and the load is rolled back, so
    read the path in the reply rather than assuming the name you asked for.

    Loading is not free: measured 0.006 s for a 20-operator component and
    1.08 s for kantanMapper's 4,079, all of it on TouchDesigner's main thread.
    """
    db = store()
    rows = db.conn.execute(
        "SELECT name, category, path, summary FROM palette WHERE name = ?",
        (name,),
    ).fetchall()
    if category:
        needle = category.lower()
        rows = [r for r in rows if needle in (r["category"] or "").lower()]

    if not rows:
        detail = f" in a category matching {category!r}" if category else ""
        return (
            f"No palette component is named {name!r}{detail}.\n"
            f"{hint('palette_unknown')}"
        )
    if len(rows) > 1:
        lines = [
            f"{len(rows)} palette components are named {name!r}. Refusing to "
            f"guess — repeat the call with category= set to one of these:"
        ]
        lines += [f"  category={r['category']!r}  {r['path']}" for r in rows]
        lines.append(hint("palette_ambiguous"))
        return "\n".join(lines)

    row = rows[0]
    tox = Path(row["path"])
    if not tox.is_file():
        return (
            f"error: the index lists {row['name']} at {tox}, but there is "
            f"no file there.\n{hint('index_stale')}"
        )

    client = bridge()
    try:
        result = client.call(
            "palette_load",
            parent=parent,
            file=str(tox),
            name=rename or None,
            position=position,
            owner=owner,
        )
    except (BridgeUnavailable, BridgeError) as exc:
        return failure(exc)

    lines = [
        f"loaded {row['name']} [{row['category']}] into {parent} as "
        f"{result['path']} ({result.get('type', '')})"
    ]
    if not rename and result.get("name") != row["name"]:
        # Only reachable without `rename`: a taken rename is refused by
        # TouchDesigner and comes back as an error above, never as a
        # different name. Without one, a sibling makes the load renumber
        # silently, and every later call needs the real path.
        lines.append(
            f"  note: {row['name']} already existed here, so this one is "
            f"{result.get('name')!r}."
        )
    if result.get("errors"):
        lines.append(f"  ERROR: {result['errors']}")
    if result.get("warnings"):
        lines.append(f"  warning: {result['warnings']}")
    if not result.get("errors") and not result.get("warnings"):
        lines.append(
            "  nothing reported yet — but this is read before the component "
            "has cooked, so it is not a clean bill of health. Palette "
            "components usually need parameters set before they do anything "
            "(td_op_info lists them), and td_health is what catches a "
            "component that fails once it runs."
        )
    return _warn(client) + "\n".join(lines)


@mcp.tool()
@guarded
def td_glossary(term: str, limit: int = 5) -> str:
    """Look up TouchDesigner terminology.

    185 glossary entries defining the vocabulary the rest of the docs assume:
    Cook, Time Slice, Par, CHOP, Clone, Tox, Perform Mode, Sample.
    """
    db = store()
    rows = db.conn.execute(
        "SELECT a.page, a.title, a.text FROM articles_fts f "
        "JOIN articles a ON a.page = f.page "
        "WHERE articles_fts MATCH ? AND a.categories LIKE '%Touch Glossary%' "
        "ORDER BY bm25(articles_fts, 4.0, 10.0, 1.0) LIMIT ?",
        (db.match(term), limit),
    ).fetchall()
    if not rows:
        return f"No glossary entry matches {term!r}.\n{hint('no_match')}"
    return "\n\n".join(
        f"## {r['title']}\n{(r['text'] or '')[:900]}" for r in rows
    )


@mcp.tool()
@guarded
def td_errors() -> str:
    """Every operator in the project currently reporting an error or warning.

    Node errors are shown as colours in the TouchDesigner UI and are otherwise
    invisible to you; check this after building something.
    """
    client = bridge()
    try:
        result = client.errors()
    except (BridgeUnavailable, BridgeError) as exc:
        return failure(exc)
    if not result["count"]:
        return _warn(client) + "No operators are reporting errors or warnings."
    lines = [f"{result['count']} operator(s) reporting problems:"]
    for node in result["nodes"]:
        detail = node["errors"] or node["warnings"]
        kind = "ERROR" if node["errors"] else "warning"
        lines.append(f"  [{kind}] {node['path']} ({node['type']}): {detail}")
    return _warn(client) + "\n".join(lines)


@mcp.tool()
@guarded
def td_exec(code: str) -> str:
    """Run Python inside TouchDesigner and return its output.

    The full `td` namespace is in scope (op, ops, root, project, ui, app,
    families, operator type classes). A trailing expression, or a variable named
    `result`, is returned. Reach for the structured tools first — this blocks
    TouchDesigner's main thread while it runs.
    """
    client = bridge()
    try:
        result = client.exec(code)
    except BridgeError as exc:
        return failure(
            exc, head=f"{exc.type}: {exc.message}\n{exc.traceback or ''}"
        )
    except BridgeUnavailable as exc:
        return failure(exc)
    parts = []
    if result.get("stdout"):
        parts.append(result["stdout"].rstrip())
    if result.get("stderr"):
        parts.append("stderr: " + result["stderr"].rstrip())
    if result.get("result") is not None:
        parts.append(json.dumps(result["result"], indent=2, default=str))
    return _warn(client) + ("\n".join(parts) or "(no output)")


# -- project file tools -----------------------------------------------------

@mcp.tool()
@guarded
def td_project_read(
    file: str, path: str = "", depth: int = 2, params: bool = False
) -> str:
    """Read a .toe or .tox from disk, without TouchDesigner running.

    Returns the operator tree with wiring. `path` narrows to a subtree such as
    '/project1', `depth` is how many levels of children to show, and `params`
    adds the parameter values that differ from the defaults — which is all a
    saved project records, so it is exactly what someone chose deliberately.
    """
    from ..project import ExpandError, index_resolver, load_file
    from ..project.render import describe

    try:
        project = load_file(file, resolver=index_resolver())
    except ExpandError as exc:
        return failure(exc)
    return describe(project, path=path or None, depth=depth, params=params)


@mcp.tool()
@guarded
def td_project_grep(file: str, pattern: str, limit: int = 60) -> str:
    """Search the Python and GLSL held inside a project's DATs.

    Ordinary file search cannot reach this code: it lives inside the .toe
    container, not on disk. `pattern` is a regular expression.
    """
    from ..project import ExpandError, index_resolver, load_file
    from ..project.render import grep, render_matches

    try:
        project = load_file(file, resolver=index_resolver())
        matches = grep(project, pattern, limit=limit)
    except ExpandError as exc:
        return failure(exc)
    except ValueError as exc:
        return f"error: {exc}\n{hint('bad_pattern')}"
    return render_matches(matches, pattern)


@mcp.tool()
@guarded
def td_project_diff(
    before: str, after: str, show_moves: bool = False, include_text: bool = True
) -> str:
    """Compare two .toe/.tox files and report what actually changed.

    Reports added, removed, retyped, rewired and re-parameterised operators,
    plus a line diff of any changed DAT code. Nodes that were only dragged to
    a new position are counted separately so they cannot bury a real change.
    """
    from ..project import ExpandError, index_resolver, load_file
    from ..project.diff import diff

    try:
        result = diff(
            load_file(before, resolver=index_resolver()),
            load_file(after, resolver=index_resolver()),
            include_text=include_text,
        )
    except ExpandError as exc:
        return failure(exc)
    return f"{result.summary()}\n\n{result.render(show_moves=show_moves)}"


@mcp.tool()
@guarded
def td_snapshot(label: str = "snapshot", path: str = "/project1") -> str:
    """Save a component to a file so it can be diffed later.

    Take one before a round of edits and another after, then pass both to
    td_project_diff to see exactly what changed. Snapshots go to
    ~/.td-atlas/snapshots.

    A component is written rather than the whole session because saving the
    session is a Save As: it repoints TouchDesigner at the snapshot file and
    leaves the artist working in ~/.td-atlas instead of their own project.
    """
    if not re.fullmatch(r"[\w.-]+", label):
        return f"error: {label!r} is not a usable label.\n{hint('bad_label')}"
    target = cfg.home() / "snapshots"
    target.mkdir(parents=True, exist_ok=True)
    destination = target / f"{label}.tox"
    if destination.exists():
        destination.unlink()

    client = bridge()
    try:
        result = client.call("save_tox", path=path, file=str(destination))
    except (BridgeUnavailable, BridgeError) as exc:
        return failure(exc)
    saved = result.get("saved") or destination
    return f"{_warn(client)}saved {path} to {saved}"


@mcp.tool()
@guarded
def td_example(op_type: str, depth: int = 3) -> str:
    """Show a working example network for an operator.

    TouchDesigner ships an example .tox for most operators. This reads one
    offline and describes how it is wired and configured — a real usage
    reference rather than a parameter list.
    """
    db = store()
    row = db.conn.execute(
        "SELECT type, snippet_path FROM ops WHERE type = ?", (op_type,)
    ).fetchone()
    if row is None:
        return f"No operator type '{op_type}'.\n{hint('unknown_op_type')}"
    if not row["snippet_path"]:
        return (
            f"TouchDesigner ships no example network for {op_type}.\n"
            f"{hint('no_example')}"
        )

    from ..project import ExpandError, index_resolver, load_file
    from ..project.render import describe

    try:
        project = load_file(row["snippet_path"], resolver=index_resolver())
    except ExpandError as exc:
        return failure(exc)
    return describe(project, depth=depth, params=True)


@mcp.tool()
@guarded
def td_undo(redo: bool = False) -> str:
    """Undo (or redo) the last change, including whole td_build batches."""
    client = bridge()
    try:
        result = client.call("redo" if redo else "undo")
    except (BridgeUnavailable, BridgeError) as exc:
        return failure(exc)
    stack = result.get("redoStack" if redo else "undoStack") or []
    return (
        f"{_warn(client)}{'redone' if redo else 'undone'}; "
        f"stack now: {stack[-5:] or 'empty'}"
    )


# -- scope claims -----------------------------------------------------------

@mcp.tool()
@guarded
def td_claim_scope(path: str, owner: str, ttl_seconds: float = 600) -> str:
    """Announce one subtree of the network as yours while you work in it.

    Reach for this before a run of edits whenever another agent or session may
    be touching the same project: without a claim, two agents editing the same
    nodes overwrite each other and neither result reports anything wrong.
    `owner` is any string that identifies you (a task name, a session id) —
    it is what the other agent is told when it is refused.

    The claim covers everything below `path`: '/project1/audio' includes
    '/project1/audio/eq1' but not '/project1/audio2'. It lapses on its own
    after `ttl_seconds`, so a crash cannot park a subtree for the session;
    claim again to renew. This is an agreement between agents, not a lock —
    it does not constrain a person editing those nodes by hand.

    Write calls must carry the same `owner` to pass their own claim; the
    refusal text says which owner to send.
    """
    client = bridge()
    try:
        result = client.call(
            "claim_scope", path=path, owner=owner, ttl=ttl_seconds
        )
    except (BridgeUnavailable, BridgeError) as exc:
        return failure(exc)
    verb = "renewed" if result.get("renewed") else "claimed"
    return (
        f"{_warn(client)}{verb} {result['path']} for '{result['owner']}' "
        f"until {result['expiresAt']} ({result['expiresIn']:g} s)"
    )


@mcp.tool()
@guarded
def td_release_scope(path: str, owner: str) -> str:
    """Give a claimed subtree back before its claim expires.

    Call it as soon as a run of edits is finished — otherwise the next agent
    waits out the whole time-to-live for nothing. Only the owner named on the
    claim can release it.
    """
    client = bridge()
    try:
        result = client.call("release_scope", path=path, owner=owner)
    except (BridgeUnavailable, BridgeError) as exc:
        return failure(exc)
    if not result.get("released"):
        return f"{_warn(client)}{result.get('note', 'nothing to release')}"
    return f"{_warn(client)}released {result['path']}"


@mcp.tool()
@guarded
def td_scopes() -> str:
    """Which subtrees other agents have claimed, and until when.

    Check this before editing a project someone else may be in — it is the
    only way to see a claim before a write bounces off it, and it names the
    owner to coordinate with.
    """
    client = bridge()
    try:
        result = client.call("scopes")
    except (BridgeUnavailable, BridgeError) as exc:
        return failure(exc)
    if not result["count"]:
        return _warn(client) + "No scope is claimed; the whole network is free."
    lines = [f"{result['count']} scope(s) claimed:"]
    for claim in result["scopes"]:
        lines.append(
            f"  {claim['path']} — '{claim['owner']}' since {claim['claimedAt']}, "
            f"until {claim['expiresAt']} ({claim['expiresIn']:g} s left)"
        )
    return _warn(client) + "\n".join(lines)


@mcp.tool()
@guarded
def td_extension_add(
    class_name: str,
    code: str,
    path: str = "",
    parent: str = "",
    name: str = "",
    extension_name: str = "",
    promote: bool = True,
    index: int = 0,
    owner: str = "",
) -> str:
    """Attach a Python class to a COMP as an extension, in one call.

    Reach for this instead of td_exec whenever a component needs methods or
    state of its own. Done by hand it is five steps — create the COMP, create
    the DAT, write the text, set three parameters on the Extensions page,
    re-initialise — and the last one fails silently: measured on 2025.32460, a
    wrong Extension Object expression or a class that raises in `__init__`
    leaves the COMP reporting no error, no warning, and `extensionsReady` True,
    with the real message only in the textport. This tool reads the result back
    off the COMP and hands you that message.

    Aim it either at `path` (an existing COMP) or at `parent` plus `name` (a
    baseCOMP to create) — not both. `code` must define `class <class_name>`,
    conventionally taking `ownerComp` and capitalising anything meant to be
    called from outside: with `promote` on, capitalised members are callable
    straight on the COMP, and every member is reachable as
    `op(...).ext.<name>.<member>` regardless.

    The class name doubles as the name of the textDAT holding the code, which
    is what the generated Extension Object expression points at
    (`op('./Name').module.Name(me)`). `extension_name` renames the extension
    for `ext` lookups without touching the class. `index` picks which
    extension slot to write; the wiki says a COMP has four, the sequence took
    six here.

    The code is parsed on this host before anything is sent, so a typo costs
    no round trip. That check is not a guarantee TouchDesigner accepts it:
    this host's Python may be newer than TouchDesigner's embedded 3.11, so
    3.12+ syntax passes here and fails there — which is caught, but only by
    the read-back above. Pass the same `owner` you claimed the area with, or
    your own claim refuses this write.
    """
    if not class_name.isidentifier():
        return (
            f"error: {class_name!r} is not a Python identifier, so it cannot "
            f"name a class.\n{hint('extension_class_missing')}"
        )
    if extension_name and not extension_name.isidentifier():
        return (
            f"error: extension_name {extension_name!r} is not a Python "
            f"identifier — it becomes an attribute of `ext`.\n"
            f"{hint('extension_class_missing')}"
        )
    if bool(path) == bool(parent and name):
        return (
            "error: aim this at either an existing COMP (path=) or a COMP to "
            "create (parent= and name=), not both and not neither.\n"
            f"{hint('extension_target')}"
        )
    if not code.strip():
        return (
            "error: 'code' is empty, so there is no class to attach.\n"
            f"{hint('extension_class_missing')}"
        )

    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return (
            f"error: the extension code does not parse: {exc.msg} at line "
            f"{exc.lineno}. Nothing was sent.\n{hint('extension_syntax')}"
        )
    classes = [n.name for n in tree.body if isinstance(n, ast.ClassDef)]
    if class_name not in classes:
        found = ", ".join(classes) or "none"
        return (
            f"error: the code defines no top-level class {class_name!r} "
            f"(found: {found}). TouchDesigner reads the class as a module "
            f"attribute, so a nested or renamed class cannot be reached. "
            f"Nothing was sent.\n{hint('extension_class_missing')}"
        )

    client = bridge()
    try:
        result = client.call(
            "extension_add",
            path=path or None,
            parent=parent or None,
            name=name or None,
            class_name=class_name,
            code=code,
            extension_name=extension_name,
            promote=promote,
            index=index,
            owner=owner,
        )
    except (BridgeUnavailable, BridgeError) as exc:
        return failure(exc)

    verb = "built" if result["createdComp"] else "attached"
    lines = [
        f"{verb} extension {result['extension']} on {result['path']} "
        f"(slot {result['index']}, code in {result['dat']})"
    ]
    for par, value in result["set"].items():
        lines.append(f"  {par} = {value!r}")
    if result["ok"]:
        lines.append(f"  initialised: {result['object']}")
        if promote:
            lines.append(
                f"  call it as op('{result['path']}').<CapitalisedMember>() or "
                f"op('{result['path']}').ext.{result['extension']}.<member>()"
            )
        else:
            lines.append(
                f"  not promoted, so reach it as "
                f"op('{result['path']}').ext.{result['extension']}.<member>()"
            )
        if result.get("error"):
            lines.append(f"  WARNING: {result['error']}")
        return _warn(client) + "\n".join(lines)

    lines.append(
        f"  NOT INITIALISED — the parameters are set but the extension is "
        f"None. TouchDesigner reported this nowhere; the message below comes "
        f"from re-evaluating the expression:"
    )
    lines.append(f"  {result['error']}")
    lines.append(
        f"  the code is in {result['dat']}: fix it and repeat this call, or "
        f"td_undo to remove the whole block."
    )
    lines.append(hint("extension_init_failed"))
    return _warn(client) + "\n".join(lines)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
