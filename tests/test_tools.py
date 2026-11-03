import json

from warlord.tools.registry import REFEREE_TOOLS, ToolContext, run_tool, tool_definitions


def ctx(kb, store):
    return ToolContext(kb=kb, store=store, game_id="pbd00000", viewer="p3")


def test_definitions_fixed_order():
    names = [t["name"] for t in tool_definitions()]
    assert names == list(REFEREE_TOOLS)
    assert all("input_schema" in t and "description" in t for t in tool_definitions())


def test_get_state_refuses_other_viewer(kb, store):
    out, err = run_tool(ctx(kb, store), "get_state", {"viewer": "p2"})
    assert err and "hidden" in json.loads(out)["error"]


def test_get_state_returns_view(kb, store):
    out, err = run_tool(ctx(kb, store), "get_state", {})
    data = json.loads(out)
    assert not err and data["viewer"] == "p3" and "private" not in json.dumps(data["players"])


def test_lookups_record_citable_ids(kb, store):
    c = ctx(kb, store)
    run_tool(c, "rules_lookup", {"question": "move out of activated system"})
    run_tool(c, "card_lookup", {"name": "Sabotage"})
    assert "lrr.sample.movement" in c.seen_ids and "ac.sabotage" in c.seen_ids


def test_card_lookup_reports_text_only(kb, store):
    out, _ = run_tool(ctx(kb, store), "card_lookup", {"name": "Sabotage"})
    assert json.loads(out)["coverage"] == "text_only"


def test_validate_records_coverage(kb, store):
    c = ctx(kb, store)
    run_tool(c, "validate_action", {"action": {"type": "tactical", "activate": "201"}})
    assert c.coverage == [{"tool": "validate_action", "coverage": "engine", "legal": True}]


def test_unknown_and_bad_inputs(kb, store):
    assert run_tool(ctx(kb, store), "nope", {})[1]
    assert run_tool(ctx(kb, store), "combat_sim", {"attacker": {}})[1]
    assert run_tool(ctx(kb, store), "rules_lookup", {})[1]
