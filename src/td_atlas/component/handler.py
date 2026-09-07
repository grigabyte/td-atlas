"""The td-atlas RPC handler. This module runs *inside* TouchDesigner.

It is installed as the text of a Text DAT wired to a Web Server DAT's Callbacks
parameter, so it executes in TouchDesigner's embedded Python 3.11 with the `td`
globals (op, ops, root, project, ui, ...) already in scope. It must not import
anything that is not in TouchDesigner's standard library.

Every request is handled on TouchDesigner's main thread during a cook, so a
handler that blocks stalls the entire application. Keep work bounded.
"""

import base64
import hmac
import io
import json
import os
import time
import traceback
from contextlib import redirect_stderr, redirect_stdout

# The method table below *is* the wire, and so is each method's parameter set
# and the shape the host reads back, so any change to those is a protocol
# change even when no method was added or removed. The number left 5 because
# `perf`, `save` and `par_get` were removed while it still said 5, which left
# one version naming two different method sets; it left 6 because `errors`
# gained a `path` parameter, and a bridge laid down before that ignores the
# argument in silence — it answers about the whole root while reporting the
# same number, which no reader can tell from a correct answer.
# `tests/test_protocol_fingerprint.py` reddens when the table moves without
# this number; the rule is written down in `AGENTS.md`.
PROTOCOL_VERSION = 7

# The shared secret, read from ~/.td-atlas/config.json when the server starts —
# see _load_token(). It is not baked into this text: a released .tox is one file
# handed to every machine, and there is no install step to bake anything into.
# Empty disables authentication, and _load_token() says so out loud.
AUTH_TOKEN = ""
_TOKEN_LOADED = False

_MAX_REPR = 4000
_MAX_CHILDREN = 2000

# How to read every "a-b ms" in the two ceiling blocks below: it is the
# fastest and the slowest of **three runs**, and not a limit. A fourth run
# outside the range is not excluded by it — re-measuring 2026-09-07 gave
# 12.2-15.9 ms and 62.3-77.1 ms where 13-15 and 65-77 stand recorded below,
# which is what three samples do and what a bound would not. Anything that
# has to hold as a ceiling is written as one, in words.

# Depth and node ceilings for a network walk. Measured on the largest
# component TouchDesigner ships, kantanMapper.tox: 4080 operators, widest
# parent 143 children, nesting 11 levels below its own root. The first two
# were counted offline 2026-09-06 by expanding the .tox and walking the tree,
# and both were confirmed live 2026-09-07 by loading the component into a
# running instance and counting there. The nesting was not: the offline count
# said 12 levels and the live count says 11, by longest operator path
# (.../ui/main/gadgets/layers/treebrowser/tree/table/local/macros/row_type).
# _MAX_DEPTH is that 11 plus room for the component sitting a few levels
# inside a project; _MAX_NETWORK_NODES sits above the 4080 so the largest
# shipped component still comes back whole, and bounds a walk on anything
# bigger. Both cuts are reported in the reply: a partial network described as
# a whole one is the failure this prevents. What the bounded walk costs is
# measured too — 4079 operators described in 37-52 ms, and a full 5000-node
# walk in 44-91 ms.
_MAX_DEPTH = 16
_MAX_NETWORK_NODES = 5000

# The same ceiling for the two whole-subtree walks (errors, health_sample),
# for the same reason: 4080 measured operators in the largest shipped
# component, so 5000 leaves it whole. What it costs past that is measured
# rather than estimated, as of 2026-09-07 on build 2025.32460: against an
# open session of 36,144 operators — a small project, TouchDesigner's own
# /ui and /sys, and kantanMapper loaded for the measurement — a full
# 5000-node walk took 13-15 ms through `errors`
# and 65-77 ms through `health_sample` — three runs each, and both ranges
# widened by the 2026-09-07 re-measurement noted above. Timed on
# the host with perf_counter around the call, so both include HTTP and JSON
# and are an upper bound on the work done here. The estimate this replaces
# read 0.3 s, extrapolated from a text serialisation that does far more per
# operator; it was four to twenty times pessimistic.
#
# The reason for a ceiling survived that measurement, because the cost is
# linear and nothing bounds a project's size. Timed inside the handler the
# same day, without HTTP or JSON: the `errors` walk over all 32,063
# operators of that session (kantanMapper unloaded) took 45.7-50.7 ms, or
# about 1.5 us an operator — three dropped frames at 60 fps, and ten times
# that on a session ten times the size. At 5000 nodes the same walk is
# 6.9-9.6 ms, inside one frame. So the ceiling stays where it is; what
# changed 2026-09-07 is where `errors` starts walking from, which is what
# actually kept it out of the project (see m_errors). A ceiling of its own,
# raised above 5000 for `errors` alone, was measured and rejected: it is
# moot once the walk starts at the project, and it would fork a constant
# shared with `health_sample`, whose per-operator cost is an order of
# magnitude higher (59-81 ms of wall time for 4080 nodes).
_MAX_WALK_NODES = 5000

# One captured frame is a float32 RGBA array the size of the TOP:
# 1280x720 is 1280*720*4*4 = 14.7 MB, 1920x1080 is 33.2 MB, 3840x2160 is
# 132 MB (computed from the layout numpyArray() returns, not timed live).
# The buffer sits in component storage inside TouchDesigner's own process,
# which is why the cap is bytes and not frames: a 4K TOP reaches the same
# total in four frames that a 720p one reaches in thirty-five. The frame
# count is a second cap for the small-TOP case. The host asks for nine
# frames by default (bridge/filmstrip.py).
_MAX_CAPTURE_BYTES = 512 * 1024 * 1024
_MAX_CAPTURE_FRAMES = 64


# -- authentication ---------------------------------------------------------

def _home():
    """Where the host and this bridge meet; see src/td_atlas/config.py.

    Joined rather than expanded from "~/.td-atlas" in one piece:
    `os.path.expanduser` only substitutes the leading "~" and leaves the rest
    of the string alone, so on Windows that form returned
    "C:\\Users\\a/.td-atlas" — the same directory the host's `config.home()`
    means, spelled in two separators at once. Measured in CI (run 34111353871,
    windows-latest, 2026-09-07) as the two sides disagreeing over the string
    while agreeing about the directory. Nothing broke on it yet, and nothing
    should get the chance: this path is printed in warnings and compared with
    the host's answer by `td-atlas doctor`.
    """
    return os.environ.get("TD_ATLAS_HOME") or os.path.join(
        os.path.expanduser("~"), ".td-atlas"
    )


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


def _token_matches(supplied):
    """Constant-time comparison of the supplied token against AUTH_TOKEN.

    `!=` on strings returns as soon as two bytes differ, so how long the
    rejection took says how much of the token the caller already had. Nothing
    here narrows who can ask: `bootstrap.py` sets the Web Server DAT's port
    and callbacks and never its listen address, so which interfaces it binds
    is TouchDesigner's default and has not been measured. Treat the caller as
    unknown until it is.

    Both sides are encoded first because `hmac.compare_digest` raises
    TypeError on non-ASCII str, and a header is whatever the caller sent; a
    non-str header value is simply not a token, and is rejected without
    reaching the comparison.
    """
    if not isinstance(supplied, str):
        return False
    return hmac.compare_digest(
        supplied.encode("utf-8", "surrogateescape"),
        AUTH_TOKEN.encode("utf-8", "surrogateescape"),
    )


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


def _project_path(folder, name):
    """`project.folder` + `project.name`, in the separator the folder uses.

    TouchDesigner hands out `project.folder` in its own notation, and what
    that notation is on Windows is not something this project has measured —
    no TouchDesigner on Windows has ever run this code. `os.path.join` answers
    for it regardless: on Windows it inserts a backslash, so a folder reported
    as "C:/Users/a/td" became "C:/Users/a/td\\Vessel.toe" (measured in CI, run
    34111353871, windows-latest, 2026-09-07, against a POSIX-shaped folder).
    That record is what the host prints and what `--project` matches a
    fragment against, so one separator in the middle of another notation costs
    the artist a match on a path they can see.

    Reusing whichever separator the folder already carries keeps the record in
    one notation without this side having to know which one TouchDesigner
    picked. A folder with neither gets "/", the notation TouchDesigner uses
    for everything else it reports.
    """
    folder = str(folder or "")
    if not folder:
        return name
    separator = "\\" if "\\" in folder and "/" not in folder else "/"
    return folder.rstrip("/\\") + separator + name


def _instance_record(port, component_path):
    """What this bridge claims about itself. Never the token.

    The token lives in one 0600 file; copying it into a directory that exists
    to be read by every tool on the machine would spread a bearer credential
    for no gain — the host reads config.json itself.
    """
    return {
        "port": int(port),
        "project": project.name,
        "projectPath": _project_path(project.folder, project.name),
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
    """Withdraw the record on an orderly stop, so nothing outlives the bridge.

    A failure here is not fatal — the host treats a record whose pid is gone as
    stale — but it is worth saying, for the same reason `_write_instance` says
    its own: a leftover record makes `td_instances` list a bridge that is not
    there, and silence here is why nobody would think to look at the directory.
    """
    try:
        os.remove(_instance_path(int(dat.par.port.eval())))
    except FileNotFoundError:
        # Never published, or already withdrawn. Nothing to report.
        pass
    except Exception as exc:
        print("[td-atlas] could not remove the instance record: %s" % exc)


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


# -- scope claims -----------------------------------------------------------

# Cooperative claims on subtrees of the network, so two agents editing the same
# project stop overwriting each other in silence.
#
# The claims live inside TouchDesigner and not on the host: agents are separate
# processes (often separate MCP servers) and the bridge is the one thing they
# demonstrably share.
#
# They live in a Table DAT inside the bridge COMP, and NOT in a module-level
# dict, which is where they started and why they vanished. Measured on a live
# 2025.32460: this module's body is re-executed while the module object is
# reused — `id(globals())` before and after one health_sample request came back
# 5450312064 then 5451093184 — so every module-level variable is rebuilt and a
# claim held there disappears with no expiry and no trace. A claim that
# silently evaporates is worse than no claims at all: its owner keeps writing
# believing the area is guarded, and the next agent walks in too.
#
# Carrier chosen by measurement, not preference. Same live build, N=300 per
# figure, three runs, cost of one read (the guard runs on every network write)
# and one write (claim and release only):
#
#   Table DAT              read 2.2-3.0 us   write 3.2-3.7 us
#   component storage      read 0.32-0.38 us write 0.29-0.34 us
#   JSON file in ~/.td-atlas  read 16-24 us  write 124-200 us
#   module dict (the bug)  read 0.035 us     - does not survive
#
# All three survive the re-execution (verified by the same probe). At 0.02% of
# a 16.7 ms frame the Table DAT's cost is not a consideration, which leaves
# what the carriers differ in: the table is visible in the network, so a claim
# that misbehaves can be looked at instead of investigated through the bridge —
# which is what this defect cost. Component storage is ten times cheaper and
# invisible; the file is fifty times dearer to read, writes 124-200 us into the
# frame, and would need the port and pid plumbed into method bodies that are
# handed neither.
#
# Rows carry the writing process's pid and are ignored unless it matches: a
# table saved into a .toe must not come back as a claim held by a process that
# no longer exists, and the next claim, release or listing deletes those rows
# so the board a human reads never shows a claim that does not count.
#
# This is an agreement between agents, not a permission system. Nothing here
# constrains a human editing the same nodes by hand, and an agent that never
# sends an owner is never stopped by its own claim — see _caller_owner.
SCOPE_TABLE = "tdatlas_scopes"
_SCOPE_HEADER = ("path", "owner", "claimed", "expires", "pid")

# Every claim expires. An agent that crashes between claim and release must not
# park a subtree for the rest of the session, and there is nobody to notice that
# it has: expiry is the only cleanup that does not depend on the claimant coming
# back.
DEFAULT_SCOPE_TTL = 600.0
# A day, as a typo guard: a claim is minutes of work, and `ttl=1e9` from a bad
# unit conversion would otherwise be indistinguishable from "forever".
MAX_SCOPE_TTL = 86400.0


class ScopeHeld(Exception):
    """Raised when a write, or a claim, collides with another owner's claim.

    Surfaces to the caller as error type 'ScopeHeld', which is the signal to
    coordinate rather than retry — a retry loop will not outlast a live claim.
    """


def _normalise_scope_path(path):
    """An operator path in one comparable form: absolute, no trailing slash.

    Exists so '/project1/audio/', '/project1//audio' and '/project1/audio' are
    one claim rather than three, since a claim that can be spelled three ways
    stops nothing.
    """
    if not isinstance(path, str) or not path.strip():
        raise ValueError("a scope path is required, for example '/project1/audio'")
    text = path.strip()
    if not text.startswith("/"):
        raise ValueError(
            "scope paths are absolute: '%s' has no leading '/'" % path
        )
    parts = [part for part in text.split("/") if part]
    return "/" + "/".join(parts)


def _normalise_owner(owner):
    """The claimant's name, or a refusal. Empty owners are rejected on purpose.

    An empty owner would compare equal to the absent owner on an unattributed
    write, which would let any caller walk through every claim.
    """
    if not isinstance(owner, str) or not owner.strip():
        raise ValueError(
            "an owner name is required — any string that identifies this agent "
            "or session, so a second agent can be told who holds the scope"
        )
    name = owner.strip()
    # Tabs and newlines are the Table DAT's own cell and row separators; an
    # owner carrying one would come back as a different name, or as two.
    if any(character in name for character in "\t\r\n"):
        raise ValueError(
            "an owner name cannot contain tabs or newlines, got %r" % (owner,)
        )
    return name


def _scope_ttl(ttl):
    """Seconds a claim should last, defaulting rather than lasting forever."""
    if ttl is None or ttl == "":
        return DEFAULT_SCOPE_TTL
    try:
        seconds = float(ttl)
    except (TypeError, ValueError):
        raise ValueError("ttl must be a number of seconds, got %r" % (ttl,))
    # `not seconds > 0` also rejects NaN, which would otherwise make every
    # comparison against `expires` false and the claim invisible but present.
    if not seconds > 0:
        raise ValueError("ttl must be a positive number of seconds, got %r" % (ttl,))
    if seconds > MAX_SCOPE_TTL:
        raise ValueError(
            "ttl of %g s exceeds the %g s ceiling; claim for the work in hand "
            "and renew it if it runs long" % (seconds, MAX_SCOPE_TTL)
        )
    return seconds


def _scope_contains(scope, path):
    """Whether `path` lies at or below `scope`. Both already normalised.

    The separator in the prefix test is what makes '/project1/audio2' fall
    outside '/project1/audio' — a plain startswith reports it as inside, and
    would hand one agent a veto over its neighbour's network.
    """
    if scope == "/":
        return True
    return path == scope or path.startswith(scope + "/")


def _scopes_overlap(one, other):
    """Whether two subtrees intersect — either contains the other."""
    return _scope_contains(one, other) or _scope_contains(other, one)


def _scope_alive(claim, now):
    return float(claim.get("expires", 0.0)) > now


def _active_scopes(claims, now):
    """The claims still in force at `now`, oldest path first. Pure; no pruning."""
    return [
        claims[path]
        for path in sorted(claims)
        if _scope_alive(claims[path], now)
    ]


def _prune_scopes(claims, now):
    """Drop expired claims. Returns the paths dropped, for the caller to report."""
    dead = [path for path in sorted(claims) if not _scope_alive(claims[path], now)]
    for path in dead:
        del claims[path]
    return dead


def _blocking_claim(claims, path, owner, now):
    """The claim that stops `owner` writing to `path`, or None.

    Only claims held by *another* owner block, and only claims that cover the
    path — a claim below it (a colleague holding '/project1/audio/eq1') does not
    stop a write to its parent node itself, which is a deliberate reading of
    "inside": the guard answers "is this node in someone else's area", not "does
    someone hold anything under here".
    """
    target = _normalise_scope_path(path)
    for claim in _active_scopes(claims, now):
        if claim["owner"] == owner:
            continue
        if _scope_contains(claim["path"], target):
            return claim
    return None


def _overlapping_claim(claims, path, owner, now):
    """The claim that stops `owner` claiming `path`, or None.

    Claiming uses overlap rather than containment: two agents holding
    '/project1/audio' and '/project1/audio/eq1' would each believe the eq is
    theirs alone.
    """
    target = _normalise_scope_path(path)
    for claim in _active_scopes(claims, now):
        if claim["owner"] == owner:
            continue
        if _scopes_overlap(claim["path"], target):
            return claim
    return None


def _stamp(when):
    """A wall-clock moment an agent can compare with its own clock.

    Claims are timed on time.time() rather than the monotonic clock the
    instance registry uses, because their times are read by a *different*
    process than the one that set them; a monotonic reading means nothing
    there. The cost is that a system clock stepped backwards extends live
    claims, which is bounded by MAX_SCOPE_TTL and cheaper than unreadable
    timestamps.

    Never raises. `localtime` rejects a value the platform cannot represent
    (an OSError for 1e18, on macOS), and this is called from the refusal text
    inside the write guard — where a raise would turn one hand-edited cell in
    the claim table into a refusal of every edit in the project.
    """
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(when))
    except (OSError, OverflowError, ValueError):
        return "an unrepresentable time (%r)" % (when,)


