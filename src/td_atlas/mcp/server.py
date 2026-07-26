"""MCP server exposing the atom index and the live bridge.

Two layers of tools. The index tools answer offline and cost no round trip to
TouchDesigner — an agent should consult them before it builds anything. The
bridge tools act on a running instance, and validate against the index first,
so a mistyped parameter comes back as "did you mean" rather than as a
TouchDesigner traceback.
"""

from __future__ import annotations

import json
import re
from typing import Any

from mcp.server.fastmcp import FastMCP, Image

from .. import config as cfg
from ..atoms.store import AtomStore
from ..atoms.validate import validate_params
from ..bridge.client import BridgeClient, BridgeError, BridgeUnavailable

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
            raise RuntimeError(
                "No atom index yet. Run 'td-atlas build' (and 'td-atlas probe' "
                "with TouchDesigner open) to create it."
            )
    return _store


def bridge() -> BridgeClient:
    return BridgeClient.discover()


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
def td_search_operators(query: str, family: str = "", limit: int = 12) -> str:
    """Find TouchDesigner operators by what they do.

    Searches names, labels, summaries and full documentation. Use this first,
    in plain language ("blur an image", "read a MIDI device", "instance
    geometry"). `family` optionally narrows to TOP, CHOP, SOP, DAT, MAT, COMP
    or POP.
    """
    # Weight the columns so a name or label hit outranks a passing mention
    # buried in an article body. Columns: type, family, label, summary, doc_text.
    sql = (
        "SELECT type, family, label, summary FROM ops_fts "
        "WHERE ops_fts MATCH ?"
    )
    params: list[Any] = [store().match(query)]
    if family:
        sql += " AND family = ?"
        params.append(family.upper())
    sql += " ORDER BY bm25(ops_fts, 12.0, 1.0, 10.0, 4.0, 1.0) LIMIT ?"
    params.append(limit)

    rows = store().conn.execute(sql, params).fetchall()
    if not rows:
        return f"No operators match {query!r}."
    lines = []
    for row in rows:
        summary = (row["summary"] or "").split(". ")[0]
        lines.append(f"{row['type']} ({row['family']}) — {row['label']}: {summary}")
    return "\n".join(lines)


@mcp.tool()
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
        hint = (
            " Did you mean: " + ", ".join(r["type"] for r in close) if close else ""
        )
        return f"No operator type '{op_type}'.{hint}"

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
        return f"No parameters match {query!r}."
    return "\n".join(
        f"{r['op_type']}.{r['name']} — {r['label']}: "
        f"{(r['summary'] or '')[:160]}"
        for r in rows
    )


@mcp.tool()
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
        return f"Nothing found for '{name}'."

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
            return f"No article '{page}'."
        return f"# {row['title']}\n\n{row['text'][:20000]}"

    rows = db.conn.execute(
        "SELECT page, title, snippet(articles_fts, 2, '**', '**', ' … ', 40) s "
        "FROM articles_fts WHERE articles_fts MATCH ? "
        "ORDER BY bm25(articles_fts, 4.0, 10.0, 1.0) LIMIT ?",
        (store().match(query), limit),
    ).fetchall()
    if not rows:
        return f"No documentation matches {query!r}."
    return "\n\n".join(
        f"## {r['title']}  (page: {r['page']})\n{r['s']}" for r in rows
    )


@mcp.tool()
def td_expression_help(query: str, limit: int = 10) -> str:
    """Look up TouchDesigner expression and command syntax."""
    rows = store().conn.execute(
        "SELECT kind, name, signature, text FROM expressions "
        "WHERE name LIKE ? OR text LIKE ? LIMIT ?",
        (f"%{query}%", f"%{query}%", limit),
    ).fetchall()
    if not rows:
        return f"No expression or command matches {query!r}."
    return "\n\n".join(
        f"[{r['kind']}] {r['signature']}\n{(r['text'] or '')[:500]}" for r in rows
    )


# -- bridge tools -----------------------------------------------------------

