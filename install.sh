#!/bin/sh
#
# Install td-atlas from a terminal, in one line:
#
#   curl -fsSL https://grigabyte.github.io/td-atlas/i | sh
#
# That address is this file: GitHub Pages republishes it from `main` under a
# shorter name on every push that changes it, so there is no second copy to
# fall behind.
#
# What it does, in order: finds a Python 3.11 or newer, clones (or updates)
# the repository, makes a virtualenv beside it, installs the package into
# that virtualenv, builds the offline atom index, and runs `td-atlas install`
# so the bridge is staged and the two lines to paste are printed at the end.
#
# Where td-atlas itself writes:
#
#   <the directory you choose>   the checkout and its .venv
#   ~/.td-atlas                  the index, the staged bridge, the token
#
# Besides those, whichever of `uv` and `pip` does the install fills its own
# package cache — `~/.cache/uv` or `~/Library/Caches/pip` — as it would for
# any package. That is theirs, not this script's, and it is named here rather
# than left out of a promise about where things land.
#
# No system directory is touched, nothing is installed with sudo, and no
# shell startup file is edited — so `td-atlas` is called by its path out of
# the virtualenv, which is what `td-atlas install` prints and what the README
# assumes. Every command that runs is printed before it runs.
#
# Two questions are asked when a terminal is attached: where to clone, and
# whether to build the index now (13-16 seconds, no TouchDesigner process).
# Piped from `curl` with no terminal, both take their default and nothing
# waits for an answer.
#
# Running it twice is not a fresh install: an existing checkout is
# fast-forwarded rather than re-cloned, an existing virtualenv is reused, and
# an index that is already there is left alone by default — rebuilding it
# drops the runtime pass `td-atlas probe` adds, which only a live
# TouchDesigner can put back.
#
# POSIX sh on purpose: this is read by whatever `sh` the pipe lands in.

set -eu

REPO_URL="https://github.com/grigabyte/td-atlas.git"
DEFAULT_DIR="$HOME/td-atlas"
TD_HOME="${TD_ATLAS_HOME:-$HOME/.td-atlas}"

# Piped into `sh`, stdin is the script itself, so a prompt has to be read from
# the terminal directly or it would eat the rest of this file. No terminal
# means no questions.
INTERACTIVE=no
if [ -t 1 ] && [ -r /dev/tty ] && [ -w /dev/tty ]; then
    INTERACTIVE=yes
fi

say() { printf '%s\n' "$*"; }
step() { printf '\n== %s\n' "$*"; }
fail() { printf '\nerror: %s\n' "$*" >&2; exit 1; }

# Print the command, then run it. A reader of the scrollback can see exactly
# what was done to their machine.
run() {
    printf '   $ %s\n' "$*"
    "$@"
}

# Answer on stdout, prompt on the terminal so it survives `$(...)`.
ask() {
    if [ "$INTERACTIVE" = no ]; then
        # Nobody to ask, so say out loud what was decided instead of deciding
        # it in silence. stderr, because stdout is the answer.
        printf '%s -> %s (no terminal attached, taking the default)\n' "$1" "$2" >&2
        printf '%s\n' "$2"
        return 0
    fi
    printf '%s [%s]: ' "$1" "$2" >/dev/tty
    reply=""
    if IFS= read -r reply </dev/tty; then :; fi
    [ -n "$reply" ] || reply="$2"
    printf '%s\n' "$reply"
}

confirm() {
    answer=$(ask "$1" "$2")
    case "$answer" in
        [Yy] | [Yy][Ee][Ss]) return 0 ;;
        *) return 1 ;;
    esac
}

# The two roots `td_atlas.install._candidates_macos` looks in, and no others:
# a guess here that disagreed with the package would skip a build that would
# have worked, or promise one that cannot.
TD_APP=""
have_touchdesigner() {
    for root in "/Applications" "$HOME/Applications"; do
        for app in "$root"/TouchDesigner*.app; do
            if [ -d "$app" ]; then
                TD_APP="$app"
                return 0
            fi
        done
    done
    return 1
}