def _claim_view(claim, now):
    """One claim as reported: both raw seconds and something readable."""
    return {
        "path": claim["path"],
        "owner": claim["owner"],
        "claimed": claim["claimed"],
        "claimedAt": _stamp(claim["claimed"]),
        "expires": claim["expires"],
        "expiresAt": _stamp(claim["expires"]),
        "expiresIn": round(claim["expires"] - now, 1),
    }


def _scope_refusal(claim, path, owner, now):
    """The refusal text: who holds it, since when, until when, what to do."""
    left = max(0.0, claim["expires"] - now)
    who = "'%s'" % owner if owner else "an unnamed caller"
    return (
        "%s is inside '%s', claimed by '%s' from %s until %s (%d s left). "
        "The write from %s was refused. Either work outside that subtree, or "
        "send owner='%s' if that claim is yours, or wait for it to expire, or "
        "ask its owner to call release_scope. Claims are visible to everyone "
        "through the 'scopes' method."
        % (
            _normalise_scope_path(path),
            claim["path"],
            claim["owner"],
            _stamp(claim["claimed"]),
            _stamp(claim["expires"]),
            int(left),
            who,
            claim["owner"],
        )
    )


def _caller_owner(params):
    """Who this request says it is, or '' for an unattributed one.

    An unattributed write matches no claim, so it is refused inside any live
    claim rather than let through — including a claim its own author took out.
    That is the fail-safe direction, and the refusal says which owner to send.
    """
    owner = params.get("owner")
    return owner.strip() if isinstance(owner, str) else ""


def _parse_scope_rows(rows, pid):
    """Turn the table's cells into claims. Pure, and never raises.

    Rows written by another process are dropped: a table saved inside a .toe
    would otherwise come back after a restart as a claim held by a pid that is
    gone, which no expiry and no release would ever clear.

    A row that does not parse is skipped rather than reported, because this
    runs inside the write guard: raising on one hand-edited cell would refuse
    every edit in the project until somebody found the table.
    """
    claims = {}
    for row in rows:
        cells = [str(cell) for cell in row]
        if len(cells) < 5 or cells[0] == _SCOPE_HEADER[0]:
            continue
        try:
            path = _normalise_scope_path(cells[0])
            owner = _normalise_owner(cells[1])
            claimed = float(cells[2])
            expires = float(cells[3])
            row_pid = int(cells[4])
        except (ValueError, TypeError):
            continue
        if row_pid != pid:
            continue
        claims[path] = {
            "path": path,
            "owner": owner,
            "claimed": claimed,
            "expires": expires,
            "pid": row_pid,
        }
    return claims


def _format_scope_rows(claims, pid):
    """The header plus one row per claim, oldest path first. Pure.

    repr() rather than str() on the timestamps: the expiry a second agent is
    refused against has to be the one this one recorded, to the last digit.
    """
    rows = [list(_SCOPE_HEADER)]
    for path in sorted(claims):
        claim = claims[path]
        rows.append(
            [
                claim["path"],
                claim["owner"],
                repr(float(claim["claimed"])),
                repr(float(claim["expires"])),
                str(int(claim.get("pid", pid))),
            ]
        )
    return rows


def _scope_table(create=False):
    """The Table DAT holding the claims, or None when there are none yet.

    `create=False` on the read path so the guard never builds an operator: a
    project where nobody has claimed anything must cost the guard one lookup
    and nothing else.

    Returns None off the host too, where `me` does not exist: the module is
    imported there for PROTOCOL_VERSION and for testing the logic, and a
    network with no claims in it is the truthful answer. Only NameError is
    caught — a failure to reach a COMP that does exist must not be swallowed
    into "nothing is claimed".
    """
    try:
        holder = me.parent()
    except NameError:
        return None
    table = holder.op(SCOPE_TABLE)
    if table is None and create:
        table = holder.create(tableDAT, SCOPE_TABLE)
        table.clear()
        table.appendRow(list(_SCOPE_HEADER))
        # Parked out of the way of the DATs a person came here to read.
        table.nodeY = -300
    return table


def _read_scopes():
    """Every claim this process wrote, expired ones included. No writes."""
    table = _scope_table()
    if table is None:
        return {}
    return _parse_scope_rows(table.rows(), os.getpid())


def _store_scopes(claims):
    """Rewrite the table from `claims`, dropping whatever else was in it.

    Called only from claim, release and the listing — never from the guard,
    which would dirty a DAT on every edit to the network.
    """
    table = _scope_table(create=True)
    if table is None:
        raise RuntimeError(
            "no %s table and no bridge component to put it in — claims are "
            "kept inside TouchDesigner, so this only works through the bridge"
            % SCOPE_TABLE
        )
    table.clear()
    for row in _format_scope_rows(claims, os.getpid()):
        table.appendRow(row)
    return table


def _guard_scopes(params, *paths):
    """Refuse a write that lands inside another owner's claim.

    Runs before the paths are resolved, so a refusal costs no operator lookup
    and reads the same whether or not the target exists. A path that is not
    absolute is skipped: resolving it needs TouchDesigner's own relative-path
    rules, and guessing at it would either block writes that are fine or claim
    a check it did not make.

    Read-only by design. Expired claims are filtered in memory here and swept
    from the table by the next claim, release or listing, so a network edit
    never pays for a table write.
    """
    claims = _read_scopes()
    if not claims:
        return
    now = time.time()
    owner = _caller_owner(params)
    for path in paths:
        if not isinstance(path, str) or not path.strip().startswith("/"):
            continue
        claim = _blocking_claim(claims, path, owner, now)
        if claim is not None:
            raise ScopeHeld(_scope_refusal(claim, path, owner, now))


def m_claim_scope(params):
    """Announce a subtree as one agent's working area.

    Prevents the failure where two agents edit the same network and each sees
    its own changes silently reverted. A claim on '/project1/audio' covers
    every node below it; '/project1/audio2' is a different area. Claiming a
    subtree that overlaps another owner's live claim is refused, as is a write
    into it from anybody else.
    """
    now = time.time()
    path = _normalise_scope_path(params.get("path"))
    owner = _normalise_owner(params.get("owner"))
    ttl = _scope_ttl(params.get("ttl"))
    claims = _read_scopes()
    _prune_scopes(claims, now)

    clash = _overlapping_claim(claims, path, owner, now)
    if clash is not None:
        raise ScopeHeld(
            "'%s' overlaps '%s', claimed by '%s' from %s until %s (%d s left). "
            "Claim a subtree outside it, or wait for it to expire."
            % (
                path,
                clash["path"],
                clash["owner"],
                _stamp(clash["claimed"]),
                _stamp(clash["expires"]),
                int(max(0.0, clash["expires"] - now)),
            )
        )

    existing = claims.get(path)
    # Re-claiming one's own path renews it rather than stacking a second
    # record: a long job should extend its claim, not lose it mid-flight.
    claimed = existing["claimed"] if existing else now
    claims[path] = {
        "path": path,
        "owner": owner,
        "claimed": claimed,
        "expires": now + ttl,
    }
    table = _store_scopes(claims)

    # Read back before reporting success. A claim that was accepted but not
    # kept is the defect this carrier exists to fix, and the only honest way
    # to promise it is to look.
    kept = _read_scopes().get(path)
    if kept is None or kept["owner"] != owner:
        raise RuntimeError(
            "the claim on '%s' did not survive being written to %s — refusing "
            "to report an area as held when it is not" % (path, table.path)
        )

    view = _claim_view(kept, now)
    view["renewed"] = existing is not None
    view["ttl"] = ttl
    view["table"] = table.path
    return view


def m_release_scope(params):
    """Give a claimed subtree back before it expires.

    Only the owner can release its own claim; releasing something nobody holds
    is reported, not raised, since an agent tidying up after a crash cannot
    know whether its claim already expired.
    """
    now = time.time()
    path = _normalise_scope_path(params.get("path"))
    owner = _normalise_owner(params.get("owner"))
    claims = _read_scopes()
    _prune_scopes(claims, now)

    claim = claims.get(path)
    if claim is None:
        # Still rewrite: this call may be the one that clears rows left by
        # expiry or by a previous process.
        _store_scopes(claims)
        return {
            "released": False,
            "path": path,
            "note": "no live claim on '%s' — it expired or was never made" % path,
        }
    if claim["owner"] != owner:
        raise ScopeHeld(
            "'%s' is held by '%s', not by '%s'; only its owner can release it "
            "(it expires by itself at %s)."
            % (path, claim["owner"], owner, _stamp(claim["expires"]))
        )
    del claims[path]
    _store_scopes(claims)
    return {"released": True, "path": path, "owner": owner}


def m_scopes(_params):
    """Every live claim: who holds what, since when, and when it lapses."""
    now = time.time()
    claims = _read_scopes()
    expired = _prune_scopes(claims, now)
    live = [_claim_view(claim, now) for claim in _active_scopes(claims, now)]
    if expired:
        _store_scopes(claims)
    return {"count": len(live), "scopes": live, "expired": expired, "now": now}


# -- status panel -----------------------------------------------------------

# A Text TOP inside the bridge COMP so the person whose project an agent is
# editing can see, without asking anything, that the bridge is up and what it
# just did. Read-only by construction: no interaction, no keyboard, no timer,
# and nothing outside /tdatlas is touched.
#
# The state it renders lives in a Table DAT beside the claims table and not in
# a module-level dict, for the reason decision 22 records: this module's body
# is re-executed between requests, so a module variable is rebuilt and the
# panel would forget the previous call every time.
#
# Updated once per authenticated request, at the end of it, and never on a
# timer. Measured on 2025.32460, N=300: reading the table's rows costs 8.9 us
# and writing the Text TOP's `text` parameter 1.8 us, so a whole update cycle
# is tens of microseconds against a 16 700 us frame. A request has already
# interrupted the frame by the time this runs, which is why per-request is the
# chosen frequency: it costs a frame nothing that the request was not already
# costing, and an idle bridge updates nothing at all.
#
# The price is a clock that stops while nobody is talking to the bridge, so
# every time on the panel is an absolute wall clock time, never "3 s ago" —
# a stale absolute time reads as stale, a stale relative one lies.
#
# Neither the panel nor the table is created from the request path. Both the
# textport install (bootstrap.py) and the released .tox carry the Text TOP;
# the table is made on first use like the claims table. Measured on the same
# build: creating an operator from a script, writing a DAT cell and writing a
# parameter all leave `ui.undo.undoStack` at the length they found it, so none
# of this lands on the artist's undo stack.

PANEL_TOP = "panel"
STATUS_TABLE = "tdatlas_status"

# The panel's geometry, measured rather than assumed. Every number below was
# read off the live 2025.32460 panel by rendering strings into it and looking
# at which pixels carried ink (`numpyArray`, alpha > 0.05):
#
#   font Courier New, fontsizex 15   advance exactly 12.0 px per character —
#                                    'i', '.', '0' and 'W' all advance the
#                                    same 12 px, which is what makes a line's
#                                    width a function of its length at all
#   line advance                     24 px (ink of successive lines starts at
#                                    y = 11, 35, 59, 83, 107 from the top)
#   first line                       ink from y = 11 to y = 22
#
# The font is pinned, and that is the whole reason a host-side test of this
# panel can exist. The panel used to inherit TouchDesigner's default Verdana,
# which is proportional: measured on the same panel at the same size, 40 'i'
# span 197 px and 40 'W' span 749 px — a factor of four. Under a proportional
# font no line length is a width, `_panel_line`'s `ljust` does not line the
# columns up either, and the only way to find out whether the text fits is to
# look at it. Courier New because it is the one fixed-pitch face present by
# default on both platforms this project claims (macOS and Windows); a font
# TouchDesigner cannot find falls back silently, which is the failure this
# pinning accepts.
PANEL_FONT = "Courier New"
PANEL_FONT_SIZE = 15
PANEL_PX_PER_CHAR = 12
PANEL_PX_PER_LINE = 24
# Where the first line's ink starts and ends, below the top edge.
PANEL_INK_TOP = 11
PANEL_INK_BOTTOM = 22
PANEL_X = 10
PANEL_WIDTH = 760
# Eight lines: the five the renderer always writes plus three for values that
# do not fit on one — the health verdict is regularly two, and a failed or
# refused save note can be. 200 px is what eight lines need by the numbers
# above, rounded up from 191.
PANEL_HEIGHT = 200

# How much text fits, from the geometry alone. `_wrap_value` and the tests
# both use these, so the panel cannot grow a line that does not fit without
# something going red.
PANEL_COLUMNS = (PANEL_WIDTH - PANEL_X) // PANEL_PX_PER_CHAR
PANEL_ROWS = (PANEL_HEIGHT - PANEL_INK_BOTTOM) // PANEL_PX_PER_LINE + 1

# The panel's parameters, held here because three places need the same answer
# and only this module is importable from all three: bootstrap.py reads it off
# the installed Text DAT (`handler.module`), project/release.py imports it to
# lay out the released .tox, and the tests import it on the host. Only what a
# default Text TOP does not already give — it comes up centred and 256 square.
# Word wrap stays on as a backstop only: `render_panel` breaks its own lines at
# PANEL_COLUMNS and never hands TouchDesigner a line long enough to wrap.
PANEL_PARS = (
    ("alignx", "left"),
    ("aligny", "top"),
    ("font", PANEL_FONT),
    ("fontsizex", str(PANEL_FONT_SIZE)),
    ("wordwrap", "1"),
    ("positionunit", "pixels"),
    ("positionx", str(PANEL_X)),
    ("positiony", "-6"),
    ("outputresolution", "custom"),
    ("resolutionw", str(PANEL_WIDTH)),
    ("resolutionh", str(PANEL_HEIGHT)),
)
# ASCII and one line: this exact string is written into the released .tox's
# .parm file, whose grammar has no room for a newline and whose encoding
# handling this project has not measured.
PANEL_PLACEHOLDER = "td-atlas panel - waiting for the first request"
_STATUS_HEADER = ("key", "value")

# Every line's label column, so the values sit under each other.
_PANEL_LABEL = 11


