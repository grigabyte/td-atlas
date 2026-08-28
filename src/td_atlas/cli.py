"""Command line entry point for td-atlas."""

from __future__ import annotations

import argparse
import json
import os
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


# -- MCP client wiring --------------------------------------------------

def mcp_command() -> list[str]:
    """The argv that launches the MCP server, robust to cwd and activation.

    An MCP client starts the server with no shell profile sourced and no
    virtualenv activated, often from a working directory that has nothing to
    do with this project — so a relative path, a bare "td-atlas" relying on
    PATH, or a path into ".venv/bin" (which breaks the moment that venv is
    rebuilt or the interpreter it points at is removed) are all fragile.

    The absolute path to the *current* interpreter (``sys.executable``,
    unmodified) plus "-m td_atlas.cli mcp" survives both: it needs no
    activation and no PATH lookup. Do not resolve the symlink: a venv's
    `bin/python` is normally a symlink to a base interpreter, and CPython
    only finds that venv's `pyvenv.cfg` (and therefore its site-packages,
    where the editable install lives) by looking next to the path it was
    *invoked as* — not next to where the symlink points. Measured directly:
    launching via the venv path returns a working MCP `initialize` reply;
    launching the same command with the symlink resolved to the Homebrew
    base interpreter fails immediately with `ModuleNotFoundError: No module
    named 'td_atlas'`, because that interpreter never finds the venv's
    pyvenv.cfg and never sees its site-packages at all. So this string does
    not survive the base interpreter being deleted — nothing printed here
    could; that is `td-atlas status`'s job. Once this package is published,
    `uvx td-atlas mcp` becomes the sturdier choice (no local venv at all) —
    not offered yet because there is nothing to fetch.
    """
    return [sys.executable, "-m", "td_atlas.cli", "mcp"]


def mcp_connection_line(server_name: str = "td-atlas") -> str:
    return "claude mcp add " + server_name + " -- " + " ".join(mcp_command())


