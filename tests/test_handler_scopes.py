"""Cooperative scope claims: path containment, expiry, refusal, release.

Two agents on one TouchDesigner overwrite each other in silence; a claim is
what turns that into a refusal naming the other owner.

The claims live in a Table DAT inside the bridge COMP because TouchDesigner
re-executes this module's body between requests, which wiped the module-level
dict they started in — see `test_claims_survive_the_module_being_re_executed`,
which is that defect written down. None of this needs TouchDesigner running:
the comparison, expiry and row parsing are plain module-level functions, and
the table is reached through one accessor a fake stands in for here.
"""

from __future__ import annotations

import importlib
import os

import pytest

from td_atlas.component import handler


class FakeTableDAT:
    """As much of a Table DAT as the handler touches.

    Cells come back as `str` because that is what a real Cell yields through
    `str()` — measured against a live 2025.32460, where `str(row[0])` gave
    '/project1/audio' for a cell holding that path.
    """

    path = "/tdatlas/tdatlas_scopes"

    def __init__(self, rows=None):
        self._rows = [list(row) for row in (rows or [])]

    def clear(self):
        self._rows = []

    def appendRow(self, cells):
        self._rows.append([str(cell) for cell in cells])

    def rows(self):
        return [list(row) for row in self._rows]

    @property
    def numRows(self):
        return len(self._rows)


@pytest.fixture(autouse=True)
def claim_table(monkeypatch):
    """An empty claim table, standing where the bridge COMP's DAT would be."""
    table = FakeTableDAT()
    monkeypatch.setattr(handler, "_scope_table", lambda create=False: table)
    return table


# -- path containment -------------------------------------------------------

def test_a_claim_covers_its_own_subtree():
    assert handler._scope_contains("/project1/audio", "/project1/audio")
    assert handler._scope_contains("/project1/audio", "/project1/audio/eq1")
    assert handler._scope_contains("/project1/audio", "/project1/audio/eq1/level")


def test_a_sibling_whose_name_starts_the_same_is_outside():
    """The trap: startswith('/project1/audio') says '/project1/audio2' is inside.

    Naive prefix matching hands one agent a veto over its neighbour's network,
    which is the one mistake this whole comparison exists to avoid.
    """
    assert not handler._scope_contains("/project1/audio", "/project1/audio2")
    assert not handler._scope_contains("/project1/audio", "/project1/audio2/eq1")
    assert not handler._scopes_overlap("/project1/audio", "/project1/audio2")


def test_a_parent_is_not_inside_its_child_but_they_overlap():
    assert not handler._scope_contains("/project1/audio/eq1", "/project1/audio")
    assert handler._scopes_overlap("/project1/audio/eq1", "/project1/audio")


def test_root_contains_everything():
    assert handler._scope_contains("/", "/project1")
    assert handler._scope_contains("/", "/")


def test_unrelated_branches_do_not_overlap():
    assert not handler._scopes_overlap("/project1/audio", "/project1/video")


def test_paths_are_normalised_to_one_spelling():
    assert handler._normalise_scope_path("/project1/audio/") == "/project1/audio"
    assert handler._normalise_scope_path("/project1//audio") == "/project1/audio"
    assert handler._normalise_scope_path("  /project1/audio  ") == "/project1/audio"
    assert handler._normalise_scope_path("/") == "/"


def test_a_relative_or_empty_path_is_refused():
    with pytest.raises(ValueError, match="absolute"):
        handler._normalise_scope_path("project1/audio")
    with pytest.raises(ValueError, match="required"):
        handler._normalise_scope_path("")


def test_an_empty_owner_is_refused():
    """An empty owner would compare equal to an unattributed write's owner."""
    with pytest.raises(ValueError, match="owner name is required"):
        handler._normalise_owner("   ")


# -- time to live -----------------------------------------------------------