@mcp.tool()
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
        db_note = f"index: {exc}"

    try:
        info = bridge().ping()
    except (BridgeUnavailable, BridgeError) as exc:
        return f"{db_note}\nbridge: unavailable — {exc}"
    return (
        f"{db_note}\nbridge: connected to {info['product']} {info['build']}, "
        f"project '{info['project']}' in {info['projectFolder']}, "
        f"{info['fps']} fps, frame {info['frame']}"
    )


@mcp.tool()
def td_network(path: str = "/project1", depth: int = 1) -> str:
    """List the operators inside a component and how they are wired."""
    try:
        result = bridge().network(path=path, depth=depth)
    except (BridgeUnavailable, BridgeError) as exc:
        return f"error: {exc}"

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
    return "\n".join(lines)


@mcp.tool()
def td_op_info(path: str) -> str:
    """Inspect one operator in the running project: type, wiring, live parameter values."""
    try:
        info = bridge().op_info(path)
    except (BridgeUnavailable, BridgeError) as exc:
        return f"error: {exc}"

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
    return "\n".join(lines)


@mcp.tool()
def td_build(operations: list[dict], undo_name: str = "agent edit") -> str:
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
            )

    try:
        result = bridge().batch(operations, undo_name=undo_name)
    except BridgeError as exc:
        return (
            f"batch failed and was rolled back — {exc.type}: {exc.message}\n"
            f"{exc.traceback or ''}"
        )
    except BridgeUnavailable as exc:
        return f"error: {exc}"

    lines = [f"applied {result['applied']} operation(s) as '{undo_name}'"]
    for item in result["results"]:
        if isinstance(item, dict) and "path" in item:
            lines.append(f"  {item['path']} ({item.get('type', '')})")
    return "\n".join(lines)


@mcp.tool()
def td_set_params(path: str, pars: dict, op_type: str = "") -> str:
    """Set parameters on an existing operator, checked against the index first.

    Values may be a constant, {"expr": "..."} for an expression, {"bind": "..."}
    or {"pulse": true}. Pass `op_type` to have the names validated locally
    before anything is sent.
    """
    if op_type:
        try:
            check = validate_params(store(), op_type, pars)
            if not check.ok:
                return check.render()
        except RuntimeError:
            pass
    try:
        result = bridge().call("par_set", path=path, pars=pars)
    except (BridgeUnavailable, BridgeError) as exc:
        return f"error: {exc}"
    applied = ", ".join(f"{k}={v!r}" for k, v in result["applied"].items())
    return f"{result['path']}: {applied}"


@mcp.tool()
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
        return f"error: {exc}"
    return Image(data=data, format="png")


@mcp.tool()
def td_errors() -> str:
    """Every operator in the project currently reporting an error or warning.

    Node errors are shown as colours in the TouchDesigner UI and are otherwise
    invisible to you; check this after building something.
    """
    try:
        result = bridge().errors()
    except (BridgeUnavailable, BridgeError) as exc:
        return f"error: {exc}"
    if not result["count"]:
        return "No operators are reporting errors or warnings."
    lines = [f"{result['count']} operator(s) reporting problems:"]
    for node in result["nodes"]:
        detail = node["errors"] or node["warnings"]
        kind = "ERROR" if node["errors"] else "warning"
        lines.append(f"  [{kind}] {node['path']} ({node['type']}): {detail}")
    return "\n".join(lines)


@mcp.tool()
def td_exec(code: str) -> str:
    """Run Python inside TouchDesigner and return its output.

    The full `td` namespace is in scope (op, ops, root, project, ui, app,
    families, operator type classes). A trailing expression, or a variable named
    `result`, is returned. Reach for the structured tools first — this blocks
    TouchDesigner's main thread while it runs.
    """
    try:
        result = bridge().exec(code)
    except BridgeError as exc:
        return f"{exc.type}: {exc.message}\n{exc.traceback or ''}"
    except BridgeUnavailable as exc:
        return f"error: {exc}"
    parts = []
    if result.get("stdout"):
        parts.append(result["stdout"].rstrip())
    if result.get("stderr"):
        parts.append("stderr: " + result["stderr"].rstrip())
    if result.get("result") is not None:
        parts.append(json.dumps(result["result"], indent=2, default=str))
    return "\n".join(parts) or "(no output)"


