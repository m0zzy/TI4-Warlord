import io
import json
import zipfile

import pytest

from warlord.state.importers.asyncti4 import from_save, from_web_data, set_hand
from warlord.state.importers.fetch import MAX_EXPORT_GAMES, CapReached, fetch_completed
from warlord.state.perspective import project
from warlord.state.store import GameStore

USER_A, USER_B = "111111111111111111", "222222222222222222"

# A tiny save in AsyncTI4's line format. User ids and names are fake.
SAVE = "\n".join([
    USER_A, "Owner Name", "testgame1", "-mapinfo-", "-gameinfo-",
    "phase_of_game action",
    "round 3",
    f"speaker {USER_B}",
    f"active_player {USER_A}",
    "vp_count 10",
    "thunders_edge_mode true",
    "ac_deck_id action_cards_te",
    "strategy_card_set te",
    "sc_played 1,true;2,false;",
    "revealedPublicObjectives corner,0;Custodian/Imperial,1;",
    "customPublicVP Custodian/Imperial,1;",
    f"scoredPublicObjectives corner,{USER_A}-{USER_B};Custodian/Imperial,{USER_B};",
    "publicObjectives1 found,other",
    "law minister_peace,990;",
    "law_info minister_peace,ghost;",
    "action_cards_discarded refit,173;",
    "-endgameinfo-", "-playerinfo-",
    "-player-", USER_A, "Player A Name",
    "faction ghost", "color teal", "passed false", "notepad secret plans here",
    "tactical_cc 2", "fleet_cc 3", "strategy_cc 1", "trade_goods 4",
    "tech gd,st", "tech_exhausted gd", "units_owned carrier2,dreadnought",
    "strategy_card 1",
    "action_cards sabo1,906;", "promissory_notes teal_ps,84;iff,47;",
    "promissory_notes_play_area plum_sftt",
    "secret_objectives ans,626;", "secret_objectives_scored dhw,103;",
    "planets quann", "planets_exhausted quann",
    "leaders ghostagent,agent,0,true,false,false;",
    "-endplayer-",
    "-player-", USER_B, "Player B Name",
    "faction xxcha", "color plum", "passed true",
    "tactical_cc 1", "fleet_cc 3", "strategy_cc 2", "strategy_card 2",
    "action_cards ", "secret_objectives ", "planets ",
    "-endplayer-", "-endplayerinfo-", "-endmapinfo-",
    "-tile-", "25 201", "-unitholder-",
    "-units-", "space", "tea—cv 1,0,0,0", "tea—dn 1,1,0,0", "-endunits-",
    "-planettokens-", "command_tea.png", "-planetendtokens-",
    "-units-", "quann", "tea—gf 2,0,0,0", "-endunits-",
    "-planettokens-", "control_tea.png", "attachment_richworld.png", "-planetendtokens-",
    "-endunitholder-", "-tokens-", "-endtokens-", "-endtile-",
])
EVENTS = [
    {"seq": 1, "archetype": "MANUAL_COMMAND", "round": 3, "phase": "action", "faction": "ghost",
     "payload": {"user": "Player A Name", "command": "/remove_cc"}},
    {"seq": 2, "archetype": "SC_PLAYED", "round": 3, "phase": "action", "faction": "ghost", "payload": {"scNumber": 1}},
]
COLORS = {"tea": "teal", "plm": "plum"}


def test_save_import_maps_everything_and_drops_personal_data():
    r = from_save(SAVE, EVENTS, kb=None, colors=COLORS)
    s = r.state
    assert s.meta.game_id == "testgame1" and s.turn.round == 3 and s.turn.phase == "action"
    assert s.turn.speaker == "xxcha" and s.turn.active_player == "ghost"
    assert s.ruleset.thunders_edge and s.ruleset.decks["action_card"] == "deck.action_cards_te"
    ghost = s.players["ghost"]
    assert ghost.command_tokens.tactic == 2 and ghost.trade_goods == 4
    assert [t.exhausted for t in ghost.techs] == [True, False]
    assert ghost.unit_upgrades == ["carrier_2"]
    assert ghost.strategy_cards[0].primary_used is True
    assert ghost.leaders.agent == "exhausted"
    assert ghost.private.action_cards == ["asyncti4:sabo1"]  # no KB loaded: kept, flagged
    # VP: corner (1) + secret (1); xxcha: corner (1) + custodians (1).
    assert s.players["xxcha"].vp == 2
    system = s.system("201")
    assert system.tile == "25" and system.command_tokens == ["ghost"]
    assert system.space["ghost"] == {"carrier": 1, "dreadnought": 2}  # "1,1,..." = 1 healthy + 1 damaged
    assert system.damaged["ghost"] == {"dreadnought": 1}
    planet = system.planets["quann"]
    assert planet.controller == "ghost" and planet.exhausted and planet.units["ghost"] == {"infantry": 2}
    assert planet.attachments == ["richworld"]
    assert [e.kind for e in s.log] == ["manual_command", "sc_played"]
    blob = s.model_dump_json()
    for private in (USER_A, USER_B, "Owner Name", "Player A Name", "secret plans"):
        assert private not in blob