def test_ttl_defaults_and_is_bounded():
    assert handler._scope_ttl(None) == handler.DEFAULT_SCOPE_TTL
    assert handler._scope_ttl("30") == 30.0
    with pytest.raises(ValueError, match="positive"):
        handler._scope_ttl(0)
    with pytest.raises(ValueError, match="positive"):
        handler._scope_ttl(float("nan"))
    with pytest.raises(ValueError, match="ceiling"):
        handler._scope_ttl(handler.MAX_SCOPE_TTL + 1)


def test_an_expired_claim_stops_counting():
    claims = {
        "/project1/audio": {
            "path": "/project1/audio",
            "owner": "agent-a",
            "claimed": 1000.0,
            "expires": 1060.0,
        }
    }
    assert handler._active_scopes(claims, 1059.0)
    assert handler._active_scopes(claims, 1061.0) == []
    # And it no longer blocks anybody once it has lapsed.
    assert handler._blocking_claim(claims, "/project1/audio/eq1", "agent-b", 1059.0)
    assert handler._blocking_claim(claims, "/project1/audio/eq1", "agent-b", 1061.0) is None


def test_pruning_reports_what_it_dropped():
    claims = {
        "/a": {"path": "/a", "owner": "x", "claimed": 0.0, "expires": 10.0},
        "/b": {"path": "/b", "owner": "x", "claimed": 0.0, "expires": 100.0},
    }
    assert handler._prune_scopes(claims, 50.0) == ["/a"]
    assert sorted(claims) == ["/b"]


def test_an_expired_claim_does_not_block_a_new_one(monkeypatch):
    clock = {"now": 1000.0}
    monkeypatch.setattr(handler.time, "time", lambda: clock["now"])

    handler.m_claim_scope({"path": "/project1/audio", "owner": "agent-a", "ttl": 60})
    clock["now"] = 1100.0

    granted = handler.m_claim_scope(
        {"path": "/project1/audio", "owner": "agent-b", "ttl": 60}
    )
    assert granted["owner"] == "agent-b"
    assert handler.m_scopes({})["count"] == 1


# -- claiming ---------------------------------------------------------------

def test_a_claim_is_recorded_with_its_expiry(monkeypatch):
    monkeypatch.setattr(handler.time, "time", lambda: 1000.0)

    view = handler.m_claim_scope(
        {"path": "/project1/audio/", "owner": " agent-a ", "ttl": 60}
    )

    assert view["path"] == "/project1/audio"
    assert view["owner"] == "agent-a"
    assert view["expires"] == 1060.0
    assert view["expiresIn"] == 60.0
    assert view["renewed"] is False
    assert view["expiresAt"] == handler._stamp(1060.0)


def test_an_overlapping_claim_by_another_owner_is_refused(monkeypatch):
    monkeypatch.setattr(handler.time, "time", lambda: 1000.0)
    handler.m_claim_scope({"path": "/project1/audio", "owner": "agent-a", "ttl": 60})

    with pytest.raises(handler.ScopeHeld) as caught:
        handler.m_claim_scope(
            {"path": "/project1/audio/eq1", "owner": "agent-b", "ttl": 60}
        )

    assert "agent-a" in str(caught.value)
    # The sibling that only looks like a prefix is still free.
    handler.m_claim_scope({"path": "/project1/audio2", "owner": "agent-b", "ttl": 60})
    assert handler.m_scopes({})["count"] == 2


def test_reclaiming_ones_own_scope_renews_it(monkeypatch):
    clock = {"now": 1000.0}
    monkeypatch.setattr(handler.time, "time", lambda: clock["now"])
    handler.m_claim_scope({"path": "/project1/audio", "owner": "agent-a", "ttl": 60})

    clock["now"] = 1030.0
    view = handler.m_claim_scope(
        {"path": "/project1/audio", "owner": "agent-a", "ttl": 60}
    )

    assert view["renewed"] is True
    assert view["claimed"] == 1000.0
    assert view["expires"] == 1090.0
    assert handler.m_scopes({})["count"] == 1


