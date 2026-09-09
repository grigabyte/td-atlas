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
from .. import journal
from ..atoms.store import AtomStore
from ..atoms.validate import validate_params
from ..bridge.client import BridgeClient, BridgeError, BridgeUnavailable
from ..component.handler import NODE_FLAGS
from .hints import IndexMissing, failure, from_record, guarded, hint


def _package_version() -> str:
    """Our own version, from the installed metadata rather than a copy here."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("td-atlas")
    except PackageNotFoundError:  # running from a source tree, not installed
        return "0+unknown"


mcp = FastMCP(
    "td-atlas",
    instructions=(
        "Control TouchDesigner. Search the index for the right operator and "
        "its exact parameter names before building; the index is offline and "
        "free to query. Use td_build for multi-step edits so they land as one "
        "undoable block, and td_render to see what you made."
    ),
)

# FastMCP takes no version, and the low-level server left at None reports the
# `mcp` library's own version to every client — measured: 1.29.1 against our
# 0.1.0. A bundle published under one version whose server announces another
# is the kind of quiet mismatch this project exists to catch.
mcp._mcp_server.version = _package_version()

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


def _menu_options(names: list[str], labels: list[str] | None) -> str:
    """Render a menu, keeping the labels when they say more than the values.

    The value a parameter takes and the label TouchDesigner shows for it are
    not the same string, and the difference is sometimes the only warning you
    get: the Noise TOP's `type` menu takes 'simplex3d' and 'sparse', and only
    the labels — "Simplex 3D (GPU)" against a plain "Sparse" — say that one
    runs on the GPU and the other on the CPU at ~96 ms per cook. Dropping the
    labels dropped that. Labels are paired in whenever any of them differs
    from its own value by more than capitalisation, so a menu whose labels
    add nothing stays as short as it was.
    """
    if not labels or len(labels) != len(names):
        return json.dumps(names, ensure_ascii=False)

    def bare(text: str) -> str:
        return "".join(ch for ch in text.lower() if ch.isalnum())

    if not any(bare(n) != bare(str(lab)) for n, lab in zip(names, labels)):
        return json.dumps(names, ensure_ascii=False)
    return json.dumps(
        [f"{n} — {lab}" for n, lab in zip(names, labels)], ensure_ascii=False
    )


def _fmt_param(row: dict[str, Any]) -> str:
    bits = [row["style"] or row["par_type"] or "?"]
    if row.get("default") is not None:
        bits.append(f"default={row['default']!r}")
    if row.get("menu_names"):
        bits.append(
            "options=" + _menu_options(
                row["menu_names"], row.get("menu_labels")
            )
        )
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

# `limit` is held equal to the CLI's `search --limit` by
# tests/test_cli_mcp_parity.py — the two surfaces answered the same question
# with different amounts of it for no recorded reason.
@mcp.tool()
@guarded
def td_search_operators(query: str, family: str = "", limit: int = 15) -> str:
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
def td_log(
    limit: int = 20,
    failures: bool = False,
    summary: bool = False,
    method: str = "",
) -> str:
    """Your own trail: every bridge call this host has made, and how it went.

    Reach for this when something is wrong and you do not know what you did —
    an operator is missing, a parameter is not what you set, the artist says
    "it broke after you touched it". `td_status` shows only the last call and
    the next one overwrites it; this is the whole session, and it survives
    TouchDesigner being closed and reopened, so it also answers "what happened
    yesterday".

    Also reach for it before repeating a call that failed. `failures=True`
    gives the refusals alone, each with the text it refused with, and the
    repair for the most recent one — repeating a call that a scope claim or a
    missing path already refused will refuse again for the same reason.

    `summary=True` answers a different question: over everything recorded,
    which methods refuse and which are slow. Use it to notice a pattern you
    are inside of — the same method failing five times means the approach is
    wrong, not the call.

    Not everything is here, and the gap matters: only calls that reached the
    bridge are recorded. The offline tools (td_project_read, td_docs,
    td_search_operators) never dial it and leave no trace, so an empty
    journal means no live work, not no work.
    """
    calls = journal.read(
        limit=None if summary else max(0, int(limit)),
        failures_only=bool(failures),
        method=method or "",
    )
    if summary:
        return journal.format_summary(journal.summarise(calls))
    text = journal.format_calls(calls)
    if failures and calls:
        last = calls[-1]
        text += "\n\n" + from_record(last.error, last.reason).render()
    return text


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
            # Printed under the parent it belongs to, and printed even when
            # that parent's listing is empty: a cut the reader cannot see is
            # a network they will read as complete.
            if node.get("childrenHidden"):
                lines.append(
                    f"{' ' * (indent + 2)}... {node['childrenHidden']} more "
                    f"child(ren) not listed"
                )
            elif not node.get("children") and node.get("numChildren"):
                # The depth the caller asked for is a cut like any other, and
                # it was the one cut nothing said out loud: the handler does
                # not recurse past `depth` and sets no marker there, so a leaf
                # and a component holding thousands of operators arrived as
                # the same line. `numChildren` has always been in the reply
                # and was simply never printed — measured 2026-09-07,
                # `path=/ depth=1` listed /perform (a real leaf) and /ui
                # (22,635 operators below it) identically. Named as the
                # caller's own depth rather than counted into `hidden`: that
                # total is about this tool's node budget, and a depth cut
                # reported against it would name the wrong bound and the
                # wrong repair.
                lines.append(
                    f"{' ' * (indent + 2)}... {node['numChildren']} direct "
                    f"child(ren), not walked at depth {result.get('depth')} "
                    f"— what is under them is not counted"
                )

    walk(result["children"], 2)
    if result.get("childrenHidden"):
        lines.append(f"  ... {result['childrenHidden']} more child(ren) not listed")
    if result.get("truncated"):
        # "at least", because `hidden` counts only the operators the walk had
        # already discovered and did not describe; whatever hangs below one of
        # those was never looked at and is not guessed at (see the handler's
        # `walk`). Measured live 2026-09-07 on a 36,144-operator project:
        # `network path=/ depth=16` reported hidden=66 while 31,144 operators
        # went undescribed. Phrased as a total, that number invites the reader
        # to think the reply is 66 operators short of complete — which is the
        # same wrong reading a silent cut produces, only with a number on it.
        # td_errors already says "at least" about its own remainder.
        lines.append(
            f"TRUNCATED: at least {result['hidden']} operator(s) were left "
            f"out — this reply describes at most {result.get('limit')} "
            f"operators, and at most {result.get('maxChildren')} children of "
            f"any one component. What hangs below a component that was cut "
            f"was never walked, so the real remainder is larger. Ask for a "
            f"subtree with a narrower `path` to see the rest."
        )
    if result.get("depthLimited"):
        lines.append(
            f"depth {result['depthLimited']} was reduced to "
            f"{result.get('depth')}, the deepest this tool walks."
        )
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
    position [x, y], optional connect [{"from": path, "index": 0}], and
    optional text for a DAT's contents — shader and script source belongs
    there, not in a separate td_exec, so it lands inside this undo block.
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

    `interval` is the gap between the two samples, in seconds, and is capped:
    this process sleeps through it and answers nothing else meanwhile.
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
def td_errors(path: str = "/project1") -> str:
    """Every operator at or under `path` currently reporting an error or warning.

    Node errors are shown as colours in the TouchDesigner UI and are otherwise
    invisible to you; check this after building something.

    The default is the project, not `/`, and asking for `/` is usually the
    wrong move: the walk is breadth-first and bounded, and on an open session
    TouchDesigner's own `/ui` and `/sys` are thousands of operators wide at
    the shallow levels, so the budget runs out before the walk reaches
    anything of yours. Point it at the component you built instead.
    """
    client = bridge()
    try:
        result = client.errors(path=path)
    except (BridgeUnavailable, BridgeError) as exc:
        return failure(exc)
    # Which subtree this covers, said before any verdict and in every branch:
    # "nothing is wrong" is a claim about one subtree, and a reader who does
    # not know which one reads it as a claim about the project.
    # A bridge that ignores `path` and walks from `/` is refused at connect by
    # the version check: `path` entered the method table at protocol 7, and
    # no bridge below the minimum this host accepts is let through. That
    # minimum is the version the host expects and nothing older — host and
    # bridge ride in one bundle, so there is no earlier bridge to stay
    # compatible with — and the check owns that number, not a copy of it here.
    # This used to be worked out here instead, from which
    # keys the reply did or did not carry — a convention the protocol never
    # stated, and one the version number is the right owner of.
    walked = result["root"]
    where = f"Checked {result.get('scanned')} operator(s) at and under {walked}."
    cut = ""
    if result.get("truncated"):
        cut = (
            f"\nTRUNCATED: the walk stopped after {result.get('limit')} "
            f"operator(s) at and under {walked}; at least "
            f"{result.get('notScanned')} more were not checked, and the real "
            f"remainder is larger — the children of the operators it never "
            f"visited were never counted either. Nothing is known about any "
            f"of them. Ask again with a narrower `path`."
        )
    if not result["count"]:
        return (
            _warn(client)
            + where
            + " No operators are reporting errors or warnings."
            + cut
        )
    lines = [f"{where} {result['count']} operator(s) reporting problems:"]
    for node in result["nodes"]:
        detail = node["errors"] or node["warnings"]
        kind = "ERROR" if node["errors"] else "warning"
        lines.append(f"  [{kind}] {node['path']} ({node['type']}): {detail}")
    return _warn(client) + "\n".join(lines) + cut


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
def td_project_text(file: str, path: str = "", max_bytes: int = 200_000) -> str:
    """Dump a whole .toe/.tox network as JSON, without TouchDesigner running.

    Use this when td_project_read's tree is not enough — when the answer needs
    every parameter, the wiring, the flags and the DAT code at once, for
    instance before rewriting a component or explaining what an unfamiliar
    project actually does.

    DAT text arrives as an array of lines rather than one escaped string, so a
    single changed line stays a single changed line; join the array with '\n'
    to get the file back byte for byte. Standard JSON otherwise.

    A network larger than `max_bytes` is refused rather than truncated: a cut
    dump is not parseable JSON, and `path` narrows the dump to one component.
    """
    from ..project import ExpandError, index_resolver, load_file
    from ..project.serialize import dumps, project_data

    try:
        project = load_file(file, resolver=index_resolver())
        data = project_data(project, path=path or None)
    except ExpandError as exc:
        return failure(exc)
    except LookupError as exc:
        return f"error: {exc}\n{hint('project_path_unknown')}"

    text = dumps(data)
    size = len(text.encode())
    if size > max_bytes:
        return (
            f"{project.source.name} at '{data['path']}' is "
            f"{data['operator_count']} operators and serialises to "
            f"{size} bytes, over the {max_bytes} byte limit.\n"
            f"{hint('project_text_too_large')}"
        )
    return text


@mcp.tool()
@guarded
def td_project_write(file: str, text: str, output: str) -> str:
    """Write an edited td_project_text dump back into a new .toe/.tox.

    The return leg of td_project_text: edit the JSON, hand the whole document
    back here, and get a file TouchDesigner opens — with no instance running.

    Three things to know before reaching for it, because each one is a silent
    wrong answer otherwise:

    - **`file` must still be the original the text came from.** The dump
      covers five of the forty-odd kinds of file a .toe holds; panel layouts,
      replicator settings and custom parameter definitions live in the others
      and are copied across from the original. There is no path from text
      alone to a .toe.
    - **`output` must not exist.** Repacking writes the file whole, and this
      tool will not overwrite anything of the user's. Write beside it and diff.
    - **Read the gaps in the reply.** Anything the text asked for that could
      not be written — a new operator, a changed operator type, a custom
      parameter page — is listed rather than approximated, and the built file
      does not say what the text said.

    Changing a parameter, its expression, a DAT's code, a table cell, the
    wiring, the flags, the placement or the colour all work, as does deleting
    an operator.
    """
    from pathlib import Path as _Path

    from ..project import ExpandError
    from ..project import index_resolver
    from ..project.rebuild import OutputExists, rebuild

    target = _Path(output).expanduser()
    try:
        changes = rebuild(file, text, target, resolver=index_resolver())
    except OutputExists as exc:
        # The refusal itself now comes from the layer both surfaces share;
        # what this adds is the recovery an agent gets and a shell does not.
        return f"{exc}\n{hint('project_write_output_exists')}"
    except ExpandError as exc:
        if "not a network dump" in str(exc):
            return f"error: {exc}\n{hint('project_write_not_a_dump')}"
        return failure(exc)
    except ValueError as exc:
        return f"error: {exc}\n{hint('project_write_not_a_dump')}"

    lines = [
        f"wrote {target}",
        f"{len(changes.files)} file(s) rewritten, {len(changes.deleted)} removed",
    ]
    if changes.estimated_flags:
        lines.append(
            f"{changes.estimated_flags} parameter flag word(s) were composed "
            f"from the text's keys rather than read off the original line; "
            f"bits other than expression and bind are lost on those"
        )
    if changes.gaps:
        # Not all of these are unwritten: a composed flags word is written but
        # approximate, and each line says which it is. A blanket "NOT written"
        # here would contradict the line under it.
        lines.append(
            f"{len(changes.gaps)} thing(s) the text asked for did not land as "
            f"asked — each line says whether it was written approximately or "
            f"not at all:"
        )
        lines += [f"  - {gap}" for gap in changes.gaps]
    return "\n".join(lines)


@mcp.tool()
@guarded
def td_variant_save(file: str, label: str, note: str = "", path: str = "") -> str:
    """Keep the current state of a .toe/.tox so it can be returned to and compared.

    Take one before trying a direction, another after, and td_variant_diff
    says exactly what the direction changed. Nothing of the user's is touched:
    the variant is the network text plus a byte copy of the file, kept under
    ~/.td-atlas/variants and grouped by the project's path.

    That copy is what makes a restore possible at all: the rebuild is a
    patcher, so the text alone cannot produce a .toe. It is also cheap —
    measured across the shipped palette, the copy adds a median 19% on top of
    the text and no measurable time.

    `label` may hold letters, digits, dot, dash and underscore. A label
    already in use is refused rather than overwritten. `path` narrows only the
    stored text to a subtree; the copy is always the whole file.
    """
    from ..project import index_resolver
    from ..project.variants import save

    variant = save(
        file, label, note=note, path=path or None, resolver=index_resolver()
    )
    return (
        f"saved '{variant.label}' of {variant.origin.name}\n"
        f"{variant.data['operator_count']} operators, "
        f"{variant.data['text_bytes']} B of text, "
        f"{variant.data['source_size']} B copy\n"
        f"text: {variant.text_path}"
    )


@mcp.tool()
@guarded
def td_variant_list(file: str = "") -> str:
    """List saved variants — of one .toe/.tox, or of every project that has any.

    Each line carries when it was saved, how large it is, and whether the
    original file has changed since. That last one is information rather than
    a warning: a restore reads the variant's own copy, so a changed original
    cannot affect it.
    """
    from ..project.variants import render_list, sources, variants

    if file:
        items = variants(file)
        from pathlib import Path as _Path

        head = f"{_Path(file).name}: {len(items)} variant(s)"
        return render_list(items, header=head)
    pairs = sources()
    if not pairs:
        return "no variants saved"
    return "\n".join(render_list(items, header=f"{origin}:") for origin, items in pairs)


@mcp.tool()
@guarded
def td_variant_restore(file: str, label: str, output: str) -> str:
    """Write a saved variant back out as a .toe/.tox file.

    A byte copy of what was saved, not a repack: nothing is collapsed, so
    TouchDesigner's toecollapse never runs and never moves a user's file aside
    to a .bkp1 name. `output` must not exist — hand back a new path and compare with
    td_project_diff rather than replacing anything in place. A directory as
    `output` keeps the name the file had when it was saved.
    """
    from ..project.variants import restore

    target, variant = restore(file, label, output)
    return (
        f"restored '{variant.label}' to {target}\n"
        f"the original at {variant.origin} is {variant.drift()} since the save"
    )


@mcp.tool()
@guarded
def td_variant_diff(
    file: str,
    before: str,
    after: str,
    show_moves: bool = False,
    include_text: bool = True,
) -> str:
    """Compare two saved variants of the same project.

    The same semantic comparison td_project_diff runs, aimed at two saved
    states instead of two files: added, removed, retyped, rewired and
    re-parameterised operators, plus a line diff of changed DAT code. Nodes
    that only moved are counted separately so they cannot bury a real change.
    """
    from ..project import index_resolver
    from ..project.variants import compare

    result, one, two = compare(
        file, before, after, include_text=include_text, resolver=index_resolver()
    )
    return (
        f"{one.label} -> {two.label}: {result.summary()}\n\n"
        f"{result.render(show_moves=show_moves)}"
    )


# `limit` is held equal to the CLI's `project grep --limit` by
# tests/test_cli_mcp_parity.py. The CLI had no flag at all and took
# `project/render.py`'s own 100 while this said 60, for no recorded reason.
@mcp.tool()
@guarded
def td_project_grep(file: str, pattern: str, limit: int = 100) -> str:
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
    position: list | None = None,
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
    six here. A created COMP is given a free spot in the parent network
    unless `position` names one — `[x, y]` in network units, the left and
    *bottom* edges of the tile; it is ignored when aiming at a COMP that
    already exists.

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
            position=position,
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
        "  NOT INITIALISED — the parameters are set but the extension is "
        "None. TouchDesigner reported this nowhere; the message below comes "
        "from re-evaluating the expression:"
    )
    lines.append(f"  {result['error']}")
    lines.append(
        f"  the code is in {result['dat']}: fix it and repeat this call, or "
        f"td_undo to remove the whole block."
    )
    lines.append(hint("extension_init_failed"))
    return _warn(client) + "\n".join(lines)


# -- notes in the network ----------------------------------------------------

@mcp.tool()
@guarded
def td_annotate(
    text: str,
    parent: str = "/project1",
    title: str = "",
    name: str = "",
    path: str = "",
    size: list | None = None,
    color: list | None = None,
    position: list | None = None,
    font_size: float = 0,
    mode: str = "",
    owner: str = "",
) -> str:
    """Leave a note in the network saying what you built and why.

    Reach for this at the end of a build, not as decoration: the network you
    made records *what* it does and nothing about *why*, and the person who
    opens the project next reads the network editor, not this conversation. An
    Annotate is a coloured box with your text in it, sitting beside the nodes it
    describes.

    `text` is the body (newlines work), `title` the bar along the top. Without
    `position` the note is placed where it does not cover anything, and without
    `size` it takes the default 382x288 network units — make it big enough to
    enclose the nodes it is about and `td_annotations` will report them as the
    ones it covers.

    Pass `path` (an existing note) instead of `parent` to rewrite that note
    rather than add another — the right call when you rerun a build. The reply
    always names the path the note actually has: TouchDesigner ignores the name
    given at creation, so `name` is applied afterwards and can be refused if a
    sibling holds it.

    `mode` is comment, networkbox or annotate: a comment is text only, a
    network box groups nodes without a title bar.
    """
    client = bridge()
    request: dict[str, Any] = {"owner": owner}
    if path:
        request["path"] = path
    else:
        request["parent"] = parent
    if text:
        request["text"] = text
    if title:
        request["title"] = title
    if name:
        request["name"] = name
    if size:
        request["size"] = size
    if color:
        request["color"] = color
    if position:
        request["position"] = position
    if font_size:
        request["font_size"] = font_size
    if mode:
        request["mode"] = mode
    try:
        result = client.call("annotate", **request)
    except (BridgeUnavailable, BridgeError) as exc:
        return failure(exc)

    verb = "wrote" if result.get("created") else "rewrote"
    lines = [f"{verb} the note at {result['path']}"]
    written = result.get("written") or {}
    if written:
        lines.append("  set: " + ", ".join(sorted(written)))
    if name and result.get("name") != name:
        lines.append(
            f"  note: it is named {result.get('name')!r}, not {name!r} — "
            f"address it by the path above."
        )
    return _warn(client) + "\n".join(lines)


@mcp.tool()
@guarded
def td_annotations(path: str = "/project1", depth: int = 8) -> str:
    """Read the notes in a network — including the ones a person left for you.

    Check this before building in a project you did not build. An artist can
    leave a brief as an Annotate beside the nodes it concerns, which is the
    natural place to put it and completely invisible to every other tool here:
    it is not an error, not a parameter and not a name.

    Each note comes back with the nodes its box sits over, so a note saying
    "this chain is the one to keep" can be matched to the chain. That list is
    geometric — the tiles whose centre falls inside the box — so a node the
    artist dragged half out of the box counts as outside.
    """
    client = bridge()
    try:
        result = client.call("annotations", path=path, depth=depth)
    except (BridgeUnavailable, BridgeError) as exc:
        return failure(exc)
    if not result["count"]:
        return (
            f"{_warn(client)}No note in {result['root']}.\n"
            f"{hint('no_annotations')}"
        )
    lines = [f"{result['count']} note(s) in {result['root']}:"]
    for note in result["annotations"]:
        if note.get("error"):
            lines.append(f"  {note['path']}: unreadable — {note['error']}")
            continue
        head = f"  {note['path']}"
        if note.get("title"):
            head += f" — {note['title']!r}"
        lines.append(head)
        body = note.get("text")
        if body:
            for line in str(body).splitlines():
                lines.append(f"      {line}")
        elif body is None:
            lines.append(
                "      (no text parameter — this Annotate has none of the "
                "default-setup parameters that carry text)"
            )
        if note.get("covers"):
            lines.append("      over: " + ", ".join(note["covers"]))
    return _warn(client) + "\n".join(lines)


# -- node flags --------------------------------------------------------------

@mcp.tool()
@guarded
def td_flags(path: str) -> str:
    """Read the flags that decide whether a node runs and what is visible.

    Run this when a network looks right and produces nothing. A bypassed
    operator, a COMP with its display or render flag off, and a COMP with
    cooking disabled are all invisible in a parameter dump and in td_network,
    and each of them makes a correct network output nothing — the kind of
    silent failure that costs an hour of re-reading parameters.

    `unavailable` names the flags this operator genuinely does not have, so you
    can tell "off" from "not a thing here". The clone *master* is a parameter
    rather than a flag, so it is not listed; `cloneImmune` is the flag half.
    """
    client = bridge()
    try:
        result = client.call("flags", path=path)
    except (BridgeUnavailable, BridgeError) as exc:
        return failure(exc)
    lines = []
    for target in result["ops"]:
        lines.append(f"{target['path']} ({target['type']}, {target['family']})")
        on = [name for name, value in target["flags"].items() if value]
        off = [name for name, value in target["flags"].items() if not value]
        lines.append("  on:  " + (", ".join(on) or "—"))
        lines.append("  off: " + (", ".join(off) or "—"))
        if target.get("unavailable"):
            lines.append(
                "  not on this operator: " + ", ".join(target["unavailable"])
            )
        if target.get("clones"):
            lines.append("  cloned from here: " + ", ".join(target["clones"]))
    return _warn(client) + "\n".join(lines)


@mcp.tool()
@guarded
def td_set_flags(path: str, flags: dict, owner: str = "") -> str:
    """Turn node flags on or off — bypass a node, hide it, stop it cooking.

    The write half of td_flags, and the way to bypass an operator without
    deleting it. `flags` is {"bypass": true} and the like.

    Every write is read back before this reports success, because the failure
    it exists to prevent is silence: TouchDesigner accepts `pickable` on COMPs
    only and refuses `allowCooking = false` outside a COMP, and a flag that
    exists but does nothing on this family would otherwise look like it landed.
    A refusal names the flag and the family, and nothing is left half-set.

    Pass the same `owner` you claimed the area with, or your own claim refuses
    this write.
    """
    unknown = sorted(name for name in flags if name not in NODE_FLAGS)
    if unknown:
        # Checked here as well as in the bridge so a typo costs no round trip,
        # and so the answer is the same whether or not TouchDesigner is up.
        return (
            f"error: no node flag is named {', '.join(repr(n) for n in unknown)}.\n"
            f"{hint('flag_unknown')}"
        )
    client = bridge()
    try:
        result = client.call("flags_set", path=path, flags=flags, owner=owner)
    except (BridgeUnavailable, BridgeError) as exc:
        return failure(exc)
    changes = ", ".join(
        f"{name}: {result['before'][name]} -> {value}"
        for name, value in result["applied"].items()
    )
    return f"{_warn(client)}{result['path']} ({result['type']}) {changes}"


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