# The version is asked of the interpreter, never parsed out of `--version`:
# the tuple is the thing the package's `requires-python` compares against.
find_python() {
    for candidate in python3.14 python3.13 python3.12 python3.11 python3 python; do
        found=$(command -v "$candidate" 2>/dev/null) || continue
        if "$found" -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3, 11) else 1)' 2>/dev/null; then
            printf '%s\n' "$found"
            return 0
        fi
    done
    return 1
}

say "td-atlas installer"
say
say "td-atlas itself writes in two places:"
say "  the checkout and its .venv, in the directory you choose below"
say "  $TD_HOME — the index, the staged bridge, the token"
say
say "Besides those, uv or pip fills its own package cache, as it would for any"
say "package. No sudo, no system directory, no change to your shell rc."

# -- what has to be there already -------------------------------------------

step "Checking what is already on this machine"

command -v git >/dev/null 2>&1 || fail "git is not installed. On macOS \`xcode-select --install\` puts it there; otherwise see https://git-scm.com/downloads"

if PYTHON=$(find_python); then
    say "   python  $PYTHON ($("$PYTHON" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))'))"
else
    printf '\n' >&2
    say "error: no Python 3.11 or newer was found on this machine." >&2
    say "" >&2
    say "td-atlas is a Python package, so one has to be installed first." >&2
    say "On macOS, either of these gets you one:" >&2
    say "    brew install python@3.12" >&2
    say "    or the installer at https://www.python.org/downloads/" >&2
    say "" >&2
    say "Then run this script again. Nothing has been changed." >&2
    exit 1
fi

# uv is faster and is used when it is already here. It is deliberately not
# installed by this script: its own installer edits shell startup files, and
# this one promises not to. `python3 -m venv` needs nothing and does the same
# job.
if command -v uv >/dev/null 2>&1; then
    USE_UV=yes
    say "   uv      $(command -v uv) — used for the virtualenv and the install"
else
    USE_UV=no
    say "   uv      not found — using \`$PYTHON -m venv\`, which needs nothing extra"
fi

if have_touchdesigner; then
    say "   TouchDesigner in $TD_APP"
    HAVE_TD=yes
else
    say "   TouchDesigner not found in /Applications or ~/Applications"
    say "           the index is read out of your own copy of the application, so"
    say "           the build step below is skipped until it is installed"
    HAVE_TD=no
fi

# -- where it goes ----------------------------------------------------------

DIR=$(ask "
Where should the checkout go?" "$DEFAULT_DIR")
case "$DIR" in
    "~/"*) DIR="$HOME/${DIR#\~/}" ;;
    "~") DIR="$HOME" ;;
esac

step "Repository"

if [ -e "$DIR/.git" ]; then
    origin=$(git -C "$DIR" remote get-url origin 2>/dev/null || printf '')
    case "$origin" in
        *td-atlas*) ;;
        *) fail "$DIR is a git checkout of something else ($origin). Choose another directory." ;;
    esac
    say "   an existing checkout is there — updating it rather than re-cloning"
    run git -C "$DIR" fetch --quiet origin
    branch=$(git -C "$DIR" symbolic-ref --short -q HEAD || printf '')
    if [ "$branch" = "main" ]; then
        if run git -C "$DIR" merge --ff-only origin/main; then
            :
        else
            say "   could not fast-forward: local commits, or local changes in the"
            say "   way. The checkout is left exactly as it is and the rest of this"
            say "   script runs against it."
        fi
    else
        say "   on ${branch:-a detached HEAD} rather than main, so nothing is merged"
        say "   — the rest of this script runs against the checkout as it stands."
    fi
elif [ -d "$DIR" ] && [ -n "$(ls -A "$DIR" 2>/dev/null || true)" ]; then
    fail "$DIR already exists and is not empty, and is not a git checkout. Choose another directory."
else
    run git clone "$REPO_URL" "$DIR"
fi

