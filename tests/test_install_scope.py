"""`td-atlas install` offers the MCP server for every directory, not only one.

Second agent report, "Спотыкания помельче": the server was registered with
the default `claude mcp add`, whose scope is `local` — this directory only
(`claude mcp add --help` on this machine, 2026-09-24: `-s, --scope <scope>
Configuration scope (local, user, or project) (default: "local")`). The owner
opened Claude Code in an empty directory to record a session and had no
tools. Both lines are now printed and labelled; nothing is registered
without the person running one of them.
"""

from __future__ import annotations

from pathlib import Path

from td_atlas import cli
from td_atlas.cli import build_parser, mcp_connection_line

ROOT = Path(__file__).resolve().parent.parent


def test_the_user_scope_line_is_the_same_command_with_the_scope():
    local = mcp_connection_line()
    user = mcp_connection_line(scope="user")
    assert user.startswith("claude mcp add -s user td-atlas -- ")
    assert user.split(" -- ", 1)[1] == local.split(" -- ", 1)[1]


def test_install_prints_both_and_the_directory_one_first(monkeypatch, capsys):
    # No clipboard: the test must not overwrite the developer's.
    monkeypatch.setattr(cli.shutil, "which", lambda _name: None)
    args = build_parser().parse_args(["install"])
    assert args.func(args) == 0
    out = capsys.readouterr().out

    local = mcp_connection_line()
    user = mcp_connection_line(scope="user")
    assert local in out and user in out
    # install.sh takes the first `claude mcp add` line as the default.
    assert out.index(local) < out.index(user)
    assert "every directory" in out


def test_install_sh_passes_the_user_scope_line_on():
    script = (ROOT / "install.sh").read_text()
    assert "claude mcp add -s user" in script