def _parse_status_rows(rows):
    """The table's cells as a flat mapping. Pure, and never raises.

    Unlike the claims table this is not filtered by pid: what it holds is a
    display, not a permission, and a stale line saved inside a .toe is
    replaced by the first request rather than acted upon.
    """
    state = {}
    for row in rows:
        cells = [str(cell) for cell in row]
        if len(cells) < 2 or cells[0] == _STATUS_HEADER[0]:
            continue
        state[cells[0]] = cells[1]
    return state


def _format_status_rows(state):
    """The header plus one row per key, in a stable order. Pure."""
    rows = [list(_STATUS_HEADER)]
    for key in sorted(state):
        value = str(state[key])
        # Tabs and newlines are the Table DAT's own separators; a value
        # carrying either would come back as extra cells or extra rows.
        value = value.replace("\t", " ").replace("\r", " ").replace("\n", " ")
        rows.append([key, value])
    return rows


def _clock(stamp):
    """A stored epoch as a wall clock time, or '' when there is nothing.

    Local time, because the only reader is the person sitting at this machine.
    """
    try:
        seconds = float(stamp)
    except (TypeError, ValueError):
        return ""
    if seconds <= 0:
        return ""
    return time.strftime("%H:%M:%S", time.localtime(seconds))


def _tally_failures(state, outcome, pid):
    """Update the session's refusal count in `state`, in place. Pure.

    Tied to the pid for the same reason the claims table is (decision 22): the
    status table can be saved inside a .toe, and a count carried over from
    another run would tell the artist their fresh session had already failed
    seven times. A pid that does not match the running one resets the count
    rather than continuing it, so "this session" means what it says.

    Only failures are counted. The panel already names the last call and
    whether it failed; what a glance cannot get from that is whether the
    failure was the first or the twelfth, which is the difference between a
    typo and an agent stuck in a loop.
    """
    try:
        stored = int(state.get("fails") or 0)
    except (TypeError, ValueError):
        stored = 0
    count = stored if str(state.get("failsPid") or "") == str(pid) else 0
    if outcome == "error":
        count += 1
    state["fails"] = str(count)
    state["failsPid"] = str(pid)
    return count


def _panel_line(label, value):
    return label.ljust(_PANEL_LABEL) + value


def _wrap_value(label, value):
    """One labelled line as the lines it actually occupies. Pure.

    Broken here rather than left to the Text TOP's own word wrap, for two
    reasons measured on the live panel: TouchDesigner breaks only at spaces, so
    a long unbroken token (a path, a file name) is *clipped* instead of
    wrapped; and a wrap nobody counted is a wrap that can push the last line
    off the bottom, which is what the health verdict was doing.

    Continuations are indented under the value column, so the label still
    reads as belonging to all of them.
    """
    room = PANEL_COLUMNS - _PANEL_LABEL
    indent = " " * _PANEL_LABEL
    words = str(value).split(" ")
    lines = []
    current = ""
    for word in words:
        candidate = word if not current else current + " " + word
        if len(candidate) <= room:
            current = candidate
            continue
        if current:
            lines.append(current)
            current = ""
        # A single word longer than the column is cut, not dropped: the
        # alternative is a line TouchDesigner clips without saying so.
        while len(word) > room:
            lines.append(word[:room])
            word = word[room:]
        current = word
    lines.append(current)
    return [_panel_line(label, lines[0])] + [indent + line for line in lines[1:]]


def _hard_split(line):
    """A line with no label, cut to the panel's width. Pure.

    The first line's numbers come out of the status table, and a table saved
    inside a .toe can hold anything a person typed into it, so even that line
    is not trusted to be short.
    """
    line = str(line)
    if len(line) <= PANEL_COLUMNS:
        return [line]
    return [line[i:i + PANEL_COLUMNS] for i in range(0, len(line), PANEL_COLUMNS)]


def _fit(lines):
    """Cut the panel's text to the rows the panel has, and say that it was cut.

    The renderer's own worst case fits; an unbounded value (a caller's name, a
    writer's exception message) does not have to, and a line that silently
    fell off the bottom would read as a line that was never written.
    """
    if len(lines) <= PANEL_ROWS:
        return lines
    kept = lines[:PANEL_ROWS]
    last = kept[-1]
    marker = "..."
    kept[-1] = last[: PANEL_COLUMNS - len(marker)].rstrip() + marker
    return kept


def render_panel(state):
    """The panel's text, from the stored state alone. Pure.

    Four lines, in the order a glance wants them: what this is and where it
    listens, who called it last and when, how big the edit in flight is, and
    the last health verdict. A field nobody has written yet says so rather
    than showing a zero, which would read as a measurement.

    Every line the panel shows is broken here, at `PANEL_COLUMNS`, and the
    whole is cut to `PANEL_ROWS` — both numbers come from the measured
    geometry above, so "the text fits the panel" is a claim a test on the host
    can check without TouchDesigner anywhere.
    """
    port = str(state.get("port") or "?")
    protocol = str(state.get("protocol") or PROTOCOL_VERSION)
    head = "td-atlas    port %s    protocol %s" % (port, protocol)
    # On the first line rather than on a fifth of its own: the panel has
    # exactly PANEL_ROWS rows and the fifth is already spoken for by the
    # network text (see below), so a new row would push a real line off the
    # bottom. Absent at zero — a counter reading 0 is noise, and the reader
    # needs to notice it only when it is not.
    try:
        fails = int(state.get("fails") or 0)
    except (TypeError, ValueError):
        fails = 0
    if fails > 0:
        head += "    %d failed" % fails
    lines = _hard_split(head)

    method = str(state.get("method") or "")
    if method:
        owner = str(state.get("owner") or "")
        call = method + ("  by " + owner if owner else "  unattributed")
        when = _clock(state.get("at"))
        if when:
            call += "  at " + when
        if str(state.get("outcome") or "") == "error":
            call += "  FAILED"
        lines.extend(_wrap_value("last call", call))
    else:
        lines.extend(_wrap_value("last call", "nothing yet"))

    lines.extend(_wrap_value("batch", str(state.get("batch") or "none yet")))

    verdict = str(state.get("health") or "")
    if verdict:
        when = _clock(state.get("healthAt"))
        lines.extend(_wrap_value("health", verdict + ("  at " + when if when else "")))
    else:
        lines.extend(_wrap_value("health", "not checked yet"))

    # A fifth line, and only once a save has actually happened. Not "off" from
    # the start: the four lines above are what the panel has always said, and a
    # bridge whose artist never saves must not grow a line telling them about a
    # feature they did not ask about. Once a save has run, the line is there
    # either way — a switched-off externalisation that says nothing would leave
    # somebody who did turn it on with no way to tell it apart from a failure.
    written = str(state.get("text") or "")
    if written:
        when = _clock(state.get("textAt"))
        lines.extend(_wrap_value("text", written + ("  at " + when if when else "")))
    return "\n".join(_fit(lines))


def _bridge_holder():
    """The COMP the bridge lives in, or None off the host.

    Same rule as `_scope_table`: only NameError is caught, so a real failure
    to reach a COMP is not swallowed into "there is no panel".
    """
    try:
        return me.parent()
    except NameError:
        return None


def _status_table(create=False):
    """The Table DAT holding the panel's state, or None when there is none."""
    holder = _bridge_holder()
    if holder is None:
        return None
    table = holder.op(STATUS_TABLE)
    if table is None and create:
        table = holder.create(tableDAT, STATUS_TABLE)
        table.clear()
        table.appendRow(list(_STATUS_HEADER))
        # Parked below the claims table, out of the way of the DATs a person
        # came here to read.
        table.nodeY = -450
    return table


def _read_status():
    table = _status_table()
    if table is None:
        return {}
    return _parse_status_rows(table.rows())


def _note_status(_amend=None, **fields):
    """Merge `fields` into the stored state and repaint the panel.

    Never raises: a panel that cannot be drawn must not turn a working request
    into a failed one. It is a display.

    `_amend` is a callback given the merged state before it is written, for
    the one field that cannot be computed without reading what is already
    there — the session's failure count. It rides inside the read the write
    already does, so a counter that has to look at its own previous value
    still costs the frame exactly one table read and one table write, the same
    as before (decision 30: 42.8 us for the whole cycle).
    """
    try:
        table = _status_table(create=True)
        if table is None:
            return None
        state = _parse_status_rows(table.rows())
        for key, value in fields.items():
            state[key] = "" if value is None else str(value)
        if _amend is not None:
            _amend(state)
        table.clear()
        for row in _format_status_rows(state):
            table.appendRow(row)
        _paint_panel(state)
        return state
    except Exception as exc:
        print("[td-atlas] status panel not updated: %s" % exc)
        return None


def _paint_panel(state):
    """Write the rendered text onto the Text TOP, if the COMP carries one."""
    holder = _bridge_holder()
    if holder is None:
        return
    panel = holder.op(PANEL_TOP)
    if panel is None:
        return
    panel.par.text = render_panel(state)


def _note_request(dat, name, params, outcome):
    """Record the call that just ran. One table write, one parameter write."""
    try:
        port = int(dat.par.port.eval())
    except Exception:
        port = ""
    pid = os.getpid()
    _note_status(
        _amend=lambda state: _tally_failures(state, outcome, pid),
        port=port,
        protocol=PROTOCOL_VERSION,
        method=name,
        owner=_caller_owner(params or {}),
        at=repr(time.time()),
        outcome=outcome,
    )


def m_status_note(params):
    """Put a line on the panel that only the host can know.

    The health verdict is decided on the host — it needs two samples a second
    apart and the rules in bridge/health.py — so the bridge cannot compute it
    and is told it instead. Nothing else here accepts host-supplied text.
    """
    verdict = params.get("health")
    if not isinstance(verdict, str) or not verdict.strip():
        raise ValueError("status_note needs 'health', a one-line verdict")
    state = _note_status(health=verdict.strip(), healthAt=repr(time.time()))
    return {"panel": render_panel(state or {}), "stored": bool(state)}


# -- the network text written beside the .toe on save -----------------------

# When the artist saves, the bridge writes the network out as the same JSON
# `td-atlas project text` produces on the host, into a file beside the .toe.
# The point is history: a text the artist never has to ask for is a text that
# is in git after every save, and a .toe alone diffs as one opaque blob.
#
# How TouchDesigner announces a save — measured, not read. The offline index's
# Execute DAT article does not mention saving at all, but the parameter table
# dumped from this build carries `projectpresave`/`projectpostsave`, so the
# mechanism was confirmed on the running 2025.32460: an Execute DAT with
# `projectpostsave` on fires `onProjectPostSave()` for a scripted
# `project.save(path)`, with no arguments and no keywords, and `project.name`
# and `project.folder` already naming the file that was just written. Post and
# not pre for two reasons: the file exists by then, so the text lands beside
# something real, and a post-save failure cannot reach the save it follows.
#
# Cost. Everything here runs in the save callback, where parameter reads are
# cheap: measured on 2025.32460 inside `onProjectPostSave`, reading the value
# of every non-default parameter of 76 operators costs 0.84 ms, 0.14 us per
# parameter. (The same loop measured through the bridge's own request handler
# reports 96-120 us per parameter — a request runs inside a cook, and that
# number is an artefact of the measuring context, not of the API. Both
# numbers are in the report for item 19; only the callback one is the cost
# this feature actually pays.)

TEXT_SUFFIX = ".network.json"

# The Execute DAT that carries the subscription. Created by both installs
# (component/bootstrap.py and project/release.py) and never from a request:
# the same rule the panel follows.
SAVE_DAT = "onsave"

# Its parameters — only what a default Execute DAT does not already give.
SAVE_PARS = (
    ("active", "1"),
    ("projectpostsave", "1"),
)

# The Execute DAT's text. It holds no logic on purpose: the logic belongs in
# this module, which the host imports and tests, and a shim that has to be
# edited in step with it would be a second copy to keep honest. Every callback
# an Execute DAT can fire is defined, because TouchDesigner's own default text
# defines them all and a missing one is a NameError in the artist's log.
SAVE_SHIM = '''# td-atlas: write the network text beside the .toe after a save.
# The work is in the handler Text DAT beside this one; this only calls it, and
# swallows nothing itself — `on_project_post_save` never raises.


def onProjectPostSave():
    me.parent().op('handler').module.on_project_post_save()


def onProjectPreSave():
    pass


def onStart():
    pass


def onCreate():
    pass


def onExit():
    pass


def onFrameStart(frame):
    pass


def onFrameEnd(frame):
    pass


def onPlayStateChange(state):
    pass


def onDeviceChange():
    pass
'''

# The switch. Off unless the config says otherwise, because writing a file into
# the artist's own project folder is the one thing this project does that it
# cannot take back, and nobody asked for it at install time. Turning it on is
# one key in ~/.td-atlas/config.json — the file `td-atlas install` already
# writes — and the panel says at every glance which way it is set, so an artist
# who wants it never has to wonder whether it is working.
#
# Not a custom parameter on the COMP: both installs re-apply their parameters
# on every upgrade, so a custom parameter would silently reset the artist's
# choice the next time they reinstalled. config.json survives that.
TEXT_ON_SAVE_KEY = "text_on_save"

# The cap, and the reason there is one. Measured on 2025.32460, three saves
# each, of the same session with a palette component loaded beside /project1:
#
#   operators   the save without it   the text on top of it
#          12            10-14 ms                1-8 ms
#         717          1250-1690 ms            148-172 ms
#        4093           339-385 ms           1124-1241 ms
#
# The text costs ~0.25-0.30 ms per operator, and TouchDesigner's own save cost
# does not follow the operator count at all — so on a big network the text is
# not a percentage of the save, it is a multiple of it: at 4093 operators a
# 0.35 s save becomes a 1.5 s one. (The first write after the handler text is
# replaced runs 2-3x the rest, because the module body is re-executed; every
# number above is the steady state after it.)
#
# The threshold: the text may add about half a second to a save and no more.
# At the measured rate that is two thousand operators, which is where the cap
# sits. Half a second because a save is already a moment the artist waits
# through — the measured saves here run from 0.34 to 1.7 s — and because it
# stays under the second at which a wait stops reading as part of the action.
# The cap is not a promise about wall clock time on another machine; it is
# this machine's measurement turned into a number the artist can see and move.
#
# Over the cap nothing is written and the panel says so with both numbers, so
# it is never a silent no-op, and the cap is a config key for an artist who
# would rather wait. Rejected alternatives: writing it anyway (the contract
# asks for a threshold, and a 2.5 s Ctrl+S is exactly what nobody would
# report as a bug in this feature), and spreading the walk over the frames
# after the save (the text would then describe a network that has already
# moved on from the file it is named after).
TEXT_MAX_OPS_KEY = "text_on_save_max_ops"
DEFAULT_MAX_OPS = 2000

# The keys the top level of this format always carries. A file that does not
# parse as JSON, or parses without them, belongs to somebody else and is never
# overwritten — the artist's project folder is not ours to tidy.
_TEXT_KEYS = ("source", "build", "path", "operator_count", "operators")

# Roots the text deliberately leaves out, and why:
#   /ui, /sys        - not in the .toe at all; both are external .tox files
#                      inside the TouchDesigner installation (measured: their
#                      `externaltox` parameter names a file under
#                      /Applications/TouchDesigner.app, every other root's is
#                      empty). Excluded by that measured rule, not by name.
#   /local, /perform - TouchDesigner's own scaffolding, not the artist's
#                      network. Measured on 2025.32460: /local carries 55 live
#                      operators of which the .toe stores 13, because built-in
#                      components (timeCOMP, /local/midi, /local/maps/master)
#                      have children live that are not written to the file. A
#                      text that listed them would diverge from the file by
#                      more nodes than the file holds.
#   /tdatlas         - the bridge itself, including the whole of this module as
#                      the text of a DAT. Nobody wants that in their project's
#                      git history.
_SKIPPED_ROOTS = ("local", "perform", "tdatlas")