def test_an_owner_may_hold_nested_scopes_of_its_own(monkeypatch):
    monkeypatch.setattr(handler.time, "time", lambda: 1000.0)
    handler.m_claim_scope({"path": "/project1/audio", "owner": "agent-a", "ttl": 60})
    handler.m_claim_scope(
        {"path": "/project1/audio/eq1", "owner": "agent-a", "ttl": 60}
    )
    assert handler.m_scopes({})["count"] == 2


# -- the write guard --------------------------------------------------------

def test_a_write_by_another_owner_is_refused_and_says_what_to_do(monkeypatch):
    monkeypatch.setattr(handler.time, "time", lambda: 1000.0)
    handler.m_claim_scope({"path": "/project1/audio", "owner": "agent-a", "ttl": 60})

    with pytest.raises(handler.ScopeHeld) as caught:
        handler._guard_scopes({"owner": "agent-b"}, "/project1/audio/eq1")

    message = str(caught.value)
    assert "/project1/audio/eq1" in message
    assert "agent-a" in message
    assert handler._stamp(1000.0) in message
    assert handler._stamp(1060.0) in message
    assert "release_scope" in message


def test_the_owners_own_writes_pass(monkeypatch):
    monkeypatch.setattr(handler.time, "time", lambda: 1000.0)
    handler.m_claim_scope({"path": "/project1/audio", "owner": "agent-a", "ttl": 60})

    handler._guard_scopes({"owner": "agent-a"}, "/project1/audio/eq1")
    # And so does anybody's write outside the claimed subtree.
    handler._guard_scopes({"owner": "agent-b"}, "/project1/audio2/eq1")
    handler._guard_scopes({"owner": "agent-b"}, "/project1/video/blur1")


def test_an_unattributed_write_is_refused_inside_a_claim(monkeypatch):
    """No owner matches no claim: the guard fails safe, and names the owner."""
    monkeypatch.setattr(handler.time, "time", lambda: 1000.0)
    handler.m_claim_scope({"path": "/project1/audio", "owner": "agent-a", "ttl": 60})

    with pytest.raises(handler.ScopeHeld, match="unnamed caller"):
        handler._guard_scopes({}, "/project1/audio/eq1")


def test_with_nothing_claimed_every_write_passes():
    handler._guard_scopes({}, "/project1/audio/eq1")
    handler._guard_scopes({"owner": "agent-b"}, "/project1/audio/eq1")


def test_a_claim_below_the_target_does_not_block_the_target(monkeypatch):
    monkeypatch.setattr(handler.time, "time", lambda: 1000.0)
    handler.m_claim_scope(
        {"path": "/project1/audio/eq1", "owner": "agent-a", "ttl": 60}
    )
    handler._guard_scopes({"owner": "agent-b"}, "/project1/audio")


def test_a_relative_path_is_skipped_rather_than_guessed_at(monkeypatch):
    """Resolving it needs TouchDesigner's rules; the guard says so by not lying."""
    monkeypatch.setattr(handler.time, "time", lambda: 1000.0)
    handler.m_claim_scope({"path": "/project1/audio", "owner": "agent-a", "ttl": 60})
    handler._guard_scopes({"owner": "agent-b"}, "eq1")


# -- release and listing ----------------------------------------------------

def test_release_frees_the_scope_for_the_next_agent(monkeypatch):
    monkeypatch.setattr(handler.time, "time", lambda: 1000.0)
    handler.m_claim_scope({"path": "/project1/audio", "owner": "agent-a", "ttl": 60})

    assert handler.m_release_scope(
        {"path": "/project1/audio/", "owner": "agent-a"}
    ) == {"released": True, "path": "/project1/audio", "owner": "agent-a"}

    assert handler.m_scopes({})["count"] == 0
    handler._guard_scopes({"owner": "agent-b"}, "/project1/audio/eq1")


