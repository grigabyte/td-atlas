"""Cooperative scope claims: path containment, expiry, refusal, release.

Two agents on one TouchDesigner overwrite each other in silence; a claim is
what turns that into a refusal naming the other owner. None of this needs
TouchDesigner running: the comparison and expiry logic is plain module-level
code, and the three methods touch nothing but a dict, so they are called
directly here the way the Web Server DAT would call them.
"""

from __future__ import annotations

import pytest

from td_atlas.component import handler


@pytest.fixture(autouse=True)
def empty_scopes(monkeypatch):
    """A clean claim table per test — the real one is module state."""
    monkeypatch.setattr(handler, "_SCOPES", {})
    return handler._SCOPES


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


def test_the_claim_table_is_empty_at_import():
    """The host imports this module for PROTOCOL_VERSION; claims are in memory.

    Nothing about them may touch disk or exist before the first claim.
    """
    import importlib

    reloaded = importlib.reload(handler)
    assert reloaded._SCOPES == {}