def write_mcp_json(directory: Path, server_name: str = "td-atlas") -> Path:
    """Add (or update) the td-atlas server entry in DIRECTORY/.mcp.json.

    Any other servers already listed, and any other top-level keys, are left
    untouched. Running this twice does not duplicate the entry — it just
    rewrites the same key.
    """
    path = directory / ".mcp.json"
    if path.exists():
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            raise ValueError(f"{path} exists but is not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"{path} exists but its top level is not a JSON object")
    else:
        data = {}

    servers = data.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise ValueError(f"{path}: 'mcpServers' exists but is not a JSON object")

    command = mcp_command()
    servers[server_name] = {"command": command[0], "args": command[1:]}

    path.write_text(json.dumps(data, indent=2) + "\n")
    return path


def broken_env_diagnosis(prefix: Path) -> str | None:
    """Explain a virtualenv whose base interpreter has gone missing.

    PREFIX is normally `sys.prefix`: the root of the environment the running
    interpreter belongs to. A venv created by `uv venv`/`python -m venv`
    records the interpreter it was built from in `pyvenv.cfg`'s `home` key;
    if that directory (or the python binary inside it) no longer exists, the
    venv's own `python` symlink is dangling and every invocation of this
    package from it will fail with a cryptic ModuleNotFoundError rather than
    naming the real cause. Returns None when there is nothing wrong, or no
    pyvenv.cfg to check (a system interpreter, or a `uv tool run` environment
    — absence of a venv is not itself a fault).
    """
    cfg_path = prefix / "pyvenv.cfg"
    if not cfg_path.exists():
        return None
    try:
        text = cfg_path.read_text()
    except OSError:
        return None

    home = None
    for line in text.splitlines():
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() == "home":
            home = value.strip()
            break
    if home is None:
        return None

    home_dir = Path(home)
    if home_dir.exists():
        return None

    return (
        f"the base interpreter this environment was built from is gone "
        f"({home_dir} does not exist) — {prefix} is a broken virtualenv. "
        "Recreate it and reinstall: `uv venv && uv pip install -e .`"
    )


def broken_editable_install_diagnosis(
    site_packages: Path,
    sys_path: list[str] | None = None,
    package: str = "td_atlas",
    env: dict[str, str] | None = None,
) -> str | None:
    """Catch a `.pth`-based editable install whose path never reaches sys.path.

    Measured live on this machine (uv 0.12.3, Python 3.14.6, a `uv venv`
    virtualenv): `site-packages/_editable_impl_td_atlas.pth` correctly names
    the project's `src` directory, yet a fresh interpreter launched from that
    same venv raises `ModuleNotFoundError: No module named 'td_atlas'` —
    `import td_atlas` fails even directly at the REPL. `sys.path` printed
    from that interpreter does not contain the directory the `.pth` file
    names. `site-packages` also holds a `_virtualenv.pth` (`import
    _virtualenv`), sorted after the editable one; whether that import is
    what drops the path again during site processing is not confirmed here —
    that would take instrumenting CPython's `site` module, which this task
    did not do. What is confirmed, and is what this check tests: the `.pth`
    file exists, names a real directory, and that directory is absent from
    `sys.path`.

    This is exactly the failure an MCP client hits silently: it launches the
    server fresh, with no PYTHONPATH and a working directory that is not
    this project's, and gets a process that exits on
    ModuleNotFoundError before it can say anything coherent. This check
    only fires when it can actually inspect a live discrepancy — a process
    that cannot import the package at all cannot run this check on itself.

    One trap this specifically guards against: the workaround for the bug
    (`PYTHONPATH=<src>`) puts the *same* directory the `.pth` names onto
    `sys.path`, which would make a naive "is it on sys.path" check report
    all-clear on the one invocation where the underlying `.pth` wiring is
    still broken. So a target found on `sys.path` only because it came from
    `PYTHONPATH` still gets flagged — it proves the `.pth` mechanism itself
    is not doing the job, even though this particular process happens to
    work around it.
    """
    if not site_packages.is_dir():
        return None
    pth_files = sorted(site_packages.glob(f"*{package}*.pth"))
    if not pth_files:
        return None

    active = sys.path if sys_path is None else sys_path
    active_resolved = {str(Path(p).resolve()) for p in active if p}

    active_env = os.environ if env is None else env
    env_path = active_env.get("PYTHONPATH", "")
    pythonpath_resolved = {
        str(Path(p).resolve()) for p in env_path.split(os.pathsep) if p
    }

    for pth in pth_files:
        try:
            lines = pth.read_text().splitlines()
        except OSError:
            continue
        for raw in lines:
            line = raw.strip()
            if not line or line.startswith("#") or line.startswith("import "):
                continue
            target = Path(line)
            if not target.is_absolute():
                target = site_packages / target
            if not target.exists():
                continue
            resolved_target = str(target.resolve())
            if resolved_target in pythonpath_resolved:
                return (
                    f"{pth} declares {target}, and it is only importable "
                    f"right now because PYTHONPATH puts it on sys.path "
                    f"directly — the .pth-based editable install of "
                    f"'{package}' did not take effect on its own. A launch "
                    f"without that PYTHONPATH set (as an MCP client does) "
                    f"will raise ModuleNotFoundError. Reinstall to force "
                    f"the .pth to be rewritten: `uv pip install -e . "
                    f"--reinstall`; if that does not hold, the cause is "
                    f"upstream in how this environment processes site "
                    f".pth files, not in td-atlas itself."
                )
            if resolved_target in active_resolved:
                return None  # this .pth entry is actually wired in
            return (
                f"{pth} declares {target}, but that directory is not on "
                f"sys.path in this process — the editable install of "
                f"'{package}' did not take effect; running it from a "
                f"different working directory or with a different launcher "
                f"(as an MCP client does) will raise ModuleNotFoundError. "
                "Reinstall to force the .pth to be rewritten: "
                "`uv pip install -e . --reinstall`; if that does not hold, "
                "the cause is upstream in how this environment processes "
                "site .pth files, not in td-atlas itself."
            )
    return None


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

    print()
    print("To use td-atlas as an MCP server, run:")
    print()
    print("    " + mcp_connection_line())
    print()

    if args.write_mcp_json:
        target = Path(args.write_mcp_json)
        try:
            written = write_mcp_json(target)
        except ValueError as exc:
            _say(f"error: {exc}")
            return 1
        print(f"wrote the td-atlas MCP server entry to {written}")
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


# -- picking which TouchDesigner to talk to ---------------------------------

def _client(args: argparse.Namespace, timeout: float = 30.0) -> BridgeClient | None:
    """The bridge this invocation is aimed at, or None after reporting why not.

    `--port` and `--project` are global flags, so they sit before the
    subcommand: `td-atlas --project Rehearsal exec "print(1)"`.
    """
    try:
        client = BridgeClient.discover(
            timeout=timeout,
            port=getattr(args, "port", None),
            project=getattr(args, "project", None),
        )
    except cfg.InstanceSelectionError as exc:
        _say(f"error: {exc}")
        return None
    if client.ambiguity_warning:
        _say(f"warning: {client.ambiguity_warning}")
    return client


def _age(seconds: float) -> str:
    if seconds == float("inf"):
        return "never"
    if seconds < 60:
        return f"{int(seconds)}s ago"
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    return f"{int(seconds // 3600)}h ago"


def cmd_instances(_args: argparse.Namespace) -> int:
    """List the TouchDesigner instances that have declared a bridge."""
    records = cfg.read_instances()
    dead = [i for i in records if not i.alive]
    live = [i for i in records if i.alive]

    session = cfg.load_session() or {}
    default_port = int(session.get("port") or cfg.load_config().get("port") or cfg.DEFAULT_PORT)

    if not live:
        print("No running TouchDesigner has registered a bridge.")
        print("Start one and run 'td-atlas install' for the bootstrap line.")
    else:
        word = "instance" if len(live) == 1 else "instances"
        print(f"{len(live)} running {word}:")
        print()
        width = max(len(i.label) for i in live)
        for record in live:
            default = "   (default)" if record.port == default_port else ""
            head = f"  --port {record.port}   "
            print(
                f"{head}{record.label:<{width}}"
                f"   build {record.build or '?'}"
                f"   seen {_age(record.age)}{default}"
            )
            print(
                f"{' ' * len(head)}{record.project_path or '(unsaved project)'}"
                f"   pid {record.pid}"
                f"   {record.component or '?'}"
                f"   protocol {record.protocol}"
            )
        print()
        print("Target one with a global flag, before the subcommand:")
        example = live[-1]
        stem = Path(example.project or "project").stem or str(example.port)
        print(f"  td-atlas --project {stem} status")
        print(f"  td-atlas --port {example.port} exec \"print(project.name)\"")

    for record in dead:
        # What was observed, not what it implies: this line used to announce
        # "pid N is gone" about processes that were running perfectly well.
        where = "" if f"port {record.port}" in record.dead_reason else f" (port {record.port})"
        _say(f"{record.dead_reason}, so the record for {record.label}{where} was removed.")
    return 0


def cmd_probe(args: argparse.Namespace) -> int:
    """Run the runtime introspection pass against a live TouchDesigner."""
    store = AtomStore(args.db or cfg.db_path())
    if not store.exists():
        _say("error: no index yet. Run 'td-atlas build' first.")
        return 1

    client = _client(args, timeout=120.0)
    if client is None:
        return 1
    try:
        info = client.ping()
    except BridgeUnavailable as exc:
        _say(f"error: {exc}")
        return 1
    if client.version_warning:
        _say(f"warning: {client.version_warning}")

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


def cmd_reload(args: argparse.Namespace) -> int:
    """Re-run the bootstrap through the bridge, upgrading it in place.

    The running bridge can replace its own handler, so upgrading does not send
    the user back to the textport.
    """
    home = cfg.home()
    for name in ("bootstrap.py", "handler.py"):
        shutil.copyfile(COMPONENT_DIR / name, home / name)

    client = _client(args)
    if client is None:
        return 1
    try:
        result = client.exec(
            f"exec(open({str(cfg.bootstrap_path())!r}).read())"
        )
    except (BridgeUnavailable, BridgeError) as exc:
        _say(f"error: {exc}")
        if isinstance(exc, BridgeError) and exc.traceback:
            _say(exc.traceback)
        return 1
    if client.version_warning:
        _say(f"warning: {client.version_warning}")
    sys.stdout.write(result.get("stdout") or "")
    print("bridge reloaded")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    import sysconfig

    diagnosis = broken_env_diagnosis(Path(sys.prefix))
    if diagnosis is None:
        site_packages = Path(sysconfig.get_path("purelib"))
        diagnosis = broken_editable_install_diagnosis(site_packages)
    if diagnosis:
        print(f"environment   : broken — {diagnosis}")
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
    client = _client(args, timeout=3.0)
    if client is None:
        return 1
    try:
        info = client.ping()
        print(
            f"bridge        : connected on port {client.port} "
            f"(project '{info['project']}', {info['fps']} fps, "
            f"build {info['build']})"
        )
        if client.version_warning:
            _say(f"warning: {client.version_warning}")
    except BridgeError as exc:
        # Reached TouchDesigner but it refused us — almost always a token that
        # no longer matches the one baked into the running bridge.
        print(
            f"bridge        : responding on port {client.port} but rejected "
            f"the request ({exc.type}: {exc.message}). Re-run the bootstrap "
            f"line in TouchDesigner to pick up the current token."
        )
    except BridgeUnavailable as exc:
        if session or cfg.config_path().exists():
            # `exc` already carries the exact fix — a plain connectivity
            # problem or, when the bridge's protocol version is out of
            # range, the precise upgrade instruction for that case.
            print(f"bridge        : {exc}")
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
    client = _client(args)
    if client is None:
        return 1
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
    client = _client(args)
    if client is None:
        return 1
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


def cmd_release_tox(args: argparse.Namespace) -> int:
    """Build the bridge as a .tox the user can drag into a network."""
    from .project import ExpandError
    from .project.release import DEFAULT_OUTPUT, build_tox

    try:
        path = build_tox(args.output or DEFAULT_OUTPUT)
    except ExpandError as exc:
        _say(str(exc))
        return 1

    config = cfg.load_config()
    port = config.get("port", cfg.DEFAULT_PORT)
    print("Bridge component written to", path.resolve())
    print()
    print(f"Drag it into a TouchDesigner network. The Web Server DAT binds "
          f"port {port}.")
    print()
    if config.get("token"):
        print("No token is baked into the .tox — there is nothing to bake it into,")
        print("one file goes to every machine. The handler reads the token itself")
        print("when the server starts, from")
        print(f"  {cfg.config_path()}")
        print("and refuses any request that does not carry it.")
    else:
        print("The config the handler reads at server start holds no token yet:")
        print(f"  {cfg.config_path()}")
        print("so the bridge will accept any caller on this machine, and says so")
        print("in the textport when it starts. Run `td-atlas install` to mint one —")
        print("the handler picks it up at the next server start, with no rebuild.")
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
    # Global, so they precede the subcommand: `td-atlas --port 9978 exec ...`.
    # Every bridge-facing command reads them through `_client()`.
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="talk to the bridge on this port (see 'td-atlas instances')",
    )
    parser.add_argument(
        "--project",
        default=None,
        help="talk to the running project matching this piece of its name or path",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("install", help="stage the bridge and print the bootstrap line")
    p.add_argument("--port", type=int, default=None)
    p.add_argument("--no-auth", action="store_true", help="disable token auth")
    p.add_argument(
        "--write-mcp-json",
        metavar="DIR",
        help="also add the td-atlas server to DIR/.mcp.json (merged, not overwritten)",
    )
    p.set_defaults(func=cmd_install)

    p = sub.add_parser(
        "release-tox", help="build the bridge as a drag-and-drop .tox"
    )
    p.add_argument(
        "-o", "--output", default=None,
        help="where to write the component (default release/TdAtlas.tox)",
    )
    p.set_defaults(func=cmd_release_tox)

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

    p = sub.add_parser(
        "instances", help="list the running TouchDesigner instances and how to target them"
    )
    p.set_defaults(func=cmd_instances)

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
