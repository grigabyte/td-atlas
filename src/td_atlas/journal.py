"""An append-only trail of every bridge call, so a session can be read back.

The status panel holds the *last* call and the next one overwrites it. That
answers "is the bridge alive"; it cannot answer "yesterday the agent broke
something, what was it". This file is the answer to the second question.

Why the host and not TouchDesigner
---------------------------------
The two carriers already measured in this project (decision 22) both lose:

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
Bridge parameters carry whole DAT texts and whole networks. None of that is
recorded: the journal keeps the method, the outcome, the duration, and three
scalars pulled out of the parameters by name — the `path` that was aimed at,
the `owner` that claimed it, and for `batch` the number of steps. A payload
in a log is a payload nobody reads and a copy of the artist's work in a place
they did not put it.

The refusal text *is* kept whole, because that is the one thing the reader
came for, clipped only at `MAX_ERROR_CHARS` with a visible marker so a
runaway message cannot eat the file.

Every line is scrubbed of the bridge token before it is written — see
`_scrub`. The token does not travel in parameters today, so nothing currently
puts it there; the scrub exists because "currently" is not a guarantee and a
credential leaked into a log file is not recoverable by deleting the line.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import ensure_home, home, load_config

# The growth limit is in bytes, not records, and deliberately so: records are
# not uniform. A `par_set` is 196 bytes and a refusal quoting a long path and a
# long message is several times that, so a count-based cap would bound the
# number of lines while leaving the file free to reach any size at all.
#
# One megabyte, measured on this machine: a record is 196 bytes at the median
# (n=4,000, a mix of successes and refusals), so the cap holds roughly 5,300
# calls — far more than a working session (6,000 recorded calls came to 497 KB
# and never reached it), and small enough to read, grep and back up without
# thinking about it.
#
# When the file passes the cap, the oldest lines are dropped until it is under
# `TRIM_TO_BYTES`. Trimming to 75% rather than to the cap is what keeps the
# rewrite amortised: measured at 1.6 ms for a full file, paid once per 256 KiB
# appended — about one call in 1,300 — instead of on every call after the
# first overflow.
MAX_BYTES = 1024 * 1024
TRIM_TO_BYTES = 768 * 1024

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


def journal_path() -> Path:
    return home() / "calls.jsonl"


# -- redaction --------------------------------------------------------------

# Cached per config file, because `home()` moves under tests and a cache keyed
# on nothing would hand one test's token to another. The value is only ever
# used to *remove* text, so a stale miss would leak and a stale hit is
# harmless — which is why the key is the path the token was read from.
_secret_cache: tuple[Path, str] | None = None


def _config_token() -> str:
    global _secret_cache
    path = home() / "config.json"
    if _secret_cache is not None and _secret_cache[0] == path:
        return _secret_cache[1]
    token = str(load_config().get("token") or "")
    _secret_cache = (path, token)
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


# -- writing ----------------------------------------------------------------

def _clip(text: str, limit: int) -> str:
    text = str(text)
    if len(text) <= limit:
        return text
    return text[: limit - len(_CLIP_MARKER)] + _CLIP_MARKER


def _scalars(params: dict | None) -> dict:
    """The three parameter fields worth keeping, by name. Never a payload."""
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
) -> dict | None:
    """Append one call to the journal. Returns the record, or None if it could
    not be written.

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
            entry["message"] = _clip(error_message, MAX_ERROR_CHARS)
    try:
        _append(_scrub(json.dumps(entry, ensure_ascii=False), token))
    except Exception:
        return None
    return entry


def _append(line: str) -> None:
    path = journal_path()
    ensure_home()
    data = (line + "\n").encode("utf-8")
    # O_APPEND, one write() call: concurrent MCP servers and CLI invocations
    # share this file, and a single append under the pipe buffer is atomic on
    # POSIX, so two processes interleave whole lines rather than halves.
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
    a reader never sees a half-written journal. Known race, accepted rather
    than locked away: an append that lands between the read and the rename is
    lost. It costs one line out of thousands, only at the moment the file
    overflows, and a lock file would be a second piece of state to leave
    behind when a process dies.
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
            )
        except (TypeError, ValueError):
            return None

    @property
    def when(self) -> str:
        """Absolute local time, for the reason decision 30 gives: a stale
        absolute time reads as stale, a stale relative one lies."""
        if self.at <= 0:
            return "??:??:??"
        return time.strftime("%m-%d %H:%M:%S", time.localtime(self.at))


def read(limit: int | None = None, failures_only: bool = False,
         method: str = "", since: float = 0.0) -> list[Call]:
    """The journal, oldest first, filtered. A corrupt line is skipped, not
    fatal — a half-written line from a killed process must not hide the rest.
    """
    path = journal_path()
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
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

def format_calls(calls: list[Call], width: int = 100) -> str:
    """One line per call, newest last, so a terminal's tail is the present."""
    if not calls:
        return "No calls recorded yet. The journal fills as the bridge is used."
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
        if not call.ok and (call.error or call.message):
            detail = call.error + (": " + call.message if call.message else "")
            for piece in detail.splitlines():
                lines.append("        " + piece)
        elif not call.ok:
            lines.append("        (no message recorded)")
    return "\n".join(lines)


def format_summary(summary: Summary) -> str:
    """Five seconds of reading, in the order the question is actually asked:
    how much, how much of it broke, what broke, and what was slow."""
    if not summary.total:
        return "No calls recorded yet. The journal fills as the bridge is used."
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
    return "\n".join(lines)