def test_only_the_owner_can_release(monkeypatch):
    monkeypatch.setattr(handler.time, "time", lambda: 1000.0)
    handler.m_claim_scope({"path": "/project1/audio", "owner": "agent-a", "ttl": 60})

    with pytest.raises(handler.ScopeHeld, match="agent-a"):
        handler.m_release_scope({"path": "/project1/audio", "owner": "agent-b"})
    assert handler.m_scopes({})["count"] == 1


def test_releasing_nothing_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(handler.time, "time", lambda: 1000.0)
    result = handler.m_release_scope({"path": "/project1/audio", "owner": "agent-a"})
    assert result["released"] is False
    assert "no live claim" in result["note"]


def test_listing_shows_owner_and_when_each_lapses(monkeypatch):
    clock = {"now": 1000.0}
    monkeypatch.setattr(handler.time, "time", lambda: clock["now"])
    handler.m_claim_scope({"path": "/project1/audio", "owner": "agent-a", "ttl": 60})
    handler.m_claim_scope({"path": "/project1/video", "owner": "agent-b", "ttl": 600})

    clock["now"] = 1070.0
    listing = handler.m_scopes({})

    assert listing["count"] == 1
    assert listing["expired"] == ["/project1/audio"]
    only = listing["scopes"][0]
    assert only["path"] == "/project1/video"
    assert only["owner"] == "agent-b"
    assert only["expires"] == 1600.0
    assert only["expiresIn"] == 530.0
    assert only["expiresAt"] == handler._stamp(1600.0)


# -- the guard is wired into the writing methods ----------------------------

@pytest.mark.parametrize(
    "method,params",
    [
        ("op_create", {"parent": "/project1/audio", "type": "audiofilterCHOP"}),
        ("op_delete", {"path": "/project1/audio/eq1"}),
        ("op_connect", {"from": "/project1/osc1", "to": "/project1/audio/eq1"}),
        ("op_disconnect", {"path": "/project1/audio/eq1"}),
        ("par_set", {"path": "/project1/audio/eq1", "pars": {"gain": 2}}),
    ],
)
def test_every_writing_method_refuses_a_claimed_target(method, params, monkeypatch):
    """The refusal comes before the operator lookup, so it needs no TouchDesigner.

    That ordering is the point: a second agent gets told who holds the area
    whether or not the node it aimed at exists.
    """
    monkeypatch.setattr(handler.time, "time", lambda: 1000.0)
    handler.m_claim_scope({"path": "/project1/audio", "owner": "agent-a", "ttl": 60})

    with pytest.raises(handler.ScopeHeld, match="agent-a"):
        handler.METHODS[method](dict(params, owner="agent-b"))


def test_a_batch_carries_its_owner_into_every_step(monkeypatch):
    """Otherwise an agent's own batch bounces off its own claim."""

    class FakeUndo:
        def startBlock(self, _name):
            pass

        def endBlock(self):
            pass

    class FakeUi:
        undo = FakeUndo()

    seen = []
    monkeypatch.setattr(handler, "ui", FakeUi(), raising=False)
    monkeypatch.setitem(handler.METHODS, "probe", lambda p: seen.append(p) or {})
    monkeypatch.setattr(handler.time, "time", lambda: 1000.0)
    handler.m_claim_scope({"path": "/project1/audio", "owner": "agent-a", "ttl": 60})

    handler.m_batch(
        {
            "owner": "agent-a",
            "ops": [
                {"method": "probe", "params": {"path": "/project1/audio/eq1"}},
                {"method": "probe", "params": {"path": "/x", "owner": "agent-a"}},
            ],
        }
    )

    assert [step["owner"] for step in seen] == ["agent-a", "agent-a"]


# -- registration and inertness --------------------------------------------

def test_the_three_methods_are_registered():
    assert handler.METHODS["claim_scope"] is handler.m_claim_scope
    assert handler.METHODS["release_scope"] is handler.m_release_scope
    assert handler.METHODS["scopes"] is handler.m_scopes


