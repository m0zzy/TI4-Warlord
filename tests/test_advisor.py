import json

import pytest

from warlord.engine.apply import ApplyRefused, HiddenInformation, apply_action
from warlord.engine.evaluate import evaluate_position
from warlord.engine.options import initiative, legal_actions
from warlord.state.perspective import project
from warlord.tools.registry import ToolContext, run_tool

MOVE_TO_201 = {"type": "tactical", "player": "p3", "activate": "201",
               "moves": [{"from": "102", "units": {"carrier": 1, "fighter": 2, "infantry": 2}}]}


def test_initiative():
    assert initiative("trade") == 5 and initiative("sc.pok8imperial") == 8 and initiative("sc.te4construction") == 4


def test_legal_actions_families(view, kb):
    opts = legal_actions(view, kb, "p3")
    assert opts["pass"]["allowed"] is False and "trade" in opts["pass"]["why_not"][0]
    assert opts["strategic"] == [{"card": "trade", "initiative": 5}]
    t201 = next(o for o in opts["tactical"] if o["system"] == "201")
    assert t201["other_players_ships"] and t201["reachable_by"][0]["ships"] == {"carrier": 1, "dreadnought": 1}
    assert all(o["system"] != "101" for o in opts["tactical"])  # already activated


def test_legal_actions_for_opponent_shows_no_hidden_cards(view, kb):
    opts = legal_actions(view, kb, "p2")
    assert "tactical" not in opts  # not p2's turn
    assert "ac.direct_hit" not in json.dumps(opts) and "ac.sabotage" not in json.dumps(opts)


def test_apply_stops_at_space_combat(state, kb):
    out = apply_action(state, MOVE_TO_201, kb, viewer="p3")
    new = out["state"]
    assert out["verified"] and out["pending"][0]["step"] == "space_combat"
    assert new.system("201").space["p3"] == {"carrier": 1, "fighter": 2, "infantry": 2}
    assert "p3" in new.system("201").command_tokens and new.players["p3"].command_tokens.tactic == 1
    assert new.system("102").planets["wellon"].units == {}  # the infantry came off Wellon
    assert new.turn.active_player == "p3" and new.turn.step == "space_combat"
    assert state.system("201").space.get("p3") is None  # the original is untouched


def test_apply_full_turn_and_turn_order(state, kb):
    move = {"type": "tactical", "player": "p3", "activate": "000", "moves": [{"from": "102", "units": {"carrier": 1}}]}
    out = apply_action(state, move, kb, viewer="p3")
    assert out["pending"] == [] and out["state"].turn.active_player == "p2"  # p1 has passed; p2 (7) follows p3 (5)
    assert "Turn passes to p2." in out["events"]


def test_apply_invasion_takes_control(state, kb):
    state.system("000").planets["mecatol_rex"].controller = None
    move = {"type": "tactical", "player": "p3", "activate": "000",
            "moves": [{"from": "102", "units": {"carrier": 1, "infantry": 2}}],
            "invade": [{"planet": "mecatol_rex", "infantry": 2}]}
    out = apply_action(state, move, kb, viewer="p3")
    rex = out["state"].system("000").planets["mecatol_rex"]
    assert out["verified"] is False  # custodians token not tracked
    assert rex.controller == "p3" and rex.exhausted and rex.units["p3"] == {"infantry": 2}


def test_apply_refuses_illegal_and_hidden(state, kb):
    with pytest.raises(ApplyRefused):
        apply_action(state, {"type": "pass", "player": "p3"}, kb, viewer="p3")
    with pytest.raises(HiddenInformation):
        apply_action(state, {"type": "component", "player": "p2", "source": "ac.direct_hit"}, kb, viewer="p3")


def test_evaluate_position(view, kb):
    ev = evaluate_position(view, kb, "p3")
    assert [r["player"] for r in ev["vp_race"]][:2] == ["p2", "p3"] or ev["vp_race"][0]["vp"] == 4
    assert ev["spending_power"]["trade_goods"] == 5 and ev["secret_objectives_in_hand"] == 1
    assert any(t["by"] == "p2" and t["to"] == "101" for t in ev["threats"])
    assert ev["public_objectives"][0]["scored_by_you"] is True


def test_tools_through_registry(kb, store):
    ctx = ToolContext(kb=kb, store=store, game_id="pbd00000", viewer="p3")
    out, err = run_tool(ctx, "apply_action", {"action": MOVE_TO_201})
    data = json.loads(out)
    assert not err and data["applied"] and data["pending"][0]["step"] == "space_combat"
    assert {s["position"] for s in data["changes"]["systems"]} == {"102", "201"}
    refused = json.loads(run_tool(ctx, "apply_action", {"action": {"type": "pass", "player": "p3"}})[0])
    assert refused == {"applied": False, "illegal": refused["illegal"]} and refused["illegal"]
    hidden_out, hidden_err = run_tool(ctx, "apply_action", {"action": {"type": "component", "player": "p2", "source": "ac.x"}})
    assert hidden_err
    out, err = run_tool(ctx, "legal_actions", {})
    assert not err and json.loads(out)["strategic"]
    out, err = run_tool(ctx, "evaluate_position", {"player": "p2"})
    ev = json.loads(out)
    assert not err and ev["secret_objectives_in_hand"] is None  # p2's hand stays hidden from p3
