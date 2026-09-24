"""An append-only trail of every bridge call, so a session can be read back.

The status panel holds the *last* call and the next one overwrites it. That
answers "is the bridge alive"; it cannot answer "yesterday the agent broke
something, what was it". This file is the answer to the second question.

Why the host and not TouchDesigner
---------------------------------
The two carriers measured when the claims table needed one both lose:

- A Table DAT inside the bridge COMP costs 2.2-3.0 us to read, but it dies
  with the process. Making it outlive a session would mean saving the
  project, and `project.save()` is a Save As that moves the artist's working
  file — forbidden here and everywhere else in this codebase. A journal that
  evaporates when TouchDesigner closes does not answer the question it exists
  for.
- A file written from *inside* TouchDesigner was measured at 124-200 us per
  write — three to five times the whole 42.8 us panel repaint, inside a
  16.7 ms frame. Paying that on every call to duplicate what the host already
  knows is the wrong trade.

So the record is written by the host, at the one chokepoint every bridge call
passes through (`BridgeClient.call`). Frame cost: exactly zero, because no
part of it runs inside TouchDesigner. The append itself measures 41.6 us at
the median on this machine (n=2,000, real filesystem, open-write-fstat-close)
— coincidentally about what the panel repaint costs inside a frame, and the
whole point is that these microseconds are spent in a process nobody is
watching a clock in, next to a round trip that already cost 12 ms.

What the host cannot see is a call that
did not come from this package — someone poking the bridge with curl is not
in the journal, and neither is a purely offline tool (the project reader
never dials the bridge). Both are named gaps, not silent ones.

What is written, and what is deliberately not
---------------------------------------------
Every line keeps the method, the outcome, the duration, and three scalars
pulled out of the parameters by name — the `path` that was aimed at, the
`owner` that claimed it, and for `batch` the number of steps.

A call that *changes* the project also keeps what it changed, under `change`:
the parameter values and expressions a `par_set` wrote, the flags a
`flags_set` set, what an `op_create` made and where, every step of a `batch`,
the code an `exec` ran, and the walk a `timeline_run` was sent. Until 2026-09-24 the rule was the opposite —
names, never payloads — and it cost exactly what it was meant to save: six
thousand lines of an agent's session held not one parameter value, so "put it
back the way it was yesterday" was answered by matching stills from a rendered
video for forty minutes. The owner reversed the rule after that session.

An old value is kept only where the bridge already returns one (`flags_set`
reads each flag before writing it). `par_set` returns the value read back
after the write, never the one before, and asking the bridge for it would be
a second call on every write; so the previous value of a parameter is the
previous line that set it, and nothing here pretends otherwise.

Calls that only read — `network`, `op_info`, `render` and the rest — still
keep their path and nothing else: a whole network in a log is a copy of the
artist's work nobody asked for. Every string in a change is clipped
(`MAX_CODE_CHARS` for code and DAT text, `MAX_VALUE_CHARS` for the rest), a
batch keeps its first `MAX_STEPS` steps, and a line that still comes out over
`MAX_LINE_BYTES` is re-clipped harder and at last reduced to a note of its
size, so one call can never eat the file.

The refusal text *is* kept whole, because that is the one thing the reader
came for, clipped only at `MAX_ERROR_CHARS` with a visible marker so a
runaway message cannot eat the file.

Every line is scrubbed of the bridge token before it is written — see
`_scrub`. The token does not travel in parameters by design, but `exec` code
is whatever an agent typed, and a credential leaked into a log file is not
recoverable by deleting the line. The scrub runs on the finished line, so the
recorded change passes through it like every other field. Someone else's
secret is hidden too, by pattern, in the change and in the refusal text: a
quoted value given to a name like `password` or `api_key`, and keys that
announce themselves (`sk-`, `ghp_`, `xoxb-`, `AKIA`). See `_redact`.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import ensure_home, home, load_config

# The growth limit is in bytes, not records, and deliberately so: records are
# not uniform. A `par_set` without its values was 196 bytes, and an `exec`
# carrying its code can be twenty times that, so a count-based cap would bound
# the number of lines while leaving the file free to reach any size at all.
#
# Sixteen megabytes since 2026-09-24, when lines began to carry what a call
# changed. The old cap was one megabyte, sized for 196-byte lines. The journal
# on the owner's machine that day held 6,436 calls from 31 August on in 960 KB,
# 1,907 of them `exec` and 440 `op_create`. Give each `exec` its code — about
# 1 KB is an estimate, not a measurement, since no journal held code before —
# and the same span is roughly three times the old cap, so the oldest weeks
# would already be gone and a heavy day could push out the day before. At
# 16 MiB the span fits several times over, still bounded, and each line is
# bounded on its own by `MAX_LINE_BYTES`. Measured on this machine at 16 MiB
# of 1 KB lines: trimming 12.8 ms, reading the last 20 calls 67 ms (n=5 each).
#
# When the file passes the cap, the oldest lines are dropped until it is under
# `TRIM_TO_BYTES`. Trimming to 75% rather than to the cap is what keeps the
# rewrite amortised: paid once per 4 MiB appended instead of on every call
# after the first overflow.
MAX_BYTES = 16 * 1024 * 1024
TRIM_TO_BYTES = 12 * 1024 * 1024

# A refusal longer than this is clipped. What is stored is `BridgeError`'s
# `message` — the handler's own text, not its traceback, which the client
# keeps on the exception and this deliberately does not copy. Bridge refusals
# are one to a few lines (the longest in the handler, the scope-claim refusal,
# is under 400 characters), so 8 KB clips nothing the bridge produces today
# and still bounds one record at well under 1% of the file. The clip exists
# for `exec`, where the text is whatever the artist's own code raised.
MAX_ERROR_CHARS = 8000
_CLIP_MARKER = " ...[clipped]"

# The scalars lifted out of a call's parameters, and how far each may run.
# Names, not payloads: see the module docstring.
MAX_PATH_CHARS = 240
MAX_OWNER_CHARS = 80

# How much of a change a line may carry. Code and DAT text get 4 KB, which the
# contract that introduced them named and which keeps a short script whole;
# every other string — a value, an expression, a file path — gets 1,000
# characters, far past any parameter expression but short of a pasted shader.
# A clipped string ends in the visible marker and its full length is recorded
# beside it as `<key>_chars`, so the reader knows how much is missing.
MAX_CODE_CHARS = 4096
MAX_VALUE_CHARS = 1000
# Steps of a batch recorded one by one; the rest are counted, not dropped
# silently. Entries kept per mapping or list inside a change, for the same
# reason: a `pars` of a thousand names is a bug somewhere, not a record.
MAX_STEPS = 64
MAX_ITEMS = 128
# The ceiling on one written line. A 64-step batch of DATs with 4 KB of text
# each would be a quarter of a megabyte; past this bound the change is
# re-clipped harder, and past that it is replaced by a note of its size. The
# refusal text keeps its own cap (`MAX_ERROR_CHARS`) and is not cut here.
MAX_LINE_BYTES = 32 * 1024


def journal_path() -> Path:
    return home() / "calls.jsonl"


# -- redaction --------------------------------------------------------------

# Cached per config file *and* its modification time. The path alone was the
# key until 2026-09-06, which is a cache that never expires for the one thing
# that changes: `td-atlas install` rewrites `config.json` in place with a new
# token, and a long-lived MCP server went on scrubbing the old one — the value
# is only ever used to *remove* text, so a stale hit means the live token is
# written into the log in clear. Adding the mtime costs one `stat` per record;
# a token rotated twice inside one filesystem timestamp tick is the residual
# gap, and it is not one this cache can close.
_secret_cache: tuple[Path, float, str] | None = None


def _config_mtime(path: Path) -> float:
    """The config file's mtime, or 0.0 when it is absent or unreadable.

    Absent is a real state — the bridge writes the token on first registration
    — and it must not raise: every caller of this is inside `record`, which
    promises never to turn a working call into a failed one.
    """
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _config_token() -> str:
    global _secret_cache
    path = home() / "config.json"
    stamp = _config_mtime(path)
    if _secret_cache is not None and _secret_cache[:2] == (path, stamp):
        return _secret_cache[2]
    token = str(load_config().get("token") or "")
    _secret_cache = (path, stamp, token)
    return token


def forget_secrets() -> None:
    """Drop the cached token. For tests that move TD_ATLAS_HOME mid-run."""
    global _secret_cache
    _secret_cache = None


def _scrub(line: str, extra: str = "") -> str:
    """Remove the bearer token from a line about to be written.

    Applied to the finished JSON rather than to each field, so a token that
    reached the record through a path nobody predicted — a refusal quoting a
    request header, a caller who put it in `owner` — is still caught. Short
    strings are ignored: an empty or one-character "token" would replace every
    line with a row of markers.
    """
    for secret in (extra, _config_token()):
        if isinstance(secret, str) and len(secret) >= 8:
            line = line.replace(secret, "<token redacted>")
    return line


# Secrets that are not ours. Since lines began to carry what a call changed,
# the journal holds the code `exec` ran and the values `par_set` wrote, and an
# agent pastes a service key into either as readily as anything else. `_scrub`
# only knows the bridge's own token; these catch the two common shapes of
# someone else's. They are patterns, so they are a net, not a guarantee: a key
# in no known format assigned to a name that says nothing is written as sent.
#
# A name that says "secret", given a quoted string by `=` or `:`, as an
# assignment, a keyword argument or a dict entry. The name must end at the
# keyword (or at a `_key`/`_token`/`_header` after it), so `author`,
# `password_hash` and `token_count` are not names of secrets, and the value
# must be a string literal, so `token = get_token()` and `token == "x"` keep
# their text.
_SECRET_NAME = (
    r"[\w-]*?(?:password|passwd|secret|token|api_?key|access_?key|auth)"
    r"(?:[_-]?(?:key|token|header))?"
)
_SECRET_ASSIGNED = re.compile(
    r"(?i)\b(" + _SECRET_NAME + r")\b(['\"]?\s*[:=]\s*)(['\"])(?!\3)(.+?)(\3)"
)
_SECRET_KEY_NAME = re.compile(r"(?i)^" + _SECRET_NAME + r"$")
# Keys that announce themselves by prefix, wherever they stand: OpenAI-style
# `sk-`, GitHub personal tokens `ghp_`, Slack `xoxb-`/`xoxa-`/`xoxp-`, AWS
# access key ids `AKIA`.
_SECRET_LITERAL = re.compile(
    r"\bsk-[A-Za-z0-9_-]{16,}"
    r"|\bghp_[A-Za-z0-9]{20,}"
    r"|\bxox[bap]-[A-Za-z0-9-]{10,}"
    r"|\bAKIA[0-9A-Z]{16}\b"
)
REDACTED = "[redacted]"


def _redact(text: str) -> str:
    """`text` with the values of named secrets and self-announcing keys hidden."""
    text = _SECRET_LITERAL.sub(REDACTED, text)
    return _SECRET_ASSIGNED.sub(
        lambda m: m.group(1) + m.group(2) + m.group(3) + REDACTED + m.group(5),
        text,
    )


# -- writing ----------------------------------------------------------------

def _clip(text: str, limit: int) -> str:
    text = str(text)
    if len(text) <= limit:
        return text
    return text[: limit - len(_CLIP_MARKER)] + _CLIP_MARKER


# Bridge methods that change the project (or, for `save_tox`, write a file of
# it), and so leave their change behind. Everything else reads, and keeps only
# the scalars. `undo`/`redo` take no parameters, so their name is the record;
# `claim_scope`'s path and owner are already scalars; `status_note` is this
# package reporting to its own panel, not an edit. `timeline_run` pauses the
# timeline, crops the Render TOPs it is given and writes files that outlive
# it, and `timeline_cancel` puts the crop and the play mode back, so both keep
# what they were sent; `timeline_profile` pauses the timeline and moves its
# frame the same way, and forces every operator it measures to cook, so it
# keeps its walk too; `timeline_status` only reads.
CHANGES = frozenset({
    "par_set", "batch", "op_create", "op_delete", "op_connect",
    "op_disconnect", "flags_set", "exec", "palette_load", "extension_add",
    "annotate", "save_tox", "timeline_run", "timeline_cancel",
    "timeline_profile",
})

# Strings that are code or DAT content and get `MAX_CODE_CHARS`.
_LONG_KEYS = frozenset({"code", "text"})
# Parameter keys the scalars already carry, or that the batch walk replaces.
_NOT_A_CHANGE = frozenset({"path", "owner", "ops"})
# What of a reply is worth keeping, per method: only what the bridge already
# sends. `before` is the one old value any method returns; `applied` is what a
# parameter read back as after the write, which for an expression is the only
# place its evaluated value appears.
_FROM_RESULT = {"flags_set": ("before",), "par_set": ("applied",)}


def _bounded(value: Any, limit: int, long_limit: int, key: str = "",
             depth: int = 0) -> Any:
    """`value` with every string clipped and every container shortened.

    Secrets are hidden before the clip, so a clip cannot cut a value in two
    and leave half of it unrecognised; a string under a name that says it is
    a secret (`{"password": "..."}`) is hidden whole.
    """
    if isinstance(value, str):
        if key and _SECRET_KEY_NAME.match(key):
            return REDACTED
        return _clip(_redact(value), long_limit if key in _LONG_KEYS else limit)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if depth >= 6:
        return "..."
    if isinstance(value, dict):
        out = {}
        for index, (name, item) in enumerate(value.items()):
            if index >= MAX_ITEMS:
                out["..."] = "%d more" % (len(value) - MAX_ITEMS)
                break
            out[str(name)] = _bounded(item, limit, long_limit, str(name), depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        kept = [_bounded(item, limit, long_limit, key, depth + 1)
                for item in list(value)[:MAX_ITEMS]]
        if len(value) > MAX_ITEMS:
            kept.append("... %d more" % (len(value) - MAX_ITEMS))
        return kept
    return _clip(str(value), limit)


def _one_change(method: str, params: dict, result: Any, limit: int,
                long_limit: int) -> dict:
    out: dict = {}
    for key, value in params.items():
        if key in _NOT_A_CHANGE or value is None:
            continue
        out[key] = _bounded(value, limit, long_limit, key)
        if key in _LONG_KEYS and isinstance(value, str) and len(value) > long_limit:
            out[key + "_chars"] = len(value)
    if isinstance(result, dict):
        for key in _FROM_RESULT.get(method, ()):
            if key in result:
                out[key] = _bounded(result[key], limit, long_limit, key)
        if method == "op_create" and isinstance(result.get("path"), str):
            out["created"] = _clip(result["path"], MAX_PATH_CHARS)
    return out


def _change(method: str, params: dict | None, result: Any = None,
            limit: int = MAX_VALUE_CHARS, long_limit: int = MAX_CODE_CHARS,
            steps: int = MAX_STEPS) -> dict | None:
    """What a changing call changed, bounded; None for a call that only reads.

    Built from the parameters the caller sent, plus the fields of the reply
    named in `_FROM_RESULT`. On a refusal there is no reply, and the change is
    what was attempted — which is what a reader of a failure wants to see.
    """
    if method not in CHANGES or not isinstance(params, dict):
        return None
    if method != "batch":
        return _one_change(method, params, result, limit, long_limit) or None
    ops = params.get("ops")
    results = result.get("results") if isinstance(result, dict) else None
    out: dict = {}
    if params.get("undo_name"):
        out["undo_name"] = _clip(str(params["undo_name"]), MAX_OWNER_CHARS)
    recorded = []
    for index, step in enumerate(ops if isinstance(ops, (list, tuple)) else []):
        if index >= steps:
            out["more"] = len(ops) - steps
            break
        if not isinstance(step, dict):
            continue
        name = str(step.get("method") or "?")
        step_params = step.get("params") if isinstance(step.get("params"), dict) else {}
        entry: dict = {"method": name}
        if isinstance(step_params.get("path"), str):
            entry["path"] = _clip(step_params["path"], MAX_PATH_CHARS)
        step_result = (
            results[index]
            if isinstance(results, list) and index < len(results) else None
        )
        entry.update(_one_change(name, step_params, step_result, limit, long_limit))
        recorded.append(entry)
    out["steps"] = recorded
    return out


def _scalars(params: dict | None) -> dict:
    """The three parameter fields every line keeps, whatever the method."""
    out: dict = {}
    if not isinstance(params, dict):
        return out
    path = params.get("path")
    if isinstance(path, str) and path:
        out["path"] = _clip(path, MAX_PATH_CHARS)
    owner = params.get("owner")
    if isinstance(owner, str) and owner:
        out["owner"] = _clip(owner, MAX_OWNER_CHARS)
    ops = params.get("ops")
    if isinstance(ops, (list, tuple)):
        out["steps"] = len(ops)
    return out


def record(
    method: str,
    ok: bool,
    seconds: float,
    params: dict | None = None,
    port: int | None = None,
    error_type: str = "",
    error_reason: str = "",
    error_message: str = "",
    token: str = "",
    when: float | None = None,
    result: Any = None,
) -> dict | None:
    """Append one call to the journal. Returns the record, or None if it could
    not be written.

    `result` is the bridge's reply on success; only the fields named in
    `_FROM_RESULT` are taken from it.

    Never raises. A journal that turns a working call into a failed one is
    worse than no journal: every caller of this is in the success path of
    something the user actually asked for.
    """
    entry: dict = {
        "at": round(when if when is not None else time.time(), 3),
        "method": str(method),
        "ok": bool(ok),
        "ms": round(float(seconds) * 1000.0, 3),
    }
    if port:
        entry["port"] = int(port)
    entry.update(_scalars(params))
    if not ok:
        if error_type:
            entry["error"] = str(error_type)
        if error_reason:
            entry["reason"] = str(error_reason)
        if error_message:
            entry["message"] = _clip(_redact(error_message), MAX_ERROR_CHARS)
    global _write_failure
    try:
        _append(_scrub(_bounded_line(entry, method, params, result), token))
    except Exception as exc:
        # Kept rather than swallowed. The failure is invisible from the outside
        # — no caller checks this return value, and they should not: a journal
        # that turns a working call into a failed one is worse than no journal
        # — so the only place it can surface is the reader, which is where
        # someone is already asking what happened. See `not_being_kept`.
        _write_failure = "%s: %s" % (type(exc).__name__, exc)
        return None
    _write_failure = ""
    return entry


# Tighter and tighter clips for a change that will not fit in one line:
# (value chars, code chars, batch steps). The last resort after these is a
# note of the size alone.
_SHRINK = (
    (MAX_VALUE_CHARS, MAX_CODE_CHARS, MAX_STEPS),
    (240, 512, MAX_STEPS),
    (80, 120, 16),
)


def _bounded_line(entry: dict, method: str, params: dict | None,
                  result: Any) -> str:
    """The entry as one JSON line, its change fitted under `MAX_LINE_BYTES`.

    The size is measured on the serialised line, never by cutting it: a line
    clipped mid-string is not JSON, and `read` would skip the whole call.
    """
    line = json.dumps(entry, ensure_ascii=False)
    size = 0
    for limit, long_limit, steps in _SHRINK:
        try:
            change = _change(method, params, result, limit, long_limit, steps)
        except Exception:
            # A parameter shape nobody foresaw costs the change, not the line:
            # the call itself is still recorded.
            return line
        if not change:
            return line
        entry["change"] = change
        line = json.dumps(entry, ensure_ascii=False)
        size = len(line.encode("utf-8"))
        if size <= MAX_LINE_BYTES:
            return line
    entry["change"] = {"omitted": "a change of %d bytes, over the line bound" % size}
    return json.dumps(entry, ensure_ascii=False)


def _append(line: str) -> None:
    path = journal_path()
    ensure_home()
    data = (line + "\n").encode("utf-8")
    # O_APPEND, one write() call. The guarantee this actually rests on is the
    # one POSIX gives for O_APPEND on a regular file: the seek to the end and
    # the write are one operation, so two processes never write over each
    # other's bytes and the file only grows. (An earlier comment here cited
    # PIPE_BUF, which is the pipe rule and says nothing about a file.) What is
    # *not* guaranteed is that one write() lands as one contiguous piece, so a
    # torn line remains possible in principle; `read` skips a line that will
    # not parse, which is the same handling a line from a killed process gets.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, data)
        size = os.fstat(fd).st_size
    finally:
        os.close(fd)
    if size > MAX_BYTES:
        _trim(path)


def _trim(path: Path) -> None:
    """Drop the oldest lines until the file is under `TRIM_TO_BYTES`.

    Written to a sibling and renamed, because `os.rename` is atomic on POSIX:
    a reader never sees a half-written journal. Known race, named here rather
    than fixed, and larger than this comment used to claim: what is lost is
    not one line but every line appended between `read_bytes` and `os.replace`
    — those go to the old inode and disappear with it. The rewrite was
    measured at 12.8 ms for a full 16 MiB file (see MAX_BYTES), so that is the
    width of the window. Two writers crossing the cap together lose more: both
    trim, and the second `replace` discards the first's result as well.

    Not fixed because both repairs cost more than the loss. `fcntl.flock` does
    not exist on Windows, which this project's CI now runs; a lock file is a
    second piece of state to leave behind when a process dies. The loss is
    bounded to the moment the file overflows — once per 4 MiB appended — and
    what is lost is journal lines, not the calls themselves.
    """
    try:
        raw = path.read_bytes()
    except OSError:
        return
    lines = raw.splitlines(keepends=True)
    kept: list[bytes] = []
    total = 0
    for line in reversed(lines):
        total += len(line)
        if total > TRIM_TO_BYTES:
            break
        kept.append(line)
    kept.reverse()
    temp = path.with_name(path.name + ".trim-%d" % os.getpid())
    try:
        temp.write_bytes(b"".join(kept))
        temp.chmod(0o600)
        os.replace(temp, path)
    except OSError:
        try:
            temp.unlink()
        except OSError:
            pass


# -- what is wrong with the journal itself ----------------------------------

# Why the last append failed, or "" when it worked. Process-local by
# construction: the MCP server that failed to write is not the process running
# `td-atlas log`. That is why `not_being_kept` also looks at the directory —
# an unwritable home is the one cause a second process can see for itself.
_write_failure = ""

# Set when the file is there and cannot be read. Distinct from absent, which
# is the ordinary state before the first bridge call.
_read_failure = ""


def not_being_kept() -> str:
    """One line saying the journal is not a record of what happened, or "".

    Exists because "No calls recorded yet" and "nothing is being written" read
    identically, and the first is reassuring. A reader who has just been told
    the trail is empty needs to know whether to believe it.
    """
    if _write_failure:
        return "the journal is not being written: " + _write_failure
    if _read_failure:
        return "the journal cannot be read: " + _read_failure
    path = journal_path()
    # Only when the directory is there and refuses writes. `os.access` is also
    # False for a directory that does not exist, and an absent `~/.td-atlas` is
    # the ordinary state before the first `install`, `build` or bridge call —
    # complaining about it would replace one confident wrong answer with
    # another. A home whose own parent is unwritable is therefore invisible
    # here; the process that fails to create it sees that as `_write_failure`.
    if not path.exists() and path.parent.exists() and not os.access(
        path.parent, os.W_OK
    ):
        return (
            "the journal is not being written: %s is not writable"
            % path.parent
        )
    return ""


def _note(text: str) -> str:
    """Append the "not being kept" line to a rendered report, when there is one."""
    warning = not_being_kept()
    return f"{text}\n\n({warning})" if warning else text


# -- reading ----------------------------------------------------------------

@dataclass
class Call:
    """One journal line, as read back. Unknown fields are dropped, not kept:
    a record written by a newer version is still readable as what it shares."""

    at: float
    method: str
    ok: bool
    ms: float
    port: int = 0
    path: str = ""
    owner: str = ""
    steps: int | None = None
    error: str = ""
    reason: str = ""
    message: str = ""
    # What a changing call changed; None on a read and on every line written
    # before 2026-09-24, which carry no change at all.
    change: dict | None = None

    @classmethod
    def from_dict(cls, data: dict) -> "Call | None":
        if not isinstance(data, dict) or "method" not in data:
            return None
        try:
            return cls(
                at=float(data.get("at") or 0.0),
                method=str(data.get("method") or ""),
                ok=bool(data.get("ok")),
                ms=float(data.get("ms") or 0.0),
                port=int(data.get("port") or 0),
                path=str(data.get("path") or ""),
                owner=str(data.get("owner") or ""),
                steps=(
                    int(data["steps"]) if isinstance(data.get("steps"), int) else None
                ),
                error=str(data.get("error") or ""),
                reason=str(data.get("reason") or ""),
                message=str(data.get("message") or ""),
                change=(
                    data["change"] if isinstance(data.get("change"), dict) else None
                ),
            )
        except (TypeError, ValueError):
            return None

    @property
    def when(self) -> str:
        """Absolute local time, never "3 s ago": a stale absolute time reads
        as stale, a stale relative one lies."""
        if self.at <= 0:
            return "??:??:??"
        return time.strftime("%m-%d %H:%M:%S", time.localtime(self.at))


def read(limit: int | None = None, failures_only: bool = False,
         method: str = "", since: float = 0.0) -> list[Call]:
    """The journal, oldest first, filtered. A corrupt line is skipped, not
    fatal — a half-written line from a killed process must not hide the rest.
    """
    global _read_failure
    path = journal_path()
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        _read_failure = ""
        return []
    except OSError as exc:
        # Present and unreadable is not the same as absent, and returning an
        # empty list for both is what made "No calls recorded yet" a lie.
        _read_failure = "%s: %s" % (type(exc).__name__, exc)
        return []
    _read_failure = ""
    calls: list[Call] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except ValueError:
            continue
        call = Call.from_dict(data)
        if call is None:
            continue
        if failures_only and call.ok:
            continue
        if method and call.method != method:
            continue
        if since and call.at < since:
            continue
        calls.append(call)
    if limit is not None and limit >= 0:
        calls = calls[-limit:] if limit else []
    return calls


# -- the summary ------------------------------------------------------------

@dataclass
class Summary:
    """Where the tool lets an agent down, in the shape a glance wants it.

    Not a list of calls — the list is `read()`. This answers one question:
    which methods refuse, and which ones are slow enough to be the thing that
    timed out. Both are ordered worst-first, because the reader stops after
    the first line more often than not.
    """

    total: int = 0
    failures: int = 0
    span: tuple[float, float] = (0.0, 0.0)
    # name, calls, failures, worst milliseconds
    by_method: list[tuple[str, int, int, float]] = field(default_factory=list)
    reasons: list[tuple[str, int]] = field(default_factory=list)
    slowest: list[Call] = field(default_factory=list)

    @property
    def failure_rate(self) -> float:
        return (self.failures / self.total * 100.0) if self.total else 0.0


def summarise(calls: list[Call]) -> Summary:
    counts: dict[str, list] = {}
    reasons: dict[str, int] = {}
    failures = 0
    for call in calls:
        row = counts.setdefault(call.method, [0, 0, 0.0])
        row[0] += 1
        if not call.ok:
            row[1] += 1
            failures += 1
            key = call.error or "Error"
            reasons[key] = reasons.get(key, 0) + 1
        row[2] = max(row[2], call.ms)
    by_method = sorted(
        ((name, r[0], r[1], r[2]) for name, r in counts.items()),
        # Failures first, then volume: a method that refused once out of once
        # is more interesting than one that succeeded four hundred times.
        key=lambda item: (-item[2], -item[1], item[0]),
    )
    return Summary(
        total=len(calls),
        failures=failures,
        span=(calls[0].at, calls[-1].at) if calls else (0.0, 0.0),
        by_method=by_method,
        reasons=sorted(reasons.items(), key=lambda item: (-item[1], item[0])),
        slowest=sorted(calls, key=lambda c: -c.ms)[:5],
    )


# -- rendering (shared by the CLI and the MCP tool) --------------------------

# Lines of recorded code shown under an `exec` in the listing. The listing is
# what `td_log` hands an agent, twenty calls at a time by default, and twenty
# 4 KB scripts in full would be most of a context window; the head of a script
# says which one it was, and the whole of what was kept is in the file.
CODE_LINES_SHOWN = 6
# Keys `describe_change` renders in its own way; any other key is shown as
# `key: value` so a change recorded by a newer version still reads.
_RENDERED = frozenset({
    "pars", "applied", "flags", "before", "code", "code_chars", "steps",
    "more", "method", "path",
})


def _compact(value: Any, limit: int = 120) -> str:
    text = json.dumps(value, ensure_ascii=False)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _par_value(value: Any) -> str:
    if isinstance(value, dict):
        if "expr" in value:
            return "expr %s" % value["expr"]
        if "bind" in value:
            return "bind %s" % value["bind"]
        if value.get("pulse"):
            return "pulse"
    return _compact(value)


def describe_change(change: dict) -> list[str]:
    """A recorded change as the lines a person reads: `tx = 0.5`, not JSON.

    A parameter's read-back is shown only when it differs from what was
    sent — for an expression that is its value at the time, for a menu the
    name TouchDesigner stored. An old value appears only where one was
    recorded (`flags_set`); nothing is inferred.
    """
    lines: list[str] = []
    pars = change.get("pars")
    applied = change.get("applied") if isinstance(change.get("applied"), dict) else {}
    if isinstance(pars, dict):
        for name, value in pars.items():
            text = "%s = %s" % (name, _par_value(value))
            if name in applied and applied[name] != value:
                text += "  (reads %s)" % _compact(applied[name], 60)
            lines.append(text)
    flags = change.get("flags")
    before = change.get("before") if isinstance(change.get("before"), dict) else {}
    if isinstance(flags, dict):
        for name, value in flags.items():
            text = "%s = %s" % (name, value)
            if name in before:
                text += " (was %s)" % before[name]
            lines.append(text)
    code = change.get("code")
    if isinstance(code, str):
        code_lines = code.splitlines() or [""]
        lines.extend("| " + piece for piece in code_lines[:CODE_LINES_SHOWN])
        hidden = len(code_lines) - CODE_LINES_SHOWN
        if "code_chars" in change:
            lines.append(
                "| ... %s chars in all; the journal kept the first %d"
                % (change["code_chars"], MAX_CODE_CHARS)
            )
        elif hidden > 0:
            lines.append("| ... %d more lines in %s" % (hidden, journal_path()))
    steps = change.get("steps")
    if isinstance(steps, list):
        for index, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            where = step.get("path") or step.get("created") or step.get("parent") or ""
            lines.append(("step %d: %s %s" % (index, step.get("method", "?"), where))
                         .rstrip())
            lines.extend("  " + piece for piece in describe_change(step))
        if change.get("more"):
            lines.append("... %s more steps not recorded" % change["more"])
    for key, value in change.items():
        if key in _RENDERED:
            continue
        lines.append("%s: %s" % (key, _compact(value)))
    return lines


def format_calls(calls: list[Call], width: int = 100) -> str:
    """One line per call, newest last, so a terminal's tail is the present."""
    if not calls:
        return _note(
            "No calls recorded yet. The journal fills as the bridge is used."
        )
    lines = []
    for call in calls:
        mark = "ok  " if call.ok else "FAIL"
        target = call.path or ""
        if call.steps is not None:
            target = (target + " ") if target else ""
            target += "[%d steps]" % call.steps
        head = "%s  %s  %-16s %7.1fms  %s" % (
            call.when, mark, call.method, call.ms, target
        )
        lines.append(head.rstrip())
        if call.change:
            lines.extend("        " + piece for piece in describe_change(call.change))
        if not call.ok and (call.error or call.message):
            detail = call.error + (": " + call.message if call.message else "")
            for piece in detail.splitlines():
                lines.append("        " + piece)
        elif not call.ok:
            lines.append("        (no message recorded)")
    return _note("\n".join(lines))


def format_summary(summary: Summary) -> str:
    """Five seconds of reading, in the order the question is actually asked:
    how much, how much of it broke, what broke, and what was slow."""
    if not summary.total:
        return _note(
            "No calls recorded yet. The journal fills as the bridge is used."
        )
    first = time.strftime("%m-%d %H:%M", time.localtime(summary.span[0]))
    last = time.strftime("%m-%d %H:%M", time.localtime(summary.span[1]))
    lines = [
        "%d calls, %d failed (%.0f%%)   %s to %s"
        % (summary.total, summary.failures, summary.failure_rate, first, last),
    ]
    if summary.failures:
        lines.append("")
        lines.append("where it fails")
        for name, total, fails, _worst in summary.by_method:
            if not fails:
                continue
            lines.append("  %-18s %d of %d" % (name, fails, total))
        if summary.reasons:
            lines.append(
                "  reasons: "
                + ", ".join("%s x%d" % (name, n) for name, n in summary.reasons)
            )
    else:
        lines.append("")
        lines.append("no refusals recorded")
    lines.append("")
    lines.append("slowest")
    for call in summary.slowest:
        lines.append(
            "  %-18s %8.1f ms   %s" % (call.method, call.ms, call.when)
        )
    return _note("\n".join(lines))