def test_importing_the_module_creates_no_claim_state():
    """The host imports this module for PROTOCOL_VERSION; it must stay inert.

    No table, no dict, no disk: the claims are found in the network when a
    request asks for them, and the accessor that finds them touches `me` only
    inside its body.
    """
    reloaded = importlib.reload(handler)

    assert not hasattr(reloaded, "_SCOPES")
    assert reloaded.SCOPE_TABLE == "tdatlas_scopes"


def test_claims_survive_the_module_being_re_executed(claim_table, monkeypatch):
    """The defect this carrier exists to fix, at host scale.

    Measured on a live 2025.32460: one health_sample request re-ran this
    module's body — `id(globals())` went 5450312064 -> 5451093184 — and a
    claim held in a module-level dict vanished with no expiry and nothing in
    the `expired` list. `importlib.reload` is the same event here: the module
    body runs again, and the claim must still be there, because the table it
    lives in is not part of the module.
    """
    monkeypatch.setattr(handler.time, "time", lambda: 1000.0)
    handler.m_claim_scope({"path": "/project1/audio", "owner": "agent-a", "ttl": 600})

    reloaded = importlib.reload(handler)
    # Reload restores the real accessor, so point the fresh body back at the
    # same table — which is what TouchDesigner does, the DAT being a child of
    # the COMP rather than anything the module owns.
    monkeypatch.setattr(reloaded, "_scope_table", lambda create=False: claim_table)
    monkeypatch.setattr(reloaded.time, "time", lambda: 1001.0)

    listing = reloaded.m_scopes({})

    assert listing["count"] == 1
    assert listing["scopes"][0]["owner"] == "agent-a"
    assert listing["expired"] == []
    # And the guard raised from the reloaded body still refuses a stranger.
    with pytest.raises(reloaded.ScopeHeld, match="agent-a"):
        reloaded._guard_scopes({"owner": "agent-b"}, "/project1/audio/eq1")


# -- the table as a carrier -------------------------------------------------

def test_a_claim_is_written_as_a_row_a_person_can_read(claim_table, monkeypatch):
    monkeypatch.setattr(handler.time, "time", lambda: 1000.0)

    view = handler.m_claim_scope(
        {"path": "/project1/audio", "owner": "agent-a", "ttl": 60}
    )

    assert claim_table.rows()[0] == list(handler._SCOPE_HEADER)
    assert claim_table.rows()[1] == [
        "/project1/audio",
        "agent-a",
        repr(1000.0),
        repr(1060.0),
        str(os.getpid()),
    ]
    assert view["table"] == claim_table.path


def test_rows_from_another_process_do_not_count(claim_table, monkeypatch):
    """A table saved into a .toe must not come back as a claim held forever.

    Nothing would ever clear it: its owner is a process that no longer exists,
    so no release arrives and the expiry it carries may be days away.
    """
    monkeypatch.setattr(handler.time, "time", lambda: 1000.0)
    claim_table.appendRow(list(handler._SCOPE_HEADER))
    claim_table.appendRow(
        ["/project1/audio", "agent-gone", repr(1.0), repr(1e18),
         str(os.getpid() + 1)]
    )

    assert handler.m_scopes({})["count"] == 0
    handler._guard_scopes({"owner": "agent-b"}, "/project1/audio/eq1")
    # And the next write sweeps the row away, so the board stops lying.
    handler.m_claim_scope({"path": "/project1/video", "owner": "agent-b", "ttl": 60})
    assert [row[0] for row in claim_table.rows()[1:]] == ["/project1/video"]