# -- project file tools -----------------------------------------------------

@mcp.tool()
def td_project_read(
    file: str, path: str = "", depth: int = 2, params: bool = False
) -> str:
    """Read a .toe or .tox from disk, without TouchDesigner running.

    Returns the operator tree with wiring. `path` narrows to a subtree such as
    '/project1', `depth` is how many levels of children to show, and `params`
    adds the parameter values that differ from the defaults — which is all a
    saved project records, so it is exactly what someone chose deliberately.
    """
    from ..project import ExpandError, load_file
    from ..project.render import describe

    try:
        project = load_file(file)
    except ExpandError as exc:
        return f"error: {exc}"
    return describe(project, path=path or None, depth=depth, params=params)


@mcp.tool()
def td_project_grep(file: str, pattern: str, limit: int = 60) -> str:
    """Search the Python and GLSL held inside a project's DATs.

    Ordinary file search cannot reach this code: it lives inside the .toe
    container, not on disk. `pattern` is a regular expression.
    """
    from ..project import ExpandError, load_file
    from ..project.render import grep, render_matches

    try:
        project = load_file(file)
        matches = grep(project, pattern, limit=limit)
    except ExpandError as exc:
        return f"error: {exc}"
    except ValueError as exc:
        return f"error: {exc}"
    return render_matches(matches, pattern)


@mcp.tool()
def td_project_diff(
    before: str, after: str, show_moves: bool = False, include_text: bool = True
) -> str:
    """Compare two .toe/.tox files and report what actually changed.

    Reports added, removed, retyped, rewired and re-parameterised operators,
    plus a line diff of any changed DAT code. Nodes that were only dragged to
    a new position are counted separately so they cannot bury a real change.
    """
    from ..project import ExpandError, load_file
    from ..project.diff import diff

    try:
        result = diff(
            load_file(before), load_file(after), include_text=include_text
        )
    except ExpandError as exc:
        return f"error: {exc}"
    return f"{result.summary()}\n\n{result.render(show_moves=show_moves)}"


@mcp.tool()
def td_snapshot(label: str = "snapshot") -> str:
    """Save the running project to a file so it can be diffed later.

    Take one before a round of edits and another after, then pass both to
    td_project_diff to see exactly what changed. Snapshots are written under
    ~/.td-atlas/snapshots and never touch the artist's own file.
    """
    if not re.fullmatch(r"[\w.-]+", label):
        return "error: label may contain only letters, digits, dot, dash, underscore"
    target = cfg.home() / "snapshots"
    target.mkdir(parents=True, exist_ok=True)

    # TouchDesigner appends '.1', '.2' rather than overwriting, so old files
    # under this label are cleared first to keep the path predictable.
    for stale in target.glob(f"{label}.toe") :
        stale.unlink()
    for stale in target.glob(f"{label}.[0-9]*.toe"):
        stale.unlink()

    destination = target / f"{label}.toe"
    try:
        bridge().call("save", path=str(destination))
    except (BridgeUnavailable, BridgeError) as exc:
        return f"error: {exc}"

    written = sorted(
        target.glob(f"{label}*.toe"), key=lambda p: p.stat().st_mtime
    )
    if not written:
        return f"error: TouchDesigner reported no file at {destination}"
    return f"saved to {written[-1]}"


@mcp.tool()
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
        return f"No operator type '{op_type}'."
    if not row["snippet_path"]:
        return f"TouchDesigner ships no example network for {op_type}."

    from ..project import ExpandError, load_file
    from ..project.render import describe

    try:
        project = load_file(row["snippet_path"])
    except ExpandError as exc:
        return f"error: {exc}"
    return describe(project, depth=depth, params=True)


@mcp.tool()
def td_undo(redo: bool = False) -> str:
    """Undo (or redo) the last change, including whole td_build batches."""
    try:
        result = bridge().call("redo" if redo else "undo")
    except (BridgeUnavailable, BridgeError) as exc:
        return f"error: {exc}"
    stack = result.get("redoStack" if redo else "undoStack") or []
    return f"{'redone' if redo else 'undone'}; stack now: {stack[-5:] or 'empty'}"


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
