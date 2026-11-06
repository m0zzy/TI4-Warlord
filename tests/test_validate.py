import pytest

from warlord.engine.validate import kb_ref_resolver, validate_action


def tactical(**kw):
    return {"type": "tactical", "player": "p3", "activate": "201", **kw}


def test_activation_without_moves_is_fully_checked(view):
    r = validate_action(view, tactical())
    assert r.legal is True and r.coverage == "engine"


def test_reachable_move_is_engine_verified(view):
    # Carrier II (move 2) from 102 via 101 to 201, carrying 2 fighters and 2 infantry (capacity 6).
    r = validate_action(view, tactical(moves=[{"from": "102", "units": {"carrier": 1, "fighter": 2, "infantry": 2}}]))
    assert r.legal is True and r.coverage == "engine" and r.unchecked == []


def test_invasion_is_still_unchecked(view):
    r = validate_action(view, tactical(moves=[{"from": "102", "units": {"carrier": 1, "infantry": 2}}],
                                       invade=[{"planet": "quann", "infantry": 2}]))
    assert r.legal is None and any("invasion" in u for u in r.unchecked)


def test_cannot_move_out_of_activated_system(view):
    r = validate_action(view, tactical(moves=[{"from": "101", "units": {"cruiser": 1}}]))
    assert r.legal is False and r.coverage == "engine"
    assert [v.topic for v in r.violations] == ["movement.origin"]


def test_cannot_activate_system_with_own_token(view):
    r = validate_action(view, tactical(activate="101"))
    assert r.legal is False
    assert "already contains" in r.violations[0].message


def test_units_must_be_present(view):
    r = validate_action(view, tactical(moves=[{"from": "102", "units": {"war_sun": 1}}]))
    assert r.legal is False


def test_ground_forces_on_planets_can_be_picked_up(view):
    r = validate_action(view, tactical(moves=[{"from": "102", "units": {"carrier": 1, "infantry": 2}}]))
    assert r.legal is True  # the 2 infantry come off Wellon, a planet in 102


def test_not_your_turn(view):
    r = validate_action(view, {"type": "pass", "player": "p2"})
    assert r.legal is False


def test_cannot_pass_with_unused_strategy_card(view):
    r = validate_action(view, {"type": "pass", "player": "p3"})
    assert r.legal is False and r.violations[0].topic == "passing"


def test_pass_legal_after_strategic_action(view):
    view.players["p3"].strategy_cards[0].primary_used = True
    assert validate_action(view, {"type": "pass", "player": "p3"}).legal is True


def test_no_tactic_tokens(view):
    view.players["p3"].command_tokens.tactic = 0
    r = validate_action(view, tactical())
    assert r.legal is False


def test_respond_without_window(view):
    r = validate_action(view, {"type": "respond", "player": "p3", "play": "ac.sabotage"})
    assert r.legal is False


def test_respond_card_must_be_in_hand(view):
    from warlord.state.schema import OpenWindow

    view.turn.open_window = OpenWindow(timing="when", event="action_card_played", awaiting=["p3"])
    assert validate_action(view, {"type": "respond", "player": "p3", "play": "ac.direct_hit"}).legal is False
    r = validate_action(view, {"type": "respond", "player": "p3", "play": "ac.sabotage"})
    assert r.legal is None and r.unchecked


def test_malformed_action(view):
    r = validate_action(view, {"type": "tactical", "player": "p3"})
    assert r.legal is None and r.schema_errors


def test_refs_resolved_from_kb(view, kb):
    r = validate_action(view, tactical(moves=[{"from": "101", "units": {"cruiser": 1}}]), kb_ref_resolver(kb, view.ruleset))
    assert "lrr.sample.movement" in r.violations[0].refs