def test_a_hand_edited_row_is_skipped_not_raised(claim_table, monkeypatch):
    """A raise here would refuse every edit in the project until it was found."""
    monkeypatch.setattr(handler.time, "time", lambda: 1000.0)
    pid = str(os.getpid())
    claim_table.appendRow(list(handler._SCOPE_HEADER))
    claim_table.appendRow(["oops"])
    claim_table.appendRow(["relative/path", "agent-a", "1.0", "1e18", pid])
    claim_table.appendRow(["/project1/audio", "", "1.0", "1e18", pid])
    claim_table.appendRow(["/project1/video", "agent-a", "later", "1e18", pid])
    claim_table.appendRow(["/project1/ctl", "agent-a", "1.0", "1e18", "not-a-pid"])
    claim_table.appendRow(["/project1/good", "agent-a", "1.0", repr(1e18), pid])

    listing = handler.m_scopes({})

    assert [claim["path"] for claim in listing["scopes"]] == ["/project1/good"]
    with pytest.raises(handler.ScopeHeld, match="agent-a"):
        handler._guard_scopes({"owner": "agent-b"}, "/project1/good/eq1")


def test_the_guard_never_writes_to_the_table(claim_table, monkeypatch):
    """It runs on every network write; dirtying a DAT there is not affordable.

    An expired row is filtered in memory, and swept by the next claim, release
    or listing instead.
    """
    clock = {"now": 1000.0}
    monkeypatch.setattr(handler.time, "time", lambda: clock["now"])
    handler.m_claim_scope({"path": "/project1/audio", "owner": "agent-a", "ttl": 60})
    before = claim_table.rows()

    clock["now"] = 2000.0
    monkeypatch.setattr(
        claim_table, "appendRow", lambda cells: pytest.fail("the guard wrote")
    )
    monkeypatch.setattr(
        claim_table, "clear", lambda: pytest.fail("the guard wrote")
    )
    handler._guard_scopes({"owner": "agent-b"}, "/project1/audio/eq1")

    assert claim_table.rows() == before


def test_a_claim_that_is_not_kept_is_not_reported_as_held(claim_table, monkeypatch):
    """Read back before promising. The defect was a claim accepted and lost."""
    monkeypatch.setattr(handler.time, "time", lambda: 1000.0)
    monkeypatch.setattr(claim_table, "appendRow", lambda cells: None)

    with pytest.raises(RuntimeError, match="did not survive"):
        handler.m_claim_scope(
            {"path": "/project1/audio", "owner": "agent-a", "ttl": 60}
        )


def test_an_owner_with_a_tab_or_newline_is_refused():
    """Those are the table's own separators; a name carrying one comes back split."""
    with pytest.raises(ValueError, match="tabs or newlines"):
        handler._normalise_owner("agent\ta")
    with pytest.raises(ValueError, match="tabs or newlines"):
        handler._normalise_owner("agent\nb")


# -- the claim has to reach the tools that write ----------------------------

def test_the_batch_tool_carries_the_owner_all_the_way_to_the_bridge(monkeypatch):
    """A claim nobody can write under is worse than no claim.

    The bridge already carried a batch-level owner into every step, but the
    host side had no way to send one: an agent that claimed an area was
    refused by its own claim on td_build, the very tool the server's
    instructions recommend for multi-step edits.
    """
    from td_atlas.mcp import server

    sent = {}

    class Recorder:
        ambiguity_warning = ""
        version_warning = ""

        def call(self, method, **params):
            sent[method] = params
            return {"applied": 1, "results": [{"path": "/project1/blur1"}]}

        def batch(self, ops, undo_name="td-atlas batch", owner=""):
            return self.call("batch", ops=ops, undo_name=undo_name, owner=owner)

    monkeypatch.setattr(server, "bridge", Recorder)
    # No index in this test: validation is a convenience and is skipped.
    monkeypatch.setattr(
        server, "store", lambda: (_ for _ in ()).throw(RuntimeError("no index"))
    )

    server.td_build(
        [{"method": "op_create", "params": {"parent": "/project1"}}],
        owner="agent-a",
    )

    assert sent["batch"]["owner"] == "agent-a"
