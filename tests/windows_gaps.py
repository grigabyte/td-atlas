"""What Windows does not have, for the tests whose premise needs it.

Two marks, one per missing thing, both measured rather than assumed: the
Windows leg of CI ran for the first time on 2026-09-07 (run 34111353871,
`windows-latest`, Python 3.11-3.14) and these are the facts it reported.

A skip here is a statement that the *check* cannot be built on Windows, never
that the behaviour is unimportant or that the code is excused. Where the code
itself was wrong on Windows, the fix went into the code and the test still
runs there — see `config.py`'s `pid_alive` and `PROBE_BUDGET`,
`component/handler.py`'s `_home` and `_project_path`. The budget was that
run's one open finding, and it stayed open across three more: `port_listening`
could not say "nothing is there" on Windows inside 0.25 s, its test failed
there rather than skipping, and it is closed now that run 34116622022 measured
how long a refusal actually takes (two seconds) and the budget was raised to
that reading. Nothing about Windows is skipped
because a number was inconvenient. What is skipped below is POSIX permission
semantics, which `os.chmod` cannot express on Windows at all: the platform
keeps other accounts out with ACLs, which this project neither sets nor has
measured.

Also worth being plain about: on Windows these two gaps are not only
untestable, they are real. `~/.td-atlas` is not narrowed to its owner there,
and the journal cannot notice a home that refuses writes. `docs/compatibility.md`
says so; `config.py`'s `ensure_home` and `journal.py`'s `not_being_kept`
say so where the code is.
"""

from __future__ import annotations

import os

import pytest

_ON_WINDOWS = os.name == "nt"

posix_mode_bits_only = pytest.mark.skipif(
    _ON_WINDOWS,
    reason=(
        "no POSIX mode bits: os.chmod on Windows reaches only the read-only "
        "attribute, so a directory cannot be narrowed to 0700 and st_mode "
        "reports 0o777 whatever was asked for (CI run 34111353871 measured "
        "511 where 448 was asked for, on all four Python versions)"
    ),
)

posix_access_refusal_only = pytest.mark.skipif(
    _ON_WINDOWS,
    reason=(
        "no access refusal through chmod: on Windows a directory chmod-ed to "
        "0500 still accepts writes and a file chmod-ed to 0000 is still "
        "readable, so the refusal this test needs to provoke cannot be "
        "created (CI run 34111353871)"
    ),
)