# What this text does NOT match, and why. It is printed from the live network;
# `td-atlas project text` prints from the expanded file. Seven classes of
# difference are known, all of them measured on 2025.32460 on the network
# `tests/live_network.py` builds (21 operators, and 24 more inside the
# annotation component it loads) — 104 differing fields out of 722 compared,
# with nothing left over. The network is a test fixture and not a memory, so
# any of these numbers can be taken again: `tests/test_live_text_diff.py`
# builds it, saves it with `save_tox`, reads the file back and fails if a
# field falls outside these seven.
#
#   fields  class                     mechanism
#       80  custom parameter          `toeexpand` writes a custom parameter's
#           placement                 *value* into `.parm` beside the built-in
#                                     ones (with 0x4000000 in the flags word)
#                                     and its *definition* into `.cparm`, which
#                                     `formats.read_custom_parms` deliberately
#                                     does not parse. The reader therefore
#                                     files the value under `parms`; this side
#                                     files it under `custom_parms`, where
#                                     `par.isCustom` puts it. 40 parameters,
#                                     two fields each.
#       10  custom parameter at its   Nothing is written to `.parm` for a
#           default                   custom parameter still at its default, so
#                                     the file holds it only as a `.cparm`
#                                     definition and the reader sees nothing.
#                                     This side has no isDefault filter on
#                                     custom parameters and prints it.
#        8  float text formatting     The file keeps TouchDesigner's own
#                                     printing (`2e+06`); `_parm_string` here
#                                     prints `2000000`. Same number, different
#                                     text.
#        3  parameter at its default  `.parm` carries a line for a parameter
#           with a flags word         whose value is the default when its flags
#                                     word is not zero (measured: `iop1op 32
#                                     ""`, `ext0name 256 ""`). `par.isDefault`
#                                     drops it here.
#        1  COMP input wiring         A COMP's operator input is stored in a
#                                     `.network` file (`compinputs`), which the
#                                     offline reader does not parse — see the
#                                     list of unparsed file kinds in
#                                     project/rebuild.py. The file-side text
#                                     shows no inputs where this one shows
#                                     them.
#        1  flag vocabulary           `.n` flag words this side does not know
#                                     (measured: `showDocked`). `_FLAG_WHEN_ON`
#                                     is the vocabulary; anything outside it is
#                                     absent here and present there.
#        1  the .tox save's own root  `enableexternaltox` is written into the
#           parameter                 root `.parm` of a saved `.tox` and of no
#                                     `.toe` (measured over the expansion
#                                     cache: 1326 of 2066 `.tox` roots carry
#                                     it, 0 of the `.toe` roots do). An
#                                     artefact of measuring through `save_tox`,
#                                     not of this text.
#
# All seven are the file and the live object genuinely holding different
# things. Two more stood here until 2026-08-29 and were ours to fix rather
# than to live with: a table DAT's shape, which the reader transposed, and a
# panel COMP's wire to the COMP beside it, which lives on
# `inputCOMPConnectors` and which `_inputs_data` now reads alongside
# `inputConnectors`.
#
# One gap the network does not reach and nothing above covers: a COMP with
# both an operator input and a COMP wire (measured: `tableCOMP`, and any
# container that grows an `inTOP` inside). The file splits the two — the COMP
# wire into `.n` `inputs`, the operator input into `.network` `compinputs` —
# while this side reports both, each at its own connector's index, so such a
# node would differ by more than the "COMP input wiring" class describes.


def _config_value(key, default=None):
    """One value out of ~/.td-atlas/config.json. Never raises."""
    try:
        with open(_config_path(), "r") as handle:
            config = json.load(handle)
    except Exception:
        return default
    if not isinstance(config, dict):
        return default
    return config.get(key, default)


def text_on_save_enabled():
    """Whether the artist has asked for the text. Off unless told otherwise."""
    return bool(_config_value(TEXT_ON_SAVE_KEY, False))


def text_max_ops():
    """How many operators the text will cover before it gives up. See above."""
    value = _config_value(TEXT_MAX_OPS_KEY, DEFAULT_MAX_OPS)
    try:
        value = int(value)
    except (TypeError, ValueError):
        return DEFAULT_MAX_OPS
    # Zero or below means no cap: an artist who says so has said it on
    # purpose, and the alternative reading — "write nothing, ever" — is what
    # the switch above is for.
    return value


# -- the JSON printer -------------------------------------------------------
#
# A copy of `td_atlas/project/serialize.py`'s printer, because that module
# lives on the host and nothing outside TouchDesigner's standard library can be
# imported here. The two are held together by a test rather than by care:
# `tests/test_externalise.py` prints the host's own fixture projects through
# both and asserts the bytes are equal, so a change to one that the other does
# not follow fails the suite.

_INLINE_LIMIT = 100
_BLOCK_KEYS = frozenset(
    ("text", "table", "parms", "custom_parms", "children", "operators")
)


def split_text(text):
    """DAT text as the array of lines that goes into the JSON.

    `split("\\n")`, never `splitlines()`: only the former is the exact inverse
    of `"\\n".join`.
    """
    return text.split("\n")


def _scalar(value):
    return json.dumps(value, ensure_ascii=False)


def _inline(value):
    text = json.dumps(value, ensure_ascii=False, separators=(", ", ": "))
    if len(text) <= _INLINE_LIMIT and "\n" not in text:
        return text
    return None


def _emit(value, level, key, out):
    pad = "  " * level
    if not isinstance(value, (dict, list)):
        out.append(_scalar(value))
        return
    if not value:
        out.append("{}" if isinstance(value, dict) else "[]")
        return
    if key not in _BLOCK_KEYS:
        compact = _inline(value)
        if compact is not None:
            out.append(compact)
            return
    if isinstance(value, dict):
        out.append("{\n")
        items = list(value.items())
        for index, (name, item) in enumerate(items):
            out.append("%s  %s: " % (pad, _scalar(name)))
            _emit(item, level + 1, name, out)
            out.append(",\n" if index < len(items) - 1 else "\n")
        out.append(pad + "}")
        return
    out.append("[\n")
    for index, item in enumerate(value):
        out.append(pad + "  ")
        _emit(item, level + 1, None, out)
        out.append(",\n" if index < len(value) - 1 else "\n")
    out.append(pad + "]")


def dumps(data):
    """Plain data as JSON with this project's line policy."""
    out = []
    _emit(data, 0, None, out)
    out.append("\n")
    return "".join(out)


# -- where the text goes ----------------------------------------------------


def sidecar_name(project_name):
    """The text's file name for a project file name. Pure.

    `atlas-live.3.toe` -> `atlas-live.network.json`. The version suffix
    TouchDesigner adds on save is stripped on purpose: measured on 2025.32460,
    every `project.save(path)` leaves `project.name` one version higher than
    the last, so a name carrying the version would leave a new text file
    behind after every single save and diff against nothing. One project, one
    text, rewritten in place — the history is git's job, and `network.json` is
    the name this project already uses for it (see project/variants.py).
    """
    stem = project_name
    for suffix in (".toe", ".tox"):
        if stem.lower().endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    head, dot, tail = stem.rpartition(".")
    if dot and tail.isdigit():
        stem = head
    return (stem or "project") + TEXT_SUFFIX


def is_our_text(existing):
    """Does this file's content look like a text this bridge wrote? Pure.

    The guard on never clobbering somebody else's file. Deliberately shallow:
    it asks whether the bytes parse as a JSON object carrying this format's
    top-level keys, and nothing about who wrote it — a text written by
    `td-atlas project text` on the host is the same format and is ours to
    replace, while anything else in the artist's folder is not.
    """
    try:
        data = json.loads(existing)
    except Exception:
        return False
    if not isinstance(data, dict):
        return False
    return all(key in data for key in _TEXT_KEYS)


def write_text_atomic(path, text):
    """Write `text` to `path` so that no reader ever sees a half-written file.

    Into a temporary file in the same directory, then `os.replace`, which is
    atomic on POSIX and on Windows. Same directory because a replace across
    filesystems is not a rename and not atomic. The temporary is removed in a
    `finally`, so a failure anywhere leaves either the previous file or
    nothing — never a stump, and never a stray `.tmp` beside the artist's
    project.

    No `fsync`: this trades durability across a power cut, which the .toe
    beside it does not have either, for not stalling the save. What it does
    guarantee is what matters here — a reader sees one whole file or the
    other, never a partial one.
    """
    folder = os.path.dirname(path) or "."
    temp = os.path.join(folder, ".%s.%d.tmp" % (os.path.basename(path), os.getpid()))
    try:
        with open(temp, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(temp, path)
    finally:
        try:
            if os.path.exists(temp):
                os.remove(temp)
        except OSError:
            pass
    return path


# -- reading the live network -----------------------------------------------


def _parm_string(par):
    """One parameter value as the string the expanded .toe would hold.

    Measured against `toeexpand`'s own `.parm` files for the same project:
    of 89 non-default parameters, 76 matched a bare `str()` and the remaining
    13 were all toggles, which the file writes as `on`/`off` rather than as
    True/False. Floats are written without a trailing `.0` because that is how
    the file writes them (`end 2`, not `end 2.0`).
    """
    value = par.val
    if par.style == "Toggle":
        return "on" if value else "off"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float):
        if value == int(value) and abs(value) < 1e15:
            return str(int(value))
        return repr(value)
    if isinstance(value, str):
        return value
    return str(value)


def _parm_data(par):
    """A parameter as a bare string, or a map when the line carries more.

    The same shape `project/serialize.py` emits: the constant is kept beside
    an expression because the file keeps it — it is what the parameter falls
    back to when the expression is switched off.
    """
    mode = str(par.mode).rsplit(".", 1)[-1]
    out = {}
    # On the *stored text*, not on the mode. Measured on 2025.32460:
    # `/project1/moviefilein1.index` reports ParMode.CONSTANT while carrying
    # the expression "me.time.frame", and the .parm line toeexpand writes for
    # it has the expression bit set — so a reader of the file sees an
    # expression where the live mode says constant. Keying off the mode
    # silently dropped that expression, which is exactly the kind of
    # confidently-wrong answer this project refuses; keying off the text keeps
    # it and matches the file.
    expr = par.expr or ""
    bind = par.bindExpr or ""
    if expr or mode == "EXPRESSION":
        out["expr"] = expr
    if bind or mode == "BIND":
        out["bind"] = bind
    if not out:
        return _parm_string(par)
    out["value"] = _parm_string(par)
    return out


# The flags the `.n` file records, and the live attribute each is read from.
# Sparse, like the file: only what differs from the flag's default is written.
# `parlanguage` is the odd one — measured across a 705-operator palette
# component, every Python node carries `parlanguage 0` and every Tscript node
# carries nothing at all, so the marker is "0" and the condition is `python`.
_FLAG_WHEN_ON = (
    ("parlanguage", "python", "0"),
    ("viewer", "viewer", "1"),
    ("display", "display", "on"),
    ("render", "render", "on"),
    ("bypass", "bypass", "on"),
    ("lock", "lock", "on"),
    ("current", "current", "on"),
    ("picked", "selected", "on"),
    ("pickable", "pickable", "on"),
    ("cloneImmune", "cloneImmune", "on"),
    # `activate` is the COMP's active-viewer flag: measured on the expanded
    # project, /project1/geo1 carries `activate on` in its .n file and is the
    # one node whose live `activeViewer` is True.
    ("activate", "activeViewer", "on"),
    # A CHOP exporting its channels: measured on the same component, the .n
    # file of an exporting math CHOP carries `export on`.
    ("export", "export", "on"),
)


def _flags_data(target):
    flags = {}
    for name, attribute, marker in _FLAG_WHEN_ON:
        if getattr(target, attribute, None):
            flags[name] = marker
    return dict(sorted(flags.items()))


def _dat_owns_its_text(target):
    """Does this DAT hold its own text, or compute it every cook?

    The .toe stores content only for the DATs that own it; a Select DAT, a
    Null, an Info, a Merge carry whatever they cooked last time and the file
    keeps none of it. Writing that into the text would put a diff in front of
    the artist on every save for something they did not touch — an Info DAT's
    shader compile log, a Null's copy of its input — which is the opposite of
    what a text in git is for.

    The rule is measured, not guessed: across 211 DATs in a 705-operator
    palette component, no DAT *type* was mixed — every operator of a given
    type either always had its content in the file or never did. The types
    that had it are `text`, `table` and the six callback families whose names
    end in `exec`; the fourteen that never did are all either filters
    (`isFilter` was True for 49 of 49 unstored ones) or generators whose
    content is an output (`select`, `info`, `in`, `out`, `script`, `eval`,
    `examine`, `keyboardin`, `renderpick`, `chopto`, `sopto`, `insert`).
    `endswith("exec")` rather than the six names, so a callback DAT type this
    sample did not contain is covered too.

    The named gap: a DAT type outside this rule that *does* own its text loses
    it in the sidecar. That is an under-report against a file that still has
    it, which is the direction this project prefers to be wrong in.
    """
    kind = getattr(target, "type", "") or ""
    return kind in ("text", "table") or kind.endswith("exec")


def _inputs_data(target):
    """Wiring as [index, source], the source named the way the file names it.

    A sibling by name, anything else by absolute path — which is what the
    reader on the host resolves against the node's own parent.

    Two connector lists, because a COMP has two. `inputConnectors` is the
    operator wiring every family has; `inputCOMPConnectors` is the left-hand
    connector a panel or object COMP hangs off its parent panel or parent
    object. Measured on 2025.32460: a COMP's `.n` `inputs` block holds the
    *COMP* wire under the connector's own index (`inputs { 0 <sibling> }` for
    a container wired to `spacer`), while its operator inputs go to a
    `.network` file's `compinputs` block, which the offline reader does not
    parse. Both lists carry their own index, and every COMP type in
    2025.32460 has at most one COMP connector, so index 0 is the only one
    measured; a COMP with both kinds wired (`tableCOMP`, or a container with
    an `inTOP` inside) therefore reports two entries at index 0, one per
    connector list.
    """
    out = []
    try:
        connectors = list(target.inputConnectors)
    except Exception:
        connectors = []
    try:
        comp_connectors = list(target.inputCOMPConnectors)
    except Exception:
        comp_connectors = []
    if not connectors and not comp_connectors:
        return out
    try:
        base = target.parent().path
    except Exception:
        base = ""
    prefix = (base.rstrip("/") + "/") if base else ""

    def gather(connector_list, comp_wire):
        for index, connector in enumerate(connector_list):
            for connection in connector.connections:
                # `outOP` and not `owner`: for a wire coming out of a component
                # the owner is the component, while the file names the node
                # inside it. Measured on 2025.32460 — /project1/out1's source
                # reports owner /project1/geo1 and outOP /project1/geo1/out1,
                # and the .n file records `geo1/out1`. `owner` is the fallback,
                # because every wire between plain operators leaves `outOP`
                # empty.
                #
                # A COMP wire is the other way round: the file names the source
                # COMP itself (`inputs { 0 spacer }`), and every COMP
                # connection measured leaves `outOP` empty anyway, so `owner`
                # is what it asks for.
                source = None
                if not comp_wire:
                    try:
                        source = connection.outOP
                    except Exception:
                        source = None
                if source is None:
                    try:
                        source = connection.owner
                    except Exception:
                        source = None
                if source is None:
                    continue
                path = source.path
                # Relative to the consumer's own parent, which is what the
                # reader on the host resolves against; anything outside stays
                # absolute.
                if prefix and path.startswith(prefix):
                    path = path[len(prefix):]
                out.append([index, path])

    gather(connectors, False)
    gather(comp_connectors, True)
    return out


