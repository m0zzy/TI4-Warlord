import asyncio
import json
import shutil

import pytest
from mcp import Client

from warlord.mcp_server import build_server, load_session

from conftest import SAMPLE


@pytest.fixture()
def data(tmp_path):
    shutil.copytree(SAMPLE / "kb", tmp_path / "kb")
    (tmp_path / "games").mkdir()
    shutil.copy(SAMPLE / "games" / "pbd00000.json", tmp_path / "games")
    export = json.loads((SAMPLE / "games" / "pbd00000.json").read_text())
    export["meta"]["game_id"] = "exported1"
    export["meta"]["source"] = "asyncti4_export"
    (tmp_path / "games" / "exported1.json").write_text(json.dumps(export))
    return tmp_path


def run(data, calls):
    """Run tool calls in order through an in-memory MCP client; return (is_error, payload) per call."""

    async def go():
        server = build_server(load_session(data))
        out = []
        async with Client(server) as c:
            for name, args in calls:
                r = await c.call_tool(name, args)
                text = r.content[0].text if r.content else ""
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError:
                    payload = text
                out.append((r.is_error, payload))
        return out

    return asyncio.run(go())


def test_tools_and_prompt_listed(data):
    async def go():
        async with Client(build_server(load_session(data))) as c:
            tools = {t.name for t in (await c.list_tools()).tools}
            prompt = await c.get_prompt("referee", {"question": "Can I pass?"})
            return tools, prompt.messages[0].content.text

    tools, prompt = asyncio.run(go())
    assert {"list_games", "open_game", "fetch_live_game", "set_my_hand", "get_state", "rules_lookup",
            "card_lookup", "validate_action", "combat_sim"} <= tools
    assert "status_line" in prompt and prompt.endswith("The player asks: Can I pass?")


def test_game_tools_need_an_open_game(data):
    (err, msg), = run(data, [("get_state", {})])
    assert err and "open_game" in msg


def test_export_games_are_never_served(data):
    (_, listing), (err, msg) = run(data, [("list_games", {}), ("open_game", {"game": "exported1", "seat": "p3"})])
    assert [g["game"] for g in listing["games"]] == ["pbd00000"]
    assert err and "hidden cards" in msg


def test_open_game_binds_one_seat(data):
    results = run(data, [
        ("open_game", {"game": "pbd00000", "seat": "p9"}),
        ("open_game", {"game": "pbd00000", "seat": "p3"}),
        ("get_state", {}),
    ])
    assert results[0][0] and "Seats: p1, p2, p3" in results[0][1]
    view = results[2][1]
    assert view["viewer"] == "p3" and view["hand"]["action_cards"] == ["ac.sabotage"]
    assert "ac.direct_hit" not in json.dumps(view)  # p2's card stays hidden


def test_validate_action_status_line(data):
    results = run(data, [
        ("open_game", {"game": "pbd00000", "seat": "p3"}),
        ("validate_action", {"action": {"type": "tactical", "player": "p3", "activate": "201",
                                        "moves": [{"from": "101", "units": {"cruiser": 1}}]}}),
        ("validate_action", {"action": {"type": "tactical", "player": "p3", "activate": "201"}}),
        ("validate_action", {"action": {"type": "tactical", "player": "p3", "activate": "201",
                                        "moves": [{"from": "102", "units": {"carrier": 1, "infantry": 1}}],
                                        "invade": [{"planet": "quann", "infantry": 1}]}}),
    ])
    assert results[1][1]["status_line"].startswith("Illegal, engine-verified: Units cannot move out of 101")
    assert results[2][1]["status_line"] == "Legal, engine-verified."
    assert results[3][1]["status_line"].startswith("Not verified by the engine. Unchecked: invasion")


def test_set_my_hand_resolves_names(data):
    results = run(data, [
        ("open_game", {"game": "pbd00000", "seat": "p3"}),
        ("set_my_hand", {"action_cards": ["sabotge", "Direct Hit"]}),
        ("get_state", {}),
        ("set_my_hand", {"action_cards": ["Gravity Drive"]}),
    ])
    assert [r["id"] for r in results[1][1]["resolved"]["action_card"]] == ["ac.sabotage", "ac.direct_hit"]
    assert results[2][1]["hand"]["action_cards"] == ["ac.sabotage", "ac.direct_hit"]
    assert results[3][0] and "not a known action card" in results[3][1]


def test_lookups_and_combat(data):
    results = run(data, [
        ("rules_lookup", {"question": "move out of a system with my command token", "k": 3}),
        ("card_lookup", {"name": "Sabotage"}),
        ("combat_sim", {"attacker": {"units": {"dreadnought": 2}}, "defender": {"units": {"fighter": 1}}, "trials": 200}),
    ])
    assert "lrr.sample.movement" in [r["id"] for r in results[0][1]["results"]]
    assert results[1][1]["match"]["id"] == "ac.sabotage"
    assert results[2][1]["attacker_win"] > 0.9


def test_missing_knowledge_base_is_explained(tmp_path):
    (err, msg), = run(tmp_path, [("rules_lookup", {"question": "anything"})])
    assert err and "warlord setup" in msg


def test_check_ruling_flags_problems_then_resets(data):
    illegal = {"type": "tactical", "player": "p3", "activate": "201", "moves": [{"from": "101", "units": {"cruiser": 1}}]}
    results = run(data, [
        ("open_game", {"game": "pbd00000", "seat": "p3"}),
        ("check_ruling", {"answer": "No.", "citations": ["lrr.sample.movement", "lrr.89.2"], "claims_verified": True}),
        ("rules_lookup", {"question": "move out of a system with my command token", "k": 3}),
        ("validate_action", {"action": illegal}),
        ("check_ruling", {"answer": "No: your cruiser is in a system you already activated, so it can't move.",
                          "citations": ["lrr.sample.movement"], "claims_verified": True}),
        ("check_ruling", {"answer": "No.", "citations": ["lrr.sample.movement"], "claims_verified": False}),
    ])
    first = results[1][1]
    assert not first["ok"]
    assert first["citations"] == {"backed": [], "not_looked_up": ["lrr.sample.movement"], "unknown": ["lrr.89.2"]}
    assert any("claims to be verified" in p for p in first["problems"])
    passed = results[4][1]
    assert passed["ok"] and passed["label"] == "Engine-verified." and passed["session_record"]
    after_reset = results[5][1]
    assert after_reset["citations"]["not_looked_up"] == ["lrr.sample.movement"]  # record was reset


def test_check_ruling_flags_long_quotes(data):
    movement = json.loads((data / "kb" / "rules.sample.jsonl").read_text().splitlines()[6])["text"]
    quoted = movement[movement.index("Ships cannot"):]
    results = run(data, [
        ("rules_lookup", {"question": "movement out of a system with a command token", "k": 3}),
        ("check_ruling", {"answer": f"The rule says: {quoted}", "citations": ["lrr.sample.movement"]}),
    ])
    check = results[1][1]
    assert not check["ok"] and check["long_quotes"] and "Paraphrase" in " ".join(check["problems"])
    assert check["label"].startswith("Unverified:")