def test_save_import_with_kb(kb):
    from warlord.kb.records import ComponentCard

    kb2 = type(kb)()
    kb2.add_many(kb.records.values())
    kb2.add(ComponentCard(id="pn.color_sftt", type="promissory_note", ruleset="base", name="Support for the Throne",
                          aliases=["<color>_sftt"], text="t", source_ref="asyncti4:promissory_note:<color>_sftt"))
    kb2.add(ComponentCard(id="ac.sabotage2", type="action_card", ruleset="base", name="Sabotage copy",
                          aliases=["sabo1"], text="t"))
    kb2.finalize()
    r = from_save(SAVE, [], kb=kb2, colors=COLORS)
    ghost = r.state.players["ghost"]
    assert ghost.promissory_in_play == ["pn.color_sftt"]
    assert ghost.vp == 1 + 1 + 1  # corner, secret, Support for the Throne
    assert "ac.sabotage2" in ghost.private.action_cards


WEB = {
    "versionSchema": 7, "gameName": "testgame2", "vpsToWin": 10, "gameRound": 2,
    "gameState": {"phase": "action", "activePlayer": "teal"},
    "strategyCards": [{"id": "te6warfare", "initiative": 6, "played": True}],
    "playerData": [
        {"faction": "ghost", "color": "teal", "discordId": USER_A, "userName": "Player A Name", "totalVps": 3,
         "tacticalCC": 2, "fleetCC": 3, "strategicCC": 2, "tg": 1, "commodities": 0, "commoditiesTotal": 3,
         "acCount": 4, "pnCount": 5, "soCount": 2, "secretsScored": {}, "scs": [6], "exhaustedSCs": [6],
         "techs": ["gd"], "exhaustedTechs": [], "unitsOwned": ["fighter2"], "isSpeaker": True, "active": True,
         "leaders": [{"type": "agent", "exhausted": False, "locked": False}]},
        {"faction": "neutral", "color": "aberration"},
    ],
    "objectives": {"allObjectives": [
        {"key": "Custodian/Imperial", "name": "Custodian/Imperial", "revealed": True, "scoredFactions": ["ghost"]}]},
    "tilePositions": ["000:18", "201:25"],
    "tileUnitData": {"201": {"ccs": ["ghost"], "space": {"ghost": [
        {"count": 2, "entityId": "dn", "entityType": "unit", "unitStates": [1, 1, 0, 0]}]},
        "planets": {"quann": {"controlledBy": "ghost", "exhausted": True, "entities": {"ghost": [
            {"count": 1, "entityId": "gf", "entityType": "unit", "unitStates": [1, 0, 0, 0]}]}}}}},
    "lawsInPlay": [],
}


def test_web_data_is_public_only():
    r = from_web_data(WEB)
    s = r.state
    assert set(s.players) == {"ghost"}  # neutral units are not a seat
    assert s.ruleset.thunders_edge
    ghost = s.players["ghost"]
    assert ghost.private.known is False and ghost.private.counts().action_cards == 4
    assert ghost.strategy_cards[0].exhausted and ghost.unit_upgrades == ["fighter_2"]
    assert s.system("201").damaged["ghost"] == {"dreadnought": 1}
    assert s.objectives.public_revealed == []  # custodians is a VP source, not an objective
    assert USER_A not in s.model_dump_json() and "Player A Name" not in s.model_dump_json()
    view = project(s, "ghost")
    assert view.hand.known is False and view.objectives.public_unrevealed is None

    set_hand(s, "ghost", action_cards=["ac.sabotage"])
    view = project(s, "ghost")
    assert view.hand.known and view.hand.action_cards == ["ac.sabotage"]
    assert view.players["ghost"].hidden.action_cards == 4  # others still see the public count


def _zip(games):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("pagination.json", json.dumps({"page": 0}))
        for g in games:
            z.writestr(f"{g}.txt", SAVE.replace("testgame1", g))
            z.writestr(f"{g}.json", json.dumps(EVENTS))
    return buf.getvalue()


def test_completed_fetch_is_capped(tmp_path):
    store = GameStore(tmp_path)
    urls = []

    def getter(url):
        urls.append(url)
        n = int(url.rsplit("limit=", 1)[1])
        return _zip([f"g{i:03d}" for i in range(len(urls) * 100, len(urls) * 100 + n)])

    first = fetch_completed(30, store, getter=getter)
    assert len(first) == 30 and urls[0].endswith("limit=30")
    second = fetch_completed(40, store, getter=getter)
    assert len(second) == MAX_EXPORT_GAMES - 30 and urls[1].endswith(f"limit={MAX_EXPORT_GAMES - 30}")
    with pytest.raises(CapReached):
        fetch_completed(1, store, getter=getter)
    assert len(store.list_games()) == MAX_EXPORT_GAMES
    assert USER_A not in (tmp_path / "g100.json").read_text()