# -- the virtualenv and the package -----------------------------------------

VENV="$DIR/.venv"
TD="$VENV/bin/td-atlas"

step "Virtualenv and package"

if [ -x "$VENV/bin/python" ]; then
    say "   reusing the virtualenv already at $VENV"
elif [ "$USE_UV" = yes ]; then
    run uv venv --python "$PYTHON" "$VENV"
else
    run "$PYTHON" -m venv "$VENV"
fi

if [ "$USE_UV" = yes ]; then
    run uv pip install --python "$VENV/bin/python" -e "$DIR"
else
    run "$VENV/bin/python" -m pip install --quiet --upgrade pip
    run "$VENV/bin/python" -m pip install -e "$DIR"
fi

[ -x "$TD" ] || fail "the package installed but $TD is not there — nothing further can run"

# -- the index --------------------------------------------------------------

step "Atom index"

BUILD=no
if [ "$HAVE_TD" = no ]; then
    say "   skipped: TouchDesigner is not installed, and the index is built from it"
elif [ -f "$TD_HOME/atlas.db" ]; then
    say "   an index is already at $TD_HOME/atlas.db."
    say "   Rebuilding drops what \`td-atlas probe\` added from a live instance,"
    say "   and that can only be put back by running probe again."
    if confirm "   Rebuild it anyway?" "n"; then BUILD=yes; fi
else
    say "   the offline pass reads your TouchDesigner installation."
    say "   13-16 seconds, no TouchDesigner process, no network."
    if confirm "   Build the index now?" "y"; then BUILD=yes; fi
fi

if [ "$BUILD" = yes ]; then
    if run "$TD" build; then
        :
    else
        say
        say "   the build did not finish. Everything else below still applies;"
        say "   run \`$TD build\` again once the reason above is dealt with."
    fi
else
    say "   not building now — \`$TD build\` does it later."
fi

# -- the bridge and the two lines -------------------------------------------

step "Bridge"

# Held in a file rather than piped through `tee`, for two reasons: a pipeline's
# exit status here would be tee's and a failed `install` would pass unnoticed,
# and the two lines to paste are read back out of it at the end. It goes inside
# the virtualenv, which exists by now and is swept up with everything else this
# script made.
INSTALL_OUT="$VENV/td-atlas-install-output.txt"
printf '   $ %s install\n' "$TD"
if "$TD" install >"$INSTALL_OUT" 2>&1; then
    cat "$INSTALL_OUT"
else
    cat "$INSTALL_OUT"
    rm -f "$INSTALL_OUT"
    fail "\`$TD install\` did not finish — the reason is above."
fi

BOOTSTRAP_LINE=$(awk '/^ *exec\(open\(/ { sub(/^ */, ""); print; exit }' "$INSTALL_OUT")
MCP_LINE=$(awk '/^ *claude mcp add/ { sub(/^ */, ""); print; exit }' "$INSTALL_OUT")
rm -f "$INSTALL_OUT"

printf '\n'
say "======================================================================"
say "Installed. Two lines to paste."
printf '\n'
say "1. Into TouchDesigner's textport (Dialogs -> Textport and DATs), once"
say "   per project. It builds the /tdatlas bridge inside the open project:"
printf '\n'
if [ -n "$BOOTSTRAP_LINE" ]; then
    say "   $BOOTSTRAP_LINE"
else
    say "   (re-run \`$TD install\` to print it)"
fi
printf '\n'
say "2. Into your MCP client, so an agent can reach it. For Claude Code, run"
say "   this in the directory you work in:"
printf '\n'
if [ -n "$MCP_LINE" ]; then
    say "   $MCP_LINE"
else
    say "   (re-run \`$TD install\` to print it)"
fi
printf '\n'
say "Then, with TouchDesigner open and line 1 pasted in:"
printf '\n'
say "   $TD probe      # completes the index with what only a live instance knows"
say "   $TD doctor     # the one command that says whether the install took"
printf '\n'
say "The README is at $DIR/README.md."
say "======================================================================"
