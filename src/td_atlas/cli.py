"""Command line entry point for td-atlas."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from . import config as cfg
from .atoms import probe as probe_mod
from .atoms.extract_static import extract
from .atoms.store import AtomStore
from .bridge.client import BridgeClient, BridgeError, BridgeUnavailable
from .install import InstallNotFound, discover

COMPONENT_DIR = Path(__file__).parent / "component"


def _say(message: str) -> None:
    print(message, file=sys.stderr)


# -- commands ---------------------------------------------------------------

def cmd_install(args: argparse.Namespace) -> int:
    """Stage the bridge sources in ~/.td-atlas and print the bootstrap line."""
    home = cfg.home()
    home.mkdir(parents=True, exist_ok=True)
    home.chmod(0o700)

    for name in ("bootstrap.py", "handler.py"):
        shutil.copyfile(COMPONENT_DIR / name, home / name)

    config = cfg.ensure_config(port=args.port, auth=not args.no_auth)
    line = f"exec(open('{cfg.bootstrap_path()}').read())"

    print("td-atlas bridge staged in", home)
    print()
    print("Paste this into TouchDesigner's textport (Alt+T), once per project:")
    print()
    print("    " + line)
    print()
    print(f"Port {config['port']}, auth", "disabled" if args.no_auth else "on")
    if not args.no_auth:
        print("A token was generated; the client reads it from the same file.")
    print("Re-running the same line upgrades an existing bridge in place.")

    if sys.platform == "darwin" and shutil.which("pbcopy"):
        try:
            subprocess.run(["pbcopy"], input=line.encode(), check=True)
            print("\n(copied to clipboard)")
        except subprocess.SubprocessError:
            pass
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    """Run the offline extraction pass."""
    try:
        install = discover(args.install_path)
    except InstallNotFound as exc:
        _say(f"error: {exc}")
        return 1

    _say(f"TouchDesigner {install.version} at {install.root}")
    store = AtomStore(args.db or cfg.db_path())
    store.create()
    with store.transaction():
        stats = extract(install, store, progress=_say)
        store.rebuild_fts()

    print(stats.summary())
    if stats.ops_without_doc:
        _say(
            f"note: {len(stats.ops_without_doc)} operator(s) have no wiki page: "
            + ", ".join(stats.ops_without_doc[:10])
        )
    print(f"index written to {store.path}")
    if not args.no_probe:
        _say("")
        _say("Run 'td-atlas probe' with TouchDesigner open to add defaults,")
        _say("ranges, menu options and connector counts.")
    return 0


def cmd_probe(args: argparse.Namespace) -> int:
    """Run the runtime introspection pass against a live TouchDesigner."""
    store = AtomStore(args.db or cfg.db_path())
    if not store.exists():
        _say("error: no index yet. Run 'td-atlas build' first.")
        return 1

    client = BridgeClient.discover(timeout=120.0)
    try:
        info = client.ping()
    except BridgeUnavailable as exc:
        _say(f"error: {exc}")
        return 1

    _say(f"connected to {info['product']} {info['build']} (project {info['project']})")
    stats = probe_mod.run(client, store, chunk_size=args.chunk, progress=_say)
    store.rebuild_fts()
    store.conn.commit()

    print(stats.summary())
    if stats.failures:
        _say("\ncould not instantiate:")
        for op_type, reason in sorted(stats.failures.items())[:20]:
            _say(f"  {op_type}: {reason}")
    if stats.new_types:
        _say(
            "\nundocumented types discovered: "
            + ", ".join(sorted(stats.new_types)[:20])
        )
    return 0


def cmd_reload(_args: argparse.Namespace) -> int:
    """Re-run the bootstrap through the bridge, upgrading it in place.

    The running bridge can replace its own handler, so upgrading does not send
    the user back to the textport.
    """
    home = cfg.home()
    for name in ("bootstrap.py", "handler.py"):
        shutil.copyfile(COMPONENT_DIR / name, home / name)

    client = BridgeClient.discover()
    try:
        result = client.exec(
            f"exec(open({str(cfg.bootstrap_path())!r}).read())"
        )
    except (BridgeUnavailable, BridgeError) as exc:
        _say(f"error: {exc}")
        if isinstance(exc, BridgeError) and exc.traceback:
            _say(exc.traceback)
        return 1
    sys.stdout.write(result.get("stdout") or "")
    print("bridge reloaded")
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    try:
        install = discover()
        print(f"TouchDesigner : {install.version} at {install.root}")
    except InstallNotFound as exc:
        print(f"TouchDesigner : not found ({exc})")

    store = AtomStore(cfg.db_path())
    if store.exists():
        stats = store.stats()
        version = store.get_meta("td_version", "?")
        runtime = store.get_meta("runtime_pass") or "not run"
        print(
            f"index         : {stats['ops']} ops, {stats['params']} params, "
            f"{stats['articles']} articles (build {version})"
        )
        print(
            f"runtime pass  : {runtime} "
            f"({stats['ops_probed']} ops probed)"
        )
    else:
        print("index         : not built (run 'td-atlas build')")

    session = cfg.load_session()
    client = BridgeClient.discover(timeout=3.0)
    try:
        info = client.ping()
        print(
            f"bridge        : connected on port {client.port} "
            f"(project '{info['project']}', {info['fps']} fps, "
            f"build {info['build']})"
        )
    except BridgeError as exc:
        # Reached TouchDesigner but it refused us — almost always a token that
        # no longer matches the one baked into the running bridge.
        print(
            f"bridge        : responding on port {client.port} but rejected "
            f"the request ({exc.type}: {exc.message}). Re-run the bootstrap "
            f"line in TouchDesigner to pick up the current token."
        )
    except BridgeUnavailable:
        if session or cfg.config_path().exists():
            print(
                f"bridge        : staged on port {client.port}, not responding. "
                f"Run the bootstrap line in TouchDesigner's textport."
            )
        else:
            print("bridge        : not installed (run 'td-atlas install')")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    store = AtomStore(args.db or cfg.db_path())
    if not store.exists():
        _say("error: no index. Run 'td-atlas build' first.")
        return 1
    rows = store.search_ops(args.query, limit=args.limit)
    if not rows:
        print("no matches")
        return 0
    for row in rows:
        summary = (row["summary"] or "").split(". ")[0][:100]
        print(f"{row['type']:<28} {row['family']:<5} {row['label']:<22} {summary}")
    return 0


def cmd_op(args: argparse.Namespace) -> int:
    store = AtomStore(args.db or cfg.db_path())
    if not store.exists():
        _say("error: no index. Run 'td-atlas build' first.")
        return 1
    row = store.conn.execute(
        "SELECT * FROM ops WHERE type = ?", (args.type,)
    ).fetchone()
    if row is None:
        _say(f"no operator type '{args.type}'")
        return 1

    print(f"{row['type']}  ({row['family']}) — {row['label']}")
    if row["summary"]:
        print(f"\n{row['summary']}")
    if row["probed"]:
        print(
            f"\ninputs: {row['min_inputs']}–{row['max_inputs']}, "
            f"outputs: {row['num_outputs']}"
        )
    else:
        print("\n(runtime pass not run: no defaults, ranges or menu options)")

    pars = store.parameters(args.type)
    if args.page:
        pars = [p for p in pars if (p["page"] or "").lower() == args.page.lower()]
    settable = [p for p in pars if p["settable"] and not p["hidden"]]
    groups = [p for p in pars if not p["settable"]]

    page = object()
    for par in settable:
        if par["page"] != page:
            page = par["page"]
            print(f"\n[{page or 'ungrouped'}]")
        bits = [par["style"] or "?"]
        if par["default"] is not None:
            bits.append(f"default={par['default']!r}")
        if par["menu_names"]:
            names = par["menu_names"]
            shown = ", ".join(names[:6]) + ("..." if len(names) > 6 else "")
            bits.append(f"menu=[{shown}]")
        elif par["is_number"]:
            lo, hi = par["norm_min"], par["norm_max"]
            if lo is not None and hi is not None:
                bits.append(f"slider={lo:g}..{hi:g}")
            if par["clamp_min"] or par["clamp_max"]:
                bits.append(
                    f"clamp={par['min_value'] if par['clamp_min'] else ''}"
                    f"..{par['max_value'] if par['clamp_max'] else ''}"
                )
        print(f"  {par['name']:<20} {par['label'] or '':<26} {' '.join(bits)}")

    if groups and args.groups:
        print("\n[documented parameter groups — set the members, not these]")
        for par in groups:
            members = [p["name"] for p in settable if p["group_name"] == par["name"]]
            print(
                f"  {par['name']:<20} {par['label'] or '':<26} "
                f"-> {', '.join(members) if members else '(no live members)'}"
            )
    return 0


def cmd_exec(args: argparse.Namespace) -> int:
    client = BridgeClient.discover()
    code = args.code
    if code == "-":
        code = sys.stdin.read()
    try:
        result = client.exec(code)
    except (BridgeUnavailable, BridgeError) as exc:
        _say(f"error: {exc}")
        if isinstance(exc, BridgeError) and exc.traceback:
            _say(exc.traceback)
        return 1
    if result.get("stdout"):
        sys.stdout.write(result["stdout"])
    if result.get("stderr"):
        sys.stderr.write(result["stderr"])
    if result.get("result") is not None:
        print(json.dumps(result["result"], indent=2))
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    client = BridgeClient.discover()
    try:
        data, meta = client.render(
            args.path, width=args.width, height=args.height
        )
    except (BridgeUnavailable, BridgeError) as exc:
        _say(f"error: {exc}")
        return 1
    out = Path(args.output)
    out.write_bytes(data)
    print(f"{meta['width']}x{meta['height']} -> {out} ({len(data)} bytes)")
    return 0


def cmd_project(args: argparse.Namespace) -> int:
    """Read, search, diff and repack .toe/.tox files without TouchDesigner."""
    from .project import ExpandError, collapse, expand, index_resolver, load_file
    from .project.diff import diff as diff_projects
    from .project.render import describe, grep, render_matches

    resolver = index_resolver()

    try:
        if args.action == "read":
            project = load_file(args.file, refresh=args.refresh, resolver=resolver)
            print(
                describe(
                    project,
                    path=args.path,
                    depth=args.depth,
                    params=args.params,
                )
            )
        elif args.action == "grep":
            project = load_file(args.file, resolver=resolver)
            matches = grep(project, args.pattern, regex=not args.fixed)
            print(render_matches(matches, args.pattern))
        elif args.action == "diff":
            before = load_file(args.file, resolver=resolver)
            after = load_file(args.other, resolver=resolver)
            result = diff_projects(before, after, include_text=not args.no_text)
            print(result.render(show_moves=args.moves))
            _say("\n" + result.summary())
        elif args.action == "expand":
            expansion = expand(args.file, refresh=args.refresh)
            print(expansion.root)
        elif args.action == "collapse":
            print(collapse(args.file, args.output))
        elif args.action == "scripts":
            project = load_file(args.file, resolver=resolver)
            target = Path(args.output)
            written = 0
            for node in project.scripts():
                # Mirror the node hierarchy so paths stay meaningful on disk.
                destination = target / node.path.lstrip("/")
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.with_suffix(_script_suffix(node)).write_text(
                    node.text or ""
                )
                written += 1
            print(f"wrote {written} script(s) to {target}")
    except ExpandError as exc:
        _say(f"error: {exc}")
        return 1
    except ValueError as exc:
        _say(f"error: {exc}")
        return 1
    return 0


def _script_suffix(node) -> str:
    """Guess a file extension so extracted DAT contents are syntax-highlighted."""
    text = node.text or ""
    if node.op_type == "glslmultiTOP" or "void main()" in text:
        return ".glsl"
    if "import " in text or "def " in text or "op(" in text:
        return ".py"
    return ".txt"


def cmd_mcp(_args: argparse.Namespace) -> int:
    from .mcp.server import main as mcp_main

    mcp_main()
    return 0


# -- argument parsing -------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="td-atlas",
        description="An atomised index of TouchDesigner plus a live bridge.",
    )
    parser.add_argument("--db", help="path to the atom index")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("install", help="stage the bridge and print the bootstrap line")
    p.add_argument("--port", type=int, default=None)
    p.add_argument("--no-auth", action="store_true", help="disable token auth")
    p.set_defaults(func=cmd_install)

    p = sub.add_parser("build", help="build the offline index from a TD install")
    p.add_argument("--install-path", help="TouchDesigner application directory")
    p.add_argument("--no-probe", action="store_true", help="suppress the probe hint")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("probe", help="add runtime facts from a running TD")
    p.add_argument("--chunk", type=int, default=40)
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser(
        "reload", help="re-stage and reload the bridge through itself"
    )
    p.set_defaults(func=cmd_reload)

    p = sub.add_parser("status", help="show install, index and bridge state")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("search", help="full-text search over operators")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=15)
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("op", help="show an operator's full schema")
    p.add_argument("type")
    p.add_argument(
        "--groups",
        action="store_true",
        help="also list documented parameter groups and their members",
    )
    p.add_argument("--page", help="only parameters on this page")
    p.set_defaults(func=cmd_op)

    p = sub.add_parser("exec", help="run Python inside TouchDesigner")
    p.add_argument("code", help="source, or - to read stdin")
    p.set_defaults(func=cmd_exec)

    p = sub.add_parser("render", help="save a TOP's image")
    p.add_argument("path")
    p.add_argument("-o", "--output", default="render.png")
    p.add_argument("--width", type=int)
    p.add_argument("--height", type=int)
    p.set_defaults(func=cmd_render)

    p = sub.add_parser(
        "project", help="read, search and diff .toe/.tox files offline"
    )
    p.set_defaults(func=cmd_project)
    actions = p.add_subparsers(dest="action", required=True)

    a = actions.add_parser("read", help="show a project's operator tree")
    a.add_argument("file")
    a.add_argument("--path", help="only this subtree, e.g. /project1")
    a.add_argument("--depth", type=int, default=2)
    a.add_argument("--params", action="store_true", help="include parameters")
    a.add_argument("--refresh", action="store_true", help="ignore the cache")

    a = actions.add_parser("grep", help="search the code inside a project's DATs")
    a.add_argument("file")
    a.add_argument("pattern")
    a.add_argument("--fixed", action="store_true", help="literal, not regex")

    a = actions.add_parser("diff", help="compare two projects semantically")
    a.add_argument("file")
    a.add_argument("other")
    a.add_argument("--moves", action="store_true", help="list moved-only nodes")
    a.add_argument("--no-text", action="store_true", help="skip DAT text diffs")

    a = actions.add_parser("expand", help="unpack to a directory and print its path")
    a.add_argument("file")
    a.add_argument("--refresh", action="store_true")

    a = actions.add_parser("collapse", help="repack an expanded directory")
    a.add_argument("file", help="the '<name>.tox.dir' directory")
    a.add_argument("-o", "--output", required=True)

    a = actions.add_parser("scripts", help="extract every DAT's contents to disk")
    a.add_argument("file")
    a.add_argument("-o", "--output", required=True)

    p = sub.add_parser("mcp", help="run the MCP server on stdio")
    p.set_defaults(func=cmd_mcp)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