def live_node_data(target, children=True):
    """One live operator in the shape `project/serialize.py` prints.

    The absolute path is deliberately absent, exactly as it is there: it is
    the concatenation of the enclosing names, and writing it into every node
    would make renaming one component rewrite a line for every operator under
    it.
    """
    data = {
        "name": target.name,
        "type": target.OPType,
        "family": target.family,
    }
    data["tile"] = [
        float(target.nodeX),
        float(target.nodeY),
        float(target.nodeWidth),
        float(target.nodeHeight),
    ]
    data["flags"] = _flags_data(target)
    try:
        data["color"] = [round(channel, 6) for channel in target.color]
    except Exception:
        data["color"] = None
    data["inputs"] = _inputs_data(target)

    parms = {}
    custom = {}
    for par in target.pars():
        try:
            if par.isCustom:
                custom[par.name] = _parm_data(par)
            elif not par.isDefault:
                parms[par.name] = _parm_data(par)
        except Exception:
            continue
    data["parms"] = dict(sorted(parms.items()))
    data["custom_parms"] = dict(sorted(custom.items()))
    pages = []
    try:
        pages = [page.name for page in target.customPages]
    except Exception:
        pages = []
    if pages:
        data["custom_pages"] = pages

    text = None
    table = None
    if getattr(target, "isDAT", False) and _dat_owns_its_text(target):
        try:
            if target.isTable:
                table = [[str(cell) for cell in row] for row in target.rows()]
            else:
                text = target.text
        except Exception:
            text = None
    data["text"] = split_text(text) if text is not None else None
    data["table"] = table

    if children:
        kids = []
        if getattr(target, "isCOMP", False):
            for child in target.children:
                kids.append(live_node_data(child))
        data["children"] = kids
    return data


def _text_roots():
    """The root components the text covers. See `_SKIPPED_ROOTS` for the rest."""
    roots = []
    for child in root.children:
        if child.name in _SKIPPED_ROOTS:
            continue
        external = ""
        try:
            external = child.par.externaltox.eval() or ""
        except Exception:
            external = ""
        if external:
            continue
        roots.append(child)
    return roots


def live_op_count():
    """How many operators the text would cover, without reading one parameter."""
    def count(target):
        total = 1
        if getattr(target, "isCOMP", False):
            for child in target.children:
                total += count(child)
        return total

    return sum(count(target) for target in _text_roots())


def live_project_data(source):
    """The whole covered network as plain data, ready for the printer."""
    nodes = [live_node_data(target) for target in _text_roots()]

    def count(node):
        total = 1
        for child in node.get("children") or ():
            total += count(child)
        return total

    return {
        "source": source,
        "build": {"build": app.build, "version": app.version},
        "path": "/",
        "operator_count": sum(count(node) for node in nodes),
        "operators": nodes,
    }


# -- the callback -----------------------------------------------------------


def externalise(folder, project_name, enabled, gather, write, exists, read,
                count=None, max_ops=0):
    """Decide and do, with every side effect handed in. Pure control flow.

    Split out from `on_project_post_save` so the whole decision — switched
    off, a foreign file in the way, a writer that raises — is testable on the
    host with no TouchDesigner anywhere. Returns the line the panel shows.
    Raises nothing: a save must not be able to fail because of this.
    """
    if not enabled:
        return {"wrote": False, "note": "off"}
    try:
        target = os.path.join(folder, sidecar_name(project_name))
        # The cap first, and the file check second. Both are cheap next to the
        # walk, but only one of them is cheap next to the other: the ownership
        # check parses whatever is already at the target, and measured on
        # 2025.32460 a 6.9 MB text left by a previous save cost ~140 ms to
        # read and parse — paid on every save, including the ones that then
        # decline to write anything. Over the cap nothing is written, so
        # nothing needs to be read.
        if count is not None and max_ops > 0:
            # Counted before the walk that reads parameters, because the
            # counting is the cheap half: measured on 2025.32460, walking the
            # tree without touching a parameter costs 0.06 ms for 76
            # operators against 0.5 ms per operator for the text itself.
            total = count()
            if total > max_ops:
                return {
                    "wrote": False,
                    "note": "skipped: %d ops over the %d cap" % (total, max_ops),
                }
        if exists(target):
            try:
                existing = read(target)
            except Exception:
                existing = None
            if existing is None or not is_our_text(existing):
                return {
                    "wrote": False,
                    "note": "refused: %s is not ours" % os.path.basename(target),
                }
        data = gather()
        write(target, dumps(data))
        return {
            "wrote": True,
            "path": target,
            "operators": data.get("operator_count", 0),
            "note": "%s  %d ops" % (os.path.basename(target), data.get("operator_count", 0)),
        }
    except BaseException as exc:
        # BaseException and not Exception. The contract for item 19 is that a
        # save cannot fail because of this "ни при каких условиях", and the
        # three that sit outside Exception are exactly the ones a big network
        # can produce: MemoryError is an Exception, but SystemExit from a
        # writer that calls something drastic and KeyboardInterrupt from the
        # textport are not, and neither is a reason to fail a save that has
        # already finished. Nothing here loops, so there is no interrupt for
        # the artist to lose.
        return {"wrote": False, "note": "failed: %s: %s" % (type(exc).__name__, exc)}


def _read_file(path):
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read()


def on_project_post_save():
    """What the Execute DAT calls. Never raises, whatever happens below.

    The save has already finished by the time this runs, so nothing here can
    cost the artist their file — but an exception escaping a callback puts a
    dialog and a red node in front of somebody who only pressed Ctrl+S, so
    the outermost frame catches everything, including the panel write.
    """
    started = time.time()
    try:
        name = project.name
        folder = project.folder
        outcome = externalise(
            folder,
            name,
            text_on_save_enabled(),
            lambda: live_project_data(name),
            write_text_atomic,
            os.path.exists,
            _read_file,
            live_op_count,
            text_max_ops(),
        )
    except BaseException as exc:  # see `externalise` for why not Exception
        outcome = {"wrote": False, "note": "failed: %s: %s" % (type(exc).__name__, exc)}
    try:
        note = outcome["note"]
        if outcome.get("wrote"):
            note += "  in %d ms" % int((time.time() - started) * 1000)
        _note_status(text=note, textAt=repr(time.time()))
    except BaseException:
        pass
    return outcome


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


def _bounded_descendants(target, limit):
    """Operators below `target`, at most `limit` of them.

    Returns (nodes, known-but-not-returned). `findChildren(depth=None)` builds
    the whole list before anything can trim it, so on a network of tens of
    thousands of operators the cost is already paid by the time a cap could
    help; walking a level at a time pays only for what comes back. The second
    number counts the operators already discovered and left unvisited — the
    true remainder is at least that, since their own children were never
    looked at, and a guess at the rest would be a made-up number.
    """
    if not hasattr(target, "children"):
        return [], 0
    found = []
    queue = list(target.children)
    while queue:
        if len(found) >= limit:
            break
        node = queue.pop(0)
        found.append(node)
        children = getattr(node, "children", None)
        if children:
            queue.extend(children)
    return found, len(queue)


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
    """Walk a component, describing children and how they are wired.

    Every cut this makes is named in the reply. A walk trimmed in silence is
    worse than a refusal: the caller reads a slice of the network as the whole
    of it and builds on operators that are not there.
    """
    target = _resolve(params.get("path") or "/")
    asked = int(params.get("depth", 1))
    depth = max(1, min(asked, _MAX_DEPTH))
    include_pars = bool(params.get("pars", False))

    # A list rather than an int: this walk is recursive and a nested function
    # cannot rebind a name in the enclosing scope without `nonlocal`, which
    # reads worse here than one mutable cell.
    budget = [_MAX_NETWORK_NODES]
    hidden = [0]

    def walk(comp, level):
        """Return (described children, how many of them were left out)."""
        if not hasattr(comp, "children"):
            return [], 0
        children = list(comp.children)
        described = []
        for child in children[:_MAX_CHILDREN]:
            if budget[0] <= 0:
                break
            budget[0] -= 1
            entry = _op_summary(child, include_pars=include_pars)
            if level < depth and getattr(child, "children", None):
                entry["children"], below = walk(child, level + 1)
                if below:
                    entry["childrenHidden"] = below
            described.append(entry)
        # Counted as direct children only: what hangs below one that was cut
        # was never walked, so its size is not known and is not guessed at.
        missing = len(children) - len(described)
        hidden[0] += missing
        return described, missing

    nodes, missing = walk(target, 1)
    result = {
        "path": target.path,
        "type": target.OPType,
        "children": nodes,
        "depth": depth,
    }
    if missing:
        result["childrenHidden"] = missing
    if hidden[0]:
        result["truncated"] = True
        result["hidden"] = hidden[0]
        result["limit"] = _MAX_NETWORK_NODES
        result["maxChildren"] = _MAX_CHILDREN
    if asked > depth:
        result["depthLimited"] = asked
    return result


def _set_dat_text(target, text):
    """Write a DAT's contents. Contents are not a parameter, so `pars` cannot.

    Without this, every shader, script and callback body costs a second call —
    an `exec` doing `op(...).text = ...` — which lands outside the batch's undo
    block and outside its rollback. A GLSL network is mostly DAT text, so that
    was the common case, not an edge one.

    The refusals are deliberate. A DAT whose text is an *output* (Select, Null,
    Info, the script generators) accepts the assignment in TouchDesigner and
    then overwrites it on its next cook, which is a silent loss; `_dat_owns_its_text`
    is the measured list of the types that keep what is written to them.
    """
    if not getattr(target, "isDAT", False):
        raise TypeError(
            "'text' needs a DAT, but %s is a %s. Shader and script source "
            "belongs in a textDAT." % (target.path, target.OPType)
        )
    if not isinstance(text, str):
        raise TypeError(
            "'text' must be a string, got %s" % type(text).__name__
        )
    if not _dat_owns_its_text(target):
        raise TypeError(
            "%s is a %s, which computes its text every cook: an assignment "
            "here is overwritten on the next cook and lost without a word. "
            "Use a textDAT or tableDAT." % (target.path, target.OPType)
        )
    target.text = text


def m_op_create(params):
    """Create an operator, optionally setting parameters and wiring an input.

    `text` fills a DAT's contents in the same step — see `_set_dat_text`.

    A `position` is honoured exactly as given. Without one, the node is given a
    free spot instead of TouchDesigner's (0, 0), and one wired to a source
    lands to the right of it — see `_place_node`, which also names the case
    this does not cover: nodes wired by separate `op_connect` steps rather than
    by `connect` here are spread out, but not ordered by the chain.
    """
    _guard_scopes(params, params.get("parent") or "/")
    parent_comp = _resolve(params.get("parent") or "/")
    op_type = params.get("type")
    if not op_type:
        raise ValueError("op_create requires 'type'")
    created = parent_comp.create(op_type, params.get("name"))

    # Everything past the create can refuse — a parameter name that does not
    # exist, text on a DAT that computes its own, a source path that is not
    # there. Measured 2026-08-30: the node stayed behind in the network on
    # every one of those, so a caller who was told "no" still had to go and
    # find the leftover. Inside a batch the rollback removed it; a single
    # op_create had nothing to undo it.
    try:
        position = params.get("position")
        if position:
            created.nodeX, created.nodeY = float(position[0]), float(position[1])

        if params.get("pars"):
            _apply_pars(created, params["pars"])

        # After `pars`, because a parameter can load content from a file and
        # would otherwise race with what the caller asked to be in the DAT.
        if params.get("text") is not None:
            _set_dat_text(created, params["text"])

        sources = []
        for wiring in params.get("connect") or []:
            source = _resolve(wiring["from"])
            index = int(wiring.get("index", 0))
            source.outputConnectors[0].connect(created.inputConnectors[index])
            sources.append(source)

        # After the wiring, because the wiring says which node this one follows.
        if not position:
            _place_node(parent_comp, created, sources)
    except Exception:
        # Best effort, and second: if destroying the node throws as well, the
        # caller still has to hear why the create refused, not why the cleanup
        # did.
        try:
            created.destroy()
        except Exception:
            pass
        raise

    return _op_summary(created, include_pars=False)


def m_op_delete(params):
    paths = params.get("paths") or [params.get("path")]
    _guard_scopes(params, *paths)
    removed = []
    for path in paths:
        target = _resolve(path)
        removed.append(target.path)
        target.destroy()
    return {"deleted": removed}


def m_op_connect(params):
    # Both ends: a connection changes the wiring of the node it lands on as
    # much as the one it leaves.
    _guard_scopes(params, params.get("from"), params.get("to"))
    source = _resolve(params.get("from"))
    target = _resolve(params.get("to"))
    index = int(params.get("index", 0))
    source.outputConnectors[0].connect(target.inputConnectors[index])
    return {"from": source.path, "to": target.path, "index": index}


def m_op_disconnect(params):
    _guard_scopes(params, params.get("path"))
    target = _resolve(params.get("path"))
    index = int(params.get("index", 0))
    target.inputConnectors[index].disconnect()
    return {"path": target.path, "index": index}


class ParEvalError(ValueError):
    """A parameter was written, and evaluating it back raised.

    Its own class rather than a bare ValueError because the two mean opposite
    things to the caller: a ValueError out of the bridge says an argument was
    refused and nothing happened, while this says the write landed and only the
    read-back failed. `hints.py` maps the name.
    """


def _read_back(target, name, par):
    """Evaluate a parameter just written, naming it when TouchDesigner will not.

    A write is confirmed by reading it back, and the read-back is where a bad
    expression finally surfaces — TouchDesigner accepts `par.expr` as text and
    only complains when something asks for a value. Its complaint is the
    problem this wraps. Measured on build 2025.32460: an expression written to
    `ty` came back as `Error in /project1/REF/masses parameter t` — the
    parameter *group*, not the member, and no reason at all. An agent reading
    that goes looking at `tx` and `tz` as well, or at the wrong operator.

    Everything the message lacks is known here: the member's own name, that the
    write itself went through, and which of the two usual causes it could be.
    The causes are offered as candidates, not a diagnosis — TouchDesigner does
    not say which, and guessing on its behalf would be the confident wrong
    answer this project refuses to give.

    `tdError` is not referenced by name: this module is imported on the host to
    be shipped into TouchDesigner, where that class does not exist.
    """
    try:
        return par.eval()
    except Exception as exc:
        try:
            mode = str(par.mode).rsplit(".", 1)[-1]
        except Exception:
            mode = "unknown"
        # Only claim the group substitution when it actually happened: the
        # message sometimes does name the member ('expr0expr'), and asserting
        # otherwise would send the reader looking for a group that is not there.
        misnamed = ""
        if name not in str(exc):
            misnamed = (
                "That message does not name '%s' — TouchDesigner reports these "
                "against the parameter group ('t' for 'ty'), so look at the "
                "member you set, not at its siblings. " % name
            )
        raise ParEvalError(
            "%s: '%s' was written (mode %s) but evaluating it raised: %s\n"
            "%sTwo causes account for "
            "most of these: an expression that is only valid inside a cook "
            "(`me.inputVal` and friends), in which case the write is good and "
            "only this read-back failed; and a name that is not in scope in a "
            "Python parameter expression — `math.sin`, not `sin`. Check with "
            "td_op_info, which shows the stored expression. One caveat on "
            "'was written': that holds for a par_set on its own. Raised from a "
            "step inside a batch, this rolls the whole batch back, and the "
            "value is gone with it — reapply after fixing."
            % (target.path, name, mode, exc, misnamed)
        ) from exc


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
        applied[name] = _jsonable(_read_back(target, name, par))
    return applied


def m_par_set(params):
    _guard_scopes(params, params.get("path"))
    target = _resolve(params.get("path"))
    return {"path": target.path, "applied": _apply_pars(target, params["pars"])}


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

    The buffer is capped, and a request that hits the cap keeps nothing new and
    says `full`. Nothing empties it but `contact_sheet` or a `reset`, so a
    caller that stops asking for a sheet — or crashes — would otherwise leave
    TouchDesigner holding the frames until the process ends.
    """
    target = _resolve(params.get("path"))
    if target.family != "TOP":
        raise TypeError("capture needs a TOP, got a %s" % target.family)

    holder = me.parent()
    frames = holder.fetch(_CAPTURE_KEY, None)
    if frames is None or params.get("reset"):
        frames = []
        holder.store(_CAPTURE_KEY, frames)

    # Checked before the cook, so a refused frame costs neither the cook nor
    # the array: the buffer can therefore end one frame over the byte cap,
    # which is the price of not allocating a frame in order to reject it.
    held = sum(getattr(frame, "nbytes", 0) for frame in frames)
    if len(frames) >= _MAX_CAPTURE_FRAMES or held >= _MAX_CAPTURE_BYTES:
        return {
            "frames": len(frames),
            "path": target.path,
            "frame": absTime.frame,
            "full": True,
            "bytes": held,
            "limit": _MAX_CAPTURE_BYTES,
            "maxFrames": _MAX_CAPTURE_FRAMES,
            "captured": False,
        }

    target.cook(force=True)
    frames.append(target.numpyArray())
    return {
        "frames": len(frames),
        "path": target.path,
        "frame": absTime.frame,
        "captured": True,
    }


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
        step = max(1, frame.shape[1] // width)
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


def m_errors(params):
    """Every operator at or under one component reporting an error or warning.

    `path` defaults to the project rather than to `/`, and that default is the
    repair for a measured failure: from `/` this tool answered "nothing is
    wrong" about a project it had never reached. The walk is breadth-first, so
    the node budget is spent on whatever is widest at the shallow levels — and
    on an open session that is TouchDesigner's own interface. Measured
    2026-09-07 on build 2025.32460 against a session of 32,063 operators
    (22,635 under /ui, 9,351 under /sys, 11 in the project, and root lists /ui
    and /sys before /project1): the first 5,000 nodes were 3,979 from /ui and
    954 from /sys, the walk died at path level 5, and a warning planted eight
    levels below /project1 was invisible while fifteen findings from /ui and
    /sys came back as the whole answer. The same walk of the project alone
    costs 0.0-0.1 ms over three runs — a spread of three samples, not a
    ceiling — and is complete.

    Bounded still: every request runs on the main thread, nothing bounds how
    large a project can be, and a walk that stopped early says so rather than
    let a partial sweep read as "nothing is wrong here".
    """
    start = _resolve(params.get("path") or "/project1")
    found = []
    # The named operator is checked as well as its descendants. From `/` it
    # was covered as a child of root; from a named path it would not be, and a
    # reply silent about the very component it was pointed at is the same
    # partial answer read as a whole one that the budget marker exists for.
    # It is charged against the budget, hence the -1: uncharged, it made
    # `scanned` come back one above the `limit` printed beside it, and
    # "checked 5001 operators, at most 5000 are walked" reads as a bug in
    # whichever of the two numbers the reader trusts less.
    scanned, unvisited = _bounded_descendants(start, _MAX_WALK_NODES - 1)
    for target in [start] + scanned:
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
    result = {
        "count": len(found),
        "nodes": found,
        # Which subtree was walked. The host prints it in every branch: "no
        # operators are reporting errors" is a claim about one subtree, and
        # which one it was is the difference between a clean project and a
        # walk that never got there.
        "root": start.path,
        "scanned": len(scanned) + 1,
    }
    if unvisited:
        result["truncated"] = True
        result["notScanned"] = unvisited
        result["limit"] = _MAX_WALK_NODES
    return result


def _undo_depth():
    """How many entries the undo stack holds, or None if it cannot be read.

    None is not a number to fall back on. A rollback that cannot see the stack
    cannot tell what it put there, and the two ways of guessing are not
    symmetric: undoing one entry too few leaves visible junk in the network,
    while one too many silently reverts an edit the artist made by hand. So the
    caller does nothing rather than guess, and the note sweep is what is left.

    `Undo.undoStack` is a documented member and has never been absent on a live
    instance; this exists for the harness, where a stand-in `ui` may not carry
    it.
    """
    try:
        return len(ui.undo.undoStack)
    except Exception:
        return None


def m_batch(params):
    """Run several operations as one undoable, all-or-nothing block.

    TouchDesigner's own undo stack is the rollback mechanism, which means a
    failed batch leaves no partial network behind *and* an artist can undo the
    agent's work with Ctrl+Z like any other edit.
    """
    steps = params.get("ops") or []
    # `undo` and `redo` are public methods, so nothing stopped an agent from
    # putting them inside a batch — and the live acceptance measured what
    # happens then: two of them in a row reach past the batch's own entries
    # and take one that existed before it, which the rollback below cannot
    # give back (the difference goes negative and max(0, ...) hides it).
    # A batch is one atomic edit; walking the artist's history from inside it
    # is not an operation, it is the opposite of one.
    for index, step in enumerate(steps):
        if step.get("method") in ("undo", "redo"):
            raise ValueError(
                "step %d: '%s' cannot be a batch step — it walks the undo "
                "history the batch is being recorded into, and two of them "
                "reach past the batch to an edit made before it. Call it on "
                "its own, after the batch." % (index, step.get("method"))
            )
        # A nested batch is the same class of problem one level down. There is
        # one `_BATCH_NOTES` list, not a stack of them, so the inner batch
        # clears it on entry and again on exit: the outer batch's status notes
        # are gone, and a rollback of the outer one no longer knows which ones
        # to put back. It also opens a second undo block inside the first,
        # which `_UNDO_HELD` counts but the artist experiences as one Ctrl+Z
        # that undoes only part of what was asked for. Flattening is exact —
        # the steps run in the same order, in one block — so there is nothing
        # to lose by refusing.
        if step.get("method") == "batch":
            raise ValueError(
                "step %d: 'batch' cannot be a batch step — the notes and the "
                "undo block are per-request, not a stack, so the inner batch "
                "erases the outer one's rollback record. Put the inner steps "
                "into this batch's `ops` instead; the result is identical."
                % index
            )
    name = params.get("undo_name") or "td-atlas batch"
    results = []
    # The length of the stack before anything is opened. What this batch has
    # actually committed is the difference — read, never assumed; see the
    # rollback below.
    baseline = _undo_depth()
    # The panel is repainted at the end of the request, not here: the frame
    # does not render while this runs, so a mid-batch repaint would cost a
    # parameter write nobody could ever see. The table write is kept because
    # it survives — a TouchDesigner that dies mid-batch leaves the size of the
    # edit that was in flight behind in the network.
    _note_status(batch="%d ops running" % len(steps))
    ui.undo.startBlock(name)
    # Counted so a step that closes the block from under this one can give the
    # level back rather than leave the endBlock below to fail; see _UNDO_HELD.
    _UNDO_HELD[0] += 1
    del _BATCH_NOTES[:]
    try:
        for index, step in enumerate(steps):
            method = METHODS.get(step.get("method"))
            if method is None:
                raise ValueError(
                    "step %d: unknown method '%s'" % (index, step.get("method"))
                )
            step_params = dict(step.get("params") or {})
            # The owner named on the batch carries into every step, so an
            # agent that claimed a scope does not have to repeat itself on
            # each operation — and does not get refused by its own claim.
            if params.get("owner") and "owner" not in step_params:
                step_params["owner"] = params["owner"]
            results.append(method(step_params))
    except Exception:
        _UNDO_HELD[0] -= 1
        try:
            ui.undo.endBlock()
        except Exception:
            # Measured: when a step has already closed the block, this raises
            # "Cannot end non existent undo operation" — and letting it out
            # replaces the failure the caller needs to see with a complaint
            # about bookkeeping.
            pass
        # Exactly the entries this batch put on the stack, counted by reading
        # it rather than by predicting it: an empty block is thrown away on
        # endBlock, so a batch that failed on its first step has committed
        # nothing and must undo nothing. Undoing "at least once" instead was
        # measured popping the artist's own last edit onto the redo stack —
        # worse than the half-built network this rollback exists to prevent,
        # because a half-built network is visible and a reverted edit is not.
        depth = _undo_depth()
        committed = 0 if (depth is None or baseline is None) else max(0, depth - baseline)
        for _ in range(committed):
            try:
                ui.undo.undo()
            except Exception:
                break
        # A sweep, not the mechanism: the undos above take the notes with them
        # (measured). This only catches a note the count somehow missed, and
        # runs after the undos, never before — destroying one first pushes a
        # delete that the next undo pops, putting the note straight back.
        for note in _BATCH_NOTES:
            try:
                if note.valid:
                    note.destroy()
            except Exception:
                pass
        del _BATCH_NOTES[:]
        _note_status(batch="%d ops rolled back" % len(steps))
        raise
    _UNDO_HELD[0] -= 1
    del _BATCH_NOTES[:]
    ui.undo.endBlock()
    _note_status(batch="%d ops applied" % len(steps))
    return {"applied": len(results), "results": results}


def m_undo(_params):
    ui.undo.undo()
    return {"undoStack": list(ui.undo.undoStack)}


def m_redo(_params):
    ui.undo.redo()
    return {"redoStack": list(ui.undo.redoStack)}

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


def m_palette_load(params):
    """Load a .tox into a parent COMP and report the node that actually arrived.

    Exists so that installing a palette component is not an `exec` of
    `loadTox`, which hands nothing back: the node's name is not the caller's
    choice, so every field of the summary is read off the created operator
    rather than echoed from the request.

    Measured against a live 2025.32460, loading checker.tox repeatedly into
    one parent (the docs describe none of this):

    - `loadTox` returns the created OP — a `baseCOMP` for palette components.
    - It never fails on a name collision: a second load beside `checker`
      arrives as `checker1`, a third as `checker2`.
    - Assigning `.name` is the opposite. A name a sibling already holds raises
      `tdError: Invalid or duplicate operator name.` and does not renumber, so
      an explicit rename can fail after a load that succeeded. That is why the
      whole block is rolled back below and the collision is named in the
      error: the alternative is a component sitting in the network under a
      name the caller did not ask for and was never told about.
    - The undo block is not a courtesy. `loadTox` outside one pushes *nothing*
      onto `ui.undo.undoStack`, so the load cannot be undone at all; inside
      one it becomes a single named entry, and undoing it invalidates the
      loaded COMP and removes it from the parent.
    - `errors()` and `warnings()` here are read *before* any forced cook, and
      came back empty on a freshly loaded component. So an empty pair means
      "nothing yet", not "this component works": a node that will fail on its
      first cook still looks clean. Forcing a cook to find out is not worth it
      at kantanMapper's cost — td_health is the tool for that question.

    Cost, measured on the same instance: 0.006 s for checker (20 operators),
    0.054 s for cameraViewport (246), 1.08 s for kantanMapper (4,079). The
    largest palette components therefore stall the main thread for about a
    second — bounded, but visible as a dropped frame.
    """
    _guard_scopes(params, params.get("parent") or "/")
    file = params.get("file")
    if not file:
        raise ValueError("palette_load requires 'file'")
    parent_comp = _resolve(params.get("parent") or "/")
    if not hasattr(parent_comp, "loadTox"):
        raise TypeError(
            "%s (%s) is not a COMP and cannot hold a component"
            % (parent_comp.path, parent_comp.OPType)
        )

    ui.undo.startBlock("td-atlas load %s" % os.path.basename(file))
    try:
        created = parent_comp.loadTox(file)
        if created is None:
            raise RuntimeError("loadTox('%s') created no operator" % file)

        name = params.get("name")
        if name:
            loaded_as = created.name
            try:
                created.name = name
            except Exception as exc:
                # tdError's message says only "Invalid or duplicate operator
                # name", which leaves the caller guessing which of the two it
                # was and which name to pick instead.
                raise ValueError(
                    "loaded %s but could not rename it to '%s': %s. A sibling "
                    "in %s may already hold that name — retry with another "
                    "name, or omit it and take the '%s' TouchDesigner chose."
                    % (loaded_as, name, exc, parent_comp.path, loaded_as)
                )

        position = params.get("position")
        if position:
            created.nodeX, created.nodeY = float(position[0]), float(position[1])
    except Exception:
        ui.undo.endBlock()
        try:
            ui.undo.undo()
        except Exception:
            pass
        raise
    ui.undo.endBlock()

    summary = _op_summary(created)
    summary["file"] = file
    return summary


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
    # Refused rather than answered emptily. `findChildren` is a COMP method
    # (index: py_members lists it on COMP alone), while `children` is an OP
    # member that is simply empty on everything else — so walking `children`
    # would report a leaf operator as a subtree with nothing wrong in it,
    # which is a confident wrong answer where there was an error before.
    if getattr(target, "family", None) != "COMP":
        raise TypeError(
            "health_sample walks a component; %s is a %s (%s). Give it the "
            "path of the container holding the network to check."
            % (target.path, target.family, target.OPType)
        )

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
    # Every failed read is carried out rather than swallowed. Swallowing one
    # here is what made a broken read indistinguishable from a clean project:
    # the report said "nothing wrong found" precisely when the check for the
    # thing it was built to catch had not run. Nothing is retried and nothing
    # is inferred — an unread surface is reported as unread.
    script_errors_unread = []

    script_errors_root = ""
    try:
        script_errors_root = target.scriptErrors(recurse=True) or ""
    except Exception as exc:
        script_errors_unread.append(
            "%s (recursive read: %s: %s)" % (target.path, type(exc).__name__, exc)
        )
    walk_scripts = bool(script_errors_root)

    script_errors = {}
    nodes = []
    # The same accounting as `errors`, and for the same reason: the subtree
    # root is examined here too — its own scriptErrors are read below — so it
    # is charged against the budget and counted in `scanned`. The two methods
    # used to disagree about whether the root was one of the operators
    # walked, which made the same number mean two things.
    scanned, unvisited = _bounded_descendants(target, _MAX_WALK_NODES - 1)
    for child in scanned:
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
                except Exception as exc:
                    script_errors_unread.append(
                        "%s (%s: %s)" % (child.path, type(exc).__name__, exc)
                    )
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
        except Exception as exc:
            script_errors_unread.append(
                "%s (%s: %s)" % (target.path, type(exc).__name__, exc)
            )

    licence = {}
    try:
        licence = {
            "type": str(getattr(licenses, "type", "")),
            "commercial": bool(getattr(licenses, "commercial", False)),
        }
    except Exception:
        pass

    sample = {
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
        "scanned": len(scanned) + 1,
    }
    if unvisited:
        # A health verdict over part of a network must not read as a verdict
        # over the network: the host turns this into a finding of its own.
        sample["truncated"] = True
        sample["notScanned"] = unvisited
        sample["limit"] = _MAX_WALK_NODES
    if script_errors_unread:
        # A handful of examples, not five thousand: the count carries the
        # scale and the reasons repeat.
        sample["scriptErrorsUnread"] = script_errors_unread[:5]
        sample["scriptErrorsUnreadCount"] = len(script_errors_unread)
    return sample


def _extension_targets(params):
    """The paths an extension build writes to, for the scope guard.

    Both the parent and the child are named in the create case: the parameters
    and the DAT land inside the new COMP, which is a subtree of its own.
    """
    path = params.get("path")
    if path:
        return (path,)
    parent = params.get("parent")
    name = params.get("name")
    if parent and name:
        return (parent, "%s/%s" % (parent.rstrip("/"), name))
    return (parent,) if parent else ()


def _extension_expr(dat_name, class_name):
    """The Extension Object code that actually resolves the class.

    Measured on a live 2025.32460 against a textDAT named DemoExt holding
    `class DemoExt`, with every form pulsed through Re-Init Extensions and the
    result read back off `comp.extensions`:

    - `op('./DemoExt').module.DemoExt(me)` — works. This is also the form
      TouchDesigner's own components carry: of the 258 non-empty Extension
      Object expressions under /ui and /sys within six levels, 257 are this
      form or the equivalent `me.mod.X.X(me)` — the Component Editor itself
      (`/sys/TDDialogs/CompEditor`) among them. (Counted twice: the first pass
      read only the first 40 rows it printed and this docstring said 40, which
      was a listing limit, not a count.)
    - `DemoExt(me)` — the form the Extensions wiki page shows — leaves
      `extensions[0]` as None. So does `mod('DemoExt').DemoExt(me)`,
      `mod('./DemoExt').DemoExt(me)` and `iop.DemoExt`.

    And the failure is silent: after each of those, `errors(recurse=False)` and
    `warnings(recurse=False)` on the COMP were both empty and
    `extensionsReady` was True. Nothing but the textport says the extension
    does not exist. That is the whole reason this method reads the result back
    instead of reporting what it set.
    """
    return "op('./%s').module.%s(me)" % (dat_name, class_name)


def m_extension_add(params):
    """Attach a Python class to a COMP as an extension, in one block.

    Replaces the five blind steps this used to take through `exec` — create
    the COMP, create the DAT, write the text, set three sequence parameters,
    re-initialise — of which the last one fails without saying so (see
    `_extension_expr`).

    Measured on 2025.32460, and none of it documented:

    - A fresh COMP reports `seq.ext.numBlocks == 1`, yet `par.ext1object`
      already exists and assigning it grows the sequence to 2. The sequence is
      grown explicitly below rather than relying on that one-block window,
      which was only measured for the next block, not for an arbitrary index.
      The wiki says a component has four extensions; the sequence took six.
    - `reinitextensions` takes effect within the same request: the extension
      was callable through `ext` on the next line, with no frame in between.
    - A class that raises in `__init__` leaves `extensions[index]` as None with
      the COMP reporting no error, exactly like the wrong expression does. The
      real message is recovered by evaluating the same expression through
      `comp.evalExpression`, which raises it on the host side of the bridge.
      That re-runs `__init__` a second time — acceptable only because the
      first run already failed.

    A class whose `__init__` fails is *not* rolled back. The structure asked
    for is there and correct, the DAT is addressable, and the caller gets both
    paths plus the error to fix in place; `undo` removes the whole block if
    they would rather start over. Structural failure — the COMP, the DAT, a
    parameter that does not exist — is rolled back the way `palette_load` does
    it.
    """
    _guard_scopes(params, *_extension_targets(params))

    path = params.get("path")
    parent = params.get("parent")
    name = params.get("name")
    if path and (parent or name):
        raise ValueError(
            "extension_add takes either 'path' (an existing COMP) or "
            "'parent' plus 'name' (a COMP to create), not both"
        )
    if not path and not (parent and name):
        raise ValueError(
            "extension_add requires 'path', or 'parent' and 'name' together"
        )

    class_name = params.get("class_name")
    if not class_name or not str(class_name).isidentifier():
        raise ValueError(
            "extension_add requires 'class_name' as a Python identifier, got %r"
            % (class_name,)
        )
    code = params.get("code")
    if not code:
        raise ValueError("extension_add requires 'code' defining the class")
    index = int(params.get("index") or 0)
    if index < 0:
        raise ValueError("extension_add needs a non-negative 'index', got %d" % index)
    extension_name = params.get("extension_name") or ""
    if extension_name and not str(extension_name).isidentifier():
        raise ValueError(
            "'extension_name' becomes an attribute of ext, so it must be a "
            "Python identifier, got %r" % (extension_name,)
        )
    promote = bool(params.get("promote", True))

    # Resolve and refuse before opening the block, so a wrong target costs no
    # undo entry — same order as palette_load.
    if path:
        comp = _resolve(path)
        # The capability check rather than the family name: measured, a
        # noiseTOP has no ext0object parameter at all, and this is the exact
        # parameter about to be written.
        if getattr(comp.par, "ext0object", None) is None:
            raise TypeError(
                "%s (%s) has no Extensions page, so it cannot hold a Python "
                "extension — aim at a COMP" % (comp.path, comp.OPType)
            )
        parent_comp = None
    else:
        parent_comp = _resolve(parent)
        if not hasattr(parent_comp, "create"):
            raise TypeError(
                "%s (%s) is not a COMP and cannot hold a new component"
                % (parent_comp.path, parent_comp.OPType)
            )
        comp = None

    expr = _extension_expr(class_name, class_name)
    pars = {
        "ext%dobject" % index: expr,
        "ext%dname" % index: extension_name,
        "ext%dpromote" % index: promote,
    }

    ui.undo.startBlock("td-atlas extension %s" % class_name)
    created_comp = False
    try:
        if comp is None:
            comp = parent_comp.create(baseCOMP, name)
            created_comp = True
            position = params.get("position")
            if position:
                comp.nodeX, comp.nodeY = float(position[0]), float(position[1])
            else:
                # Through the shared placer, not a second copy of the rule:
                # two layouts in one file drift apart. Nothing is wired here,
                # so the new COMP simply lands beside what is already in the
                # parent instead of on top of it at (0, 0).
                _place_node(parent_comp, comp)

        dat = comp.op("./%s" % class_name)
        if dat is None:
            dat = comp.create(textDAT, class_name)
        elif dat.family != "DAT":
            raise TypeError(
                "%s already holds a %s named '%s', and the extension needs a "
                "DAT of that name — rename it, or use a different class name"
                % (comp.path, dat.OPType, class_name)
            )
        dat.text = code

        # Grow the sequence before writing to a block that may not exist yet.
        try:
            blocks = comp.seq.ext.numBlocks
            if blocks < index + 1:
                comp.seq.ext.numBlocks = index + 1
        except Exception as exc:
            raise ValueError(
                "%s cannot hold extension %d: %s" % (comp.path, index, exc)
            )

        _apply_pars(comp, pars)
        comp.par.reinitextensions.pulse()
    except Exception:
        ui.undo.endBlock()
        try:
            ui.undo.undo()
        except Exception:
            pass
        raise
    ui.undo.endBlock()

    resolved = extension_name or class_name
    extensions = list(comp.extensions or [])
    obj = extensions[index] if index < len(extensions) else None
    result = {
        "path": comp.path,
        "dat": dat.path,
        "createdComp": created_comp,
        "index": index,
        "extension": resolved,
        "promote": promote,
        "set": {"ext%dobject" % index: expr, **{
            k: v for k, v in pars.items() if not k.endswith("object")
        }},
        "ok": obj is not None,
        "object": _clip(repr(obj)) if obj is not None else None,
        "error": None,
        "reachable": None,
    }
    if obj is None:
        # Nothing on the COMP reports this, so re-run the expression to get the
        # message TouchDesigner only wrote to the textport.
        try:
            comp.evalExpression(expr)
            result["error"] = (
                "the extension did not initialise, and re-evaluating %s "
                "raised nothing — no message is available" % expr
            )
        except Exception as exc:
            result["error"] = "%s: %s" % (type(exc).__name__, _clip(exc))
        return result

    try:
        result["reachable"] = getattr(comp.ext, resolved) is not None
    except Exception as exc:
        result["reachable"] = False
        result["error"] = "extension built but ext.%s is unreachable: %s" % (
            resolved,
            _clip(exc),
        )
    return result

# -- where a node lands ------------------------------------------------------
#
# A node's own size is never assumed: `nodeWidth`/`nodeHeight` are read off the
# live operator, because tiles differ by type and by how the artist has resized
# them. Measured on build 2025.32460: noiseTOP 130x90, outTOP 130x72,
# geometryCOMP 160x130, annotateCOMP 382x288. A single hard-coded tile size
# would therefore overlap outTOP's neighbours and leave a hole beside geo1.
#
# Only the whitespace between tiles is a choice, and it is one, not a
# measurement: 40 units across is about a third of a default tile, enough for
# the wire between two nodes to be visible, and 20 down keeps a column compact.
_LAYOUT_GAP_X = 40.0
_LAYOUT_GAP_Y = 20.0

# Cap on the scan below, so a crowded network cannot turn one create into a
# long main-thread stall. Reaching it is not a failure: the fallback position
# past the right edge of everything is free by construction.
_LAYOUT_MAX_PROBES = 400


def _node_box(target):
    """(left, bottom, width, height) of one node tile, in network units.

    `nodeX`/`nodeY` are the left and *bottom* edges of the tile, not its centre
    — the centre has its own pair, `nodeCenterX`/`nodeCenterY` (OP class page,
    confirmed live: a fresh noiseTOP at nodeX/nodeY 0,0 reports nodeCenterX 65,
    nodeCenterY 45 with a 130x90 tile). Treating nodeY as a centre puts every
    computed row half a tile out.
    """
    return (
        float(target.nodeX),
        float(target.nodeY),
        float(target.nodeWidth),
        float(target.nodeHeight),
    )


def _boxes_clash(one, other, gap_x=_LAYOUT_GAP_X, gap_y=_LAYOUT_GAP_Y):
    """True when two tiles overlap, or sit closer than the gap.

    The gap is part of the test rather than a nicety applied afterwards:
    touching tiles are as unreadable as overlapping ones in the network editor.
    """
    ax, ay, aw, ah = one
    bx, by, bw, bh = other
    return (
        ax - gap_x < bx + bw
        and bx < ax + aw + gap_x
        and ay - gap_y < by + bh
        and by < ay + ah + gap_y
    )


def _occupied_boxes(parent_comp, exclude=None):
    """The tiles already in a network, skipping one node.

    `exclude` is the node being placed: it exists by the time its position is
    computed (its size cannot be read before it does), and a node always
    clashes with itself.

    A child whose tile cannot be read is skipped rather than fatal — the
    placement of one new node is not worth failing over a sibling the read did
    not understand, and the worst case is a tile it does not avoid.
    """
    boxes = []
    exclude_path = getattr(exclude, "path", None)
    for child in parent_comp.children:
        if exclude_path is not None and child.path == exclude_path:
            continue
        try:
            boxes.append(_node_box(child))
        except Exception:
            continue
    return boxes


def _free_position(boxes, width, height, seed):
    """The first position at or below-right of `seed` that clashes with nothing.

    Scans columns rightward and, within a column, downward — the direction a
    TouchDesigner network reads, since nodeY grows upward. When the scan runs
    out of probes it returns a spot past the right edge of every tile, which is
    free by construction: no box can reach past its own right edge plus the gap.
    """
    seed_x, seed_y = float(seed[0]), float(seed[1])
    if not boxes:
        return (seed_x, seed_y)

    step_x = width + _LAYOUT_GAP_X
    step_y = height + _LAYOUT_GAP_Y
    probes = 0
    columns = 8
    rows = max(1, _LAYOUT_MAX_PROBES // columns)
    for col in range(columns):
        for row in range(rows):
            if probes >= _LAYOUT_MAX_PROBES:
                break
            probes += 1
            candidate = (seed_x + col * step_x, seed_y - row * step_y, width, height)
            if not any(_boxes_clash(candidate, box) for box in boxes):
                return (candidate[0], candidate[1])

    right = max(box[0] + box[2] for box in boxes)
    return (right + _LAYOUT_GAP_X, seed_y)


def _placement_seed(boxes, height, sources):
    """Where to start looking, given what the new node is wired to.

    With an input source, the seed is immediately to its right, centred on it:
    that is what makes a chain built in one batch read left to right, which is
    how TouchDesigner networks are laid out. Centring rather than aligning
    bottoms matters because tile heights differ (130x90 against 130x72), and a
    bottom-aligned chain of mixed types looks stepped.

    With no source, the seed is past the right edge of everything already
    there, top-aligned with the topmost tile — so a second node created without
    a position lands beside the first instead of on top of it.
    """
    if sources:
        try:
            sx, sy, sw, sh = _node_box(sources[0])
            return (sx + sw + _LAYOUT_GAP_X, sy + (sh - height) / 2.0)
        except Exception:
            pass
    if not boxes:
        return (0.0, 0.0)
    right = max(box[0] + box[2] for box in boxes)
    top = max(box[1] + box[3] for box in boxes)
    return (right + _LAYOUT_GAP_X, top - height)


def _place_node(parent_comp, created, sources=()):
    """Give a node with no requested position a spot of its own.

    Exists because the alternative is what the bridge did before: a create
    without a position left the node wherever TouchDesigner put it, which is
    (0, 0) for every one of them, so an agent building ten nodes built one
    visible node with nine underneath it.

    A position the caller asked for is never touched — see `m_op_create`. This
    runs after any wiring, because the wiring is what says which node the new
    one belongs to the right of.
    """
    try:
        width, height = float(created.nodeWidth), float(created.nodeHeight)
    except Exception:
        # A node whose tile size cannot be read cannot be placed honestly;
        # leaving it where TouchDesigner put it is the smaller wrong.
        return None
    boxes = _occupied_boxes(parent_comp, exclude=created)
    seed = _placement_seed(boxes, height, sources)
    x, y = _free_position(boxes, width, height, seed)
    created.nodeX, created.nodeY = x, y
    return (x, y)


# -- notes in the network ----------------------------------------------------

# The parameters that hold an Annotate's text and colour are *custom*
# parameters, added by the component's own default setup, not built-ins: a
# freshly created annotateCOMP reports pages ['Text', 'Settings', 'OP Viewer',
# 'About'] on top of the built-in Annotate page (measured live, 2025.32460).
# That is why every read below is defensive — an annotateCOMP saved by an older
# TouchDesigner, or stripped of its extension, has the built-in page and no
# 'Bodytext', and the honest answer there is a null field rather than a
# traceback.
# TouchDesigner's undo blocks nest and count, and creating an annotateCOMP
# commits one of those levels from under the caller: its own OnCreate does undo
# bookkeeping, so a `startBlock` / `create(annotateCOMP)` / `endBlock` sequence
# fails on the endBlock with 'Cannot end non existent undo operation' (measured
# on 2025.32460; with two levels open, exactly one survived). m_batch records
# here that it is holding a level, so a note created inside a batch can give
# that level back instead of leaving the batch's own endBlock to raise — which
# would report a batch that applied cleanly as a failure.
#
# Giving the level back is not the whole story: the committed one is a separate
# undo entry that has to be rolled back too. m_batch does that by reading the
# stack, not by counting levels here — see its rollback.
#
# Per-request state, and safe as such: a request runs to completion on the main
# thread before the next one starts, and TouchDesigner re-executes this module
# between requests, so it cannot leak across them either.
_UNDO_HELD = [0]

# Notes the running batch created, swept up if the rollback below could not
# reach them. Not the rollback mechanism: that is m_batch reading the stack.
#
# What was measured on 2025.32460, stack length read at every step, since none
# of it is documented and two of these were got wrong before they were read:
#
# - inside an open block, creating and wiring nodes pushes nothing; the entry
#   appears when the block is *started* and collects the work (83 -> 84 on
#   startBlock, no growth as nodes were created);
# - a block that ends up empty is thrown away on endBlock (83 -> 84 -> 83), so
#   a batch that failed on its first step has committed nothing at all;
# - creating an annotateCOMP commits the open block — the entry stays, holding
#   everything done so far including the note — and the startBlock that gives
#   the level back pushes a second entry, so a batch with a note in the middle
#   finishes as two committed entries;
# - undo() pops one entry, and one pop too many reaches into the artist's own
#   history: it was measured resurrecting operators deleted before the batch
#   began, and popping an unrelated 'artist edit' onto the redo stack.
#
# Hence the rule the rollback follows: undo exactly the entries the stack grew
# by while this batch ran, which is zero when nothing was committed.
_BATCH_NOTES = []

_ANNOTATE_TYPE = "annotateCOMP"

# request field -> parameter name on the component.
_ANNOTATE_TEXT_PARS = (
    ("text", "Bodytext"),
    ("title", "Titletext"),
    ("font_size", "Bodyfontsize"),
    ("mode", "Mode"),
)
_ANNOTATE_COLOR_PARS = ("Backcolorr", "Backcolorg", "Backcolorb")


def _annotate_par(target, name):
    return getattr(target.par, name, None)


def _annotate_require(target, name, field):
    par = _annotate_par(target, name)
    if par is None:
        raise ValueError(
            "%s has no '%s' parameter, so '%s' cannot be written. This "
            "annotateCOMP is missing the default-setup custom parameters that "
            "carry its text and colour." % (target.path, name, field)
        )
    return par


def _annotate_apply(target, params):
    """Write the requested fields onto an Annotate, and report what landed."""
    written = {}
    for field, par_name in _ANNOTATE_TEXT_PARS:
        if params.get(field) is None:
            continue
        par = _annotate_require(target, par_name, field)
        par.val = params[field]
        written[field] = _jsonable(par.eval())

    color = params.get("color")
    if color:
        if len(color) < 3:
            raise ValueError("annotate colour needs three components, r g b")
        for par_name, value in zip(_ANNOTATE_COLOR_PARS, color):
            _annotate_require(target, par_name, "color").val = float(value)
        written["color"] = [float(c) for c in color[:3]]
    if params.get("alpha") is not None:
        _annotate_require(target, "Backcoloralpha", "alpha").val = float(
            params["alpha"]
        )
        written["alpha"] = float(params["alpha"])

    size = params.get("size")
    if size:
        if len(size) < 2:
            raise ValueError("annotate size needs two numbers, width and height")
        target.nodeWidth, target.nodeHeight = float(size[0]), float(size[1])
        written["size"] = [float(size[0]), float(size[1])]

    position = params.get("position")
    if position:
        target.nodeX, target.nodeY = float(position[0]), float(position[1])
        written["position"] = [float(position[0]), float(position[1])]
    return written


def m_annotate(params):
    """Leave a note in the network, or rewrite one that is already there.

    The half of the pair an agent writes. A network an agent built says nothing
    about *why*; an Annotate is where that goes, in the one place the person who
    opens the project will actually look — the network editor itself.

    Measured live on build 2025.32460, because none of this is documented:

    - `create(annotateCOMP, 'my_note')` ignores the name and produces
      'annotate1'. The component's own OnCreate renames it, so the name is not
      the caller's to choose at create time; assigning `.name` afterwards does
      stick, and that is what `name` below does. The reply always carries the
      path the note actually has.
    - A fresh Annotate also places itself, at (-300, 100) relative to its
      parent, every time — two notes created in a row sit exactly on top of
      each other. So a note with no requested position goes through the same
      placement as any other node.
    - `nodeX`/`nodeY`/`nodeWidth`/`nodeHeight` assigned after creation hold
      across frames; the box really is sized by the node tile, not by a
      parameter.
    - A newline written straight into `Bodytext` round-trips as a newline. The
      wiki says to use an expression for newlines; for a plain assignment
      through Python it is unnecessary.

    Pass `path` to rewrite an existing note instead of adding another — an
    agent that reruns should not leave a stack of duplicates.
    """
    path = params.get("path")
    if path:
        _guard_scopes(params, path)
        target = _resolve(path)
        if target.OPType != _ANNOTATE_TYPE:
            raise TypeError(
                "%s is a %s, not an %s — 'path' here names a note to rewrite, "
                "not the network to put one in (that is 'parent')"
                % (target.path, target.OPType, _ANNOTATE_TYPE)
            )
        ui.undo.startBlock("td-atlas annotate %s" % target.name)
        try:
            written = _annotate_apply(target, params)
        finally:
            # No `undo()` on failure here, unlike the create branch below: this
            # branch only writes parameters, and if a build ever stops
            # recording those, an undo would roll back somebody else's edit
            # instead of this one. A half-written note is visible; a silently
            # reverted neighbour is not.
            ui.undo.endBlock()
        summary = _op_summary(target)
        summary["written"] = written
        summary["created"] = False
        return summary

    parent = params.get("parent") or "/"
    _guard_scopes(params, parent)
    parent_comp = _resolve(parent)
    if not hasattr(parent_comp, "create"):
        raise TypeError(
            "%s (%s) is not a COMP and cannot hold a note"
            % (parent_comp.path, parent_comp.OPType)
        )

    # Deliberately outside any block of ours: the create closes one undo level
    # from under the caller (see _UNDO_HELD), so a block opened before it could
    # not be closed afterwards.
    created = parent_comp.create(_ANNOTATE_TYPE)
    # One startBlock either way — it gives back the level the create ate when a
    # batch is holding one, and otherwise makes the writes below a single entry.
    own_block = not _UNDO_HELD[0]
    if not own_block:
        # The create just committed the batch's level, with everything the
        # batch had done up to here in it. The startBlock below gives the level
        # back; the batch counts the committed entry by reading the stack, so
        # nothing needs declaring here beyond the note itself, for the sweep.
        _BATCH_NOTES.append(created)
    ui.undo.startBlock("td-atlas annotate")
    try:
        name = params.get("name")
        if name:
            made_as = created.name
            try:
                created.name = name
            except Exception as exc:
                raise ValueError(
                    "the note was created as %s but could not be renamed to "
                    "'%s': %s. A sibling in %s may already hold that name — "
                    "retry with another name, or omit it."
                    % (made_as, name, exc, parent_comp.path)
                )
        written = _annotate_apply(target=created, params=params)
        if not params.get("position"):
            _place_node(parent_comp, created)
    except Exception:
        # Destroyed rather than undone: `ui.undo.undo()` terminates every open
        # block and would roll back whatever the enclosing batch had already
        # applied, which is somebody else's work when the caller is m_batch.
        try:
            created.destroy()
        except Exception:
            pass
        if own_block:
            ui.undo.endBlock()
        raise
    if own_block:
        ui.undo.endBlock()

    summary = _op_summary(created)
    summary["written"] = written
    summary["created"] = True
    return summary


def _annotate_view(target, siblings):
    """One note as data, plus which nodes its box sits over."""
    def value(par_name):
        par = _annotate_par(target, par_name)
        if par is None:
            return None
        try:
            return _jsonable(par.eval())
        except Exception:
            return None

    box = _node_box(target)
    left, bottom, width, height = box
    covers = []
    for other in siblings:
        if other.path == target.path or other.OPType == _ANNOTATE_TYPE:
            continue
        try:
            ox, oy, ow, oh = _node_box(other)
        except Exception:
            continue
        cx, cy = ox + ow / 2.0, oy + oh / 2.0
        if left <= cx <= left + width and bottom <= cy <= bottom + height:
            covers.append(other.path)

    color = [value(name) for name in _ANNOTATE_COLOR_PARS]
    return {
        "path": target.path,
        "name": target.name,
        "title": value("Titletext"),
        "text": value("Bodytext"),
        "mode": value("Mode"),
        "fontSize": value("Bodyfontsize"),
        "color": None if color[0] is None else color,
        "alpha": value("Backcoloralpha"),
        "position": [left, bottom],
        "size": [width, height],
        # Geometric, not TouchDesigner's own answer: the tiles whose centre
        # falls inside this box. The component's Enclose Operators feature
        # keeps no list this can be read from, so a node the artist dragged
        # half out of the box counts as outside.
        "covers": covers,
    }


def m_annotations(params):
    """Read every note in a subtree — including the ones a person wrote.

    The other half of the pair, and the reason it exists: an artist can leave
    an agent a brief in the project itself, as an Annotate beside the nodes it
    is about, and without this the agent never sees it. So this reads notes
    regardless of who wrote them, and reports the nodes each note's box sits
    over, which is what says *what* the note is about.

    Annotates are Utility nodes but do appear in `children` (measured), so the
    walk below finds them without asking for utilities specially.
    """
    root = _resolve(params.get("path") or "/")
    depth = params.get("depth")
    depth = 8 if depth is None else int(depth)

    found = []

    def walk(comp, level):
        if not hasattr(comp, "children"):
            return
        children = list(comp.children)
        for child in children:
            if child.OPType == _ANNOTATE_TYPE:
                try:
                    found.append(_annotate_view(child, children))
                except Exception as exc:
                    found.append({"path": child.path, "error": str(exc)})
            if level < depth:
                walk(child, level + 1)

    if root.OPType == _ANNOTATE_TYPE:
        parent_comp = root.parent()
        siblings = list(parent_comp.children) if parent_comp is not None else [root]
        found.append(_annotate_view(root, siblings))
    else:
        walk(root, 1)
    return {"root": root.path, "count": len(found), "annotations": found}


# -- node flags --------------------------------------------------------------
#
# The set and the spelling come from the OP and COMP class pages in the index.
# Which of them a family actually accepts was measured live on 2025.32460 by
# flipping each on a noiseTOP, geometryCOMP, noiseSOP, noiseCHOP and textDAT,
# reading it back and restoring it, because the documentation does not say:
#
# - every flag below except the two noted is readable *and* settable on all
#   five families, including `display` and `render` on a TOP or a DAT, where
#   they have no visible effect;
# - `allowCooking` raises on anything but a COMP ("This flag can only be
#   disabled for COMPs"), which the class page does state;
# - `pickable` exists on COMP only — reading it on a TOP raises
#   tdAttributeError.
#
# There is no OP attribute named `clone`. The network editor's clone
# relationship lives in the Clone Master *parameter* (`clone`), which is
# td_set_params' job; the flag half of it is `cloneImmune`, which is here.
NODE_FLAGS = (
    "display",
    "render",
    "bypass",
    "lock",
    "expose",
    "viewer",
    "activeViewer",
    "cloneImmune",
    "allowCooking",
    "selected",
    "pickable",
)

# Flags whose absence is a fact about the family rather than a fault, so the
# refusal can say which family does have them.
_FLAG_FAMILIES = {
    "pickable": "COMP",
    "allowCooking": "COMP (other families can read it but not disable it)",
}


def _flag_snapshot(target):
    """Every flag this operator actually has, and the ones it does not."""
    flags = {}
    unavailable = []
    for name in NODE_FLAGS:
        try:
            flags[name] = bool(getattr(target, name))
        except Exception:
            unavailable.append(name)
    out = {
        "path": target.path,
        "type": target.OPType,
        "family": target.family,
        "flags": flags,
        "unavailable": unavailable,
    }
    clones = getattr(target, "clones", None)
    if clones is not None:
        # Read-only, and the reason `clone` is not in NODE_FLAGS: this is the
        # only clone information an OP exposes as an attribute.
        out["clones"] = [c.path for c in clones]
    return out


def m_flags(params):
    """Read the flags that decide whether a node runs and what is visible.

    Before this, the bridge read one flag, `bypass`, and only as a field of a
    health sample. A bypassed node, a COMP with its display flag off and a
    node with cooking disabled all look identical in a parameter dump and in a
    network listing, and all three make a correct-looking network produce
    nothing — which is exactly the silent failure this connector exists to
    surface.

    `unavailable` is not padding: it says which flags this operator genuinely
    does not have, so a caller can tell "off" from "not a thing here".
    """
    paths = params.get("paths") or [params.get("path")]
    return {"ops": [_flag_snapshot(_resolve(path)) for path in paths]}


def m_flags_set(params):
    """Set node flags, and refuse audibly when one will not take.

    Written this way because the failure it replaces is silent. TouchDesigner
    accepts `pickable` nowhere but a COMP and refuses `allowCooking = False`
    outside one; a bare `setattr` on the wrong family either raises a bare
    tdError, which reaches an agent with no mapped recovery, or — for a flag
    that exists but does nothing here — appears to succeed. So every write is
    read back and compared, and a value that did not land is reported as a
    refusal naming the flag, the family and the original message.

    Rollback is done here by hand rather than through `ui.undo.undo()`: a flag
    change *is* recorded — measured on 2025.32460, flipping `bypass` puts
    TouchDesigner's own 'Change Bypass Flag' on the stack, under that name
    rather than the block's — but `undo()` terminates every open block and pops
    whatever is on top, which inside a batch is the batch's own work. Restoring
    the values this call read is the only rollback that is certain to undo just
    this call.
    """
    _guard_scopes(params, params.get("path"))
    flags = params.get("flags") or {}
    if not flags:
        raise ValueError(
            "flags_set needs 'flags', a mapping of flag name to true/false"
        )
    unknown = [name for name in flags if name not in NODE_FLAGS]
    if unknown:
        raise ValueError(
            "unknown flag(s) %s. The flags an operator has are: %s"
            % (", ".join(sorted(unknown)), ", ".join(NODE_FLAGS))
        )

    target = _resolve(params.get("path"))
    before = {}
    applied = {}
    ui.undo.startBlock("td-atlas flags %s" % target.name)
    try:
        for name, wanted in flags.items():
            wanted = bool(wanted)
            try:
                was = bool(getattr(target, name))
            except Exception as exc:
                only = _FLAG_FAMILIES.get(name)
                raise ValueError(
                    "%s (%s) has no '%s' flag%s: %s"
                    % (
                        target.path,
                        target.OPType,
                        name,
                        " — it exists on %s only" % only if only else "",
                        exc,
                    )
                )
            before[name] = was
            try:
                setattr(target, name, wanted)
            except Exception as exc:
                only = _FLAG_FAMILIES.get(name)
                raise ValueError(
                    "%s (%s) refused '%s' = %s%s: %s"
                    % (
                        target.path,
                        target.OPType,
                        name,
                        wanted,
                        " — settable on %s only" % only if only else "",
                        exc,
                    )
                )
            landed = bool(getattr(target, name))
            if landed != wanted:
                raise ValueError(
                    "%s (%s) took '%s' = %s without complaint but reads back "
                    "%s. Nothing was changed."
                    % (target.path, target.OPType, name, wanted, landed)
                )
            applied[name] = wanted
    except Exception:
        for name, was in before.items():
            try:
                setattr(target, name, was)
            except Exception:
                pass
        ui.undo.endBlock()
        raise
    ui.undo.endBlock()
    return {
        "path": target.path,
        "type": target.OPType,
        "family": target.family,
        "before": before,
        "applied": applied,
    }


# Three methods were removed on 2026-09-06 rather than kept for symmetry:
# `perf` (every field of it is in `health_sample`, which the host does call),
# `par_get` (`op_info` returns the same parameter values in the same shape),
# and `save`, which called `project.save()` — a Save As that moves the artist's
# working file. Nothing on the host had ever called any of the three and no
# test covered them, so a method that could rewrite someone's project sat in
# the table reachable by anything that could reach the bridge. `save_tox`
# stays: it writes a component to a path the caller names, not the session.
METHODS = {
    "ping": m_ping,
    "exec": m_exec,
    "op_info": m_op_info,
    "network": m_network,
    "op_create": m_op_create,
    "op_delete": m_op_delete,
    "op_connect": m_op_connect,
    "op_disconnect": m_op_disconnect,
    "par_set": m_par_set,
    "render": m_render,
    "errors": m_errors,
    "capture": m_capture,
    "contact_sheet": m_contact_sheet,
    "batch": m_batch,
    "undo": m_undo,
    "redo": m_redo,
    "save_tox": m_save_tox,
    "palette_load": m_palette_load,
    "op_types": m_op_types,
    "health_sample": m_health_sample,
    "extension_add": m_extension_add,
    "annotate": m_annotate,
    "annotations": m_annotations,
    "flags": m_flags,
    "flags_set": m_flags_set,
    "claim_scope": m_claim_scope,
    "release_scope": m_release_scope,
    "scopes": m_scopes,
    "status_note": m_status_note,
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
            if not _token_matches(supplied):
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

        params = payload.get("params") or {}
        try:
            result = method(params)
        except Exception:
            _note_request(dat, name, params, "error")
            raise
        _note_request(dat, name, params, "ok")
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
