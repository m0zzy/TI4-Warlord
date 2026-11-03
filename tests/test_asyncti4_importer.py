import json

from warlord.kb.importers.asyncti4 import import_checkout, import_rows, parse_window

# Shaped like AsyncTI4's data files; text is placeholder.
ACTION_CARDS = [
    {"alias": "sabo1", "name": "Sabotage", "phase": "Any", "window": "When another player plays an action card other than 'Sabotage'", "text": "T", "source": "base"},
    {"alias": "sabo2", "name": "Sabotage", "phase": "Any", "window": "When another player plays an action card other than 'Sabotage'", "text": "T", "source": "base"},
    {"alias": "x1", "name": "Homebrew Thing", "phase": "Action", "window": "Action", "text": "T", "source": "asteroid"},
    {"alias": "cx", "name": "Codex Card", "phase": "Action", "window": "Action", "text": "T", "source": "codex1"},
]
UNITS = [
    {"id": "dreadnought", "baseType": "dreadnought", "asyncId": "dn", "name": "Dreadnought I", "source": "base", "combatHitsOn": 5, "combatDieCount": 1, "sustainDamage": True, "isShip": True},
    {"id": "nowarsun", "baseType": "warsun", "asyncId": "ws", "source": "base"},
]


def test_parse_window():
    t = parse_window("After you activate a system", "Action")
    assert (t.window, t.trigger, t.phase) == ("after", "you activate a system", "action")
    assert parse_window("At the start of a combat round", "Action").window == "start"
    assert parse_window("Action", "Action").window == "action"


def test_copies_merge_and_sources_filter():
    r = import_rows("action_cards", ACTION_CARDS)
    assert [c.id for c in r.records] == ["ac.sabotage", "ac.codex_card"]
    assert r.records[0].aliases == ["sabo1", "sabo2"]
    assert r.merged_copies == 1
    assert r.skipped_sources == {"asteroid": 1}


def test_include_narrows_sources():
    r = import_rows("action_cards", ACTION_CARDS, include={"base", "pok"})
    assert {c.ruleset for c in r.records} == {"base"}
    assert r.skipped_sources == {"asteroid": 1, "codex1": 1}


def test_units_and_placeholders(tmp_path):
    (tmp_path / "units").mkdir()
    (tmp_path / "units" / "baseUnits.json").write_text(json.dumps(UNITS))
    r = import_checkout(tmp_path)
    (card,) = r.records
    assert card.id == "unit.dreadnought"
    assert card.stats["combat"] == 5 and card.stats["sustain_damage"] is True


TE_ROWS = {
    "action_cards": [
        {"alias": "sabo1", "name": "Sabotage", "phase": "Any", "window": "When another player plays an action card", "text": "old", "source": "base"},
        {"alias": "sabo_te", "name": "Sabotage", "phase": "Any", "window": "When another player plays an action card", "text": "new", "source": "thunders_edge"},
    ],
    "breakthroughs": [
        {"alias": "arborecbt", "name": "Psychospore", "faction": "arborec", "synergy": ["BIOTIC", "WARFARE"], "text": "T", "source": "thunders_edge"},
    ],
    "leaders": [
        {"id": "bastionagent", "faction": "bastion", "type": "agent", "name": "Dame Briar", "title": "Bastion Valiant", "abilityWindow": "When a player's unit is destroyed", "abilityText": "T", "unlockCondition": "Always Unlocked", "source": "thunders_edge"},
    ],
    "strategy_cards": [
        {"id": "pok4construction", "initiative": 4, "name": "Construction", "primaryTexts": ["a"], "secondaryTexts": ["b"], "source": "pok"},
        {"id": "te4construction", "initiative": 4, "name": "Construction", "primaryTexts": ["c"], "secondaryTexts": ["d"], "source": "thunders_edge"},
    ],
    "decks": [
        {"alias": "action_cards_te", "name": "TE AC", "type": "action_card", "cardIDs": ["sabo_te", "missing1"], "source": "thunders_edge"},
        {"alias": "action_cards_pok", "name": "PoK AC", "type": "action_card", "cardIDs": ["sabo1"], "source": "pok"},
        {"alias": "objectives", "name": "Objectives", "type": "public_stage_1_objective", "cardIDs": ["x"], "source": "pok"},
    ],
    "strategy_card_sets": [
        {"alias": "te", "name": "TE SCs", "scIDs": ["te4construction"], "source": "thunders_edge"},
        {"alias": "pok", "name": "PoK SCs", "scIDs": ["pok4construction"], "source": "pok"},
    ],
}


def _checkout(tmp_path):
    for folder, rows in TE_ROWS.items():
        (tmp_path / folder).mkdir()
        (tmp_path / folder / "data.json").write_text(json.dumps(rows))
    return import_checkout(tmp_path)


def test_te_components_and_decks(tmp_path):
    r = _checkout(tmp_path)
    ids = {c.id for c in r.records}
    assert {"breakthrough.psychospore", "leader.bastionagent", "ac.sabotage", "ac.sabotage.thunders_edge"} <= ids
    leader = next(c for c in r.records if c.id == "leader.bastionagent")
    assert leader.timing.window == "when" and leader.faction == "bastion"
    decks = {d.id: d for d in r.decks}
    assert set(decks) == {"deck.action_cards_te", "deck.action_cards_pok", "deck.objectives", "deck.sc_set.te", "deck.sc_set.pok"}
    assert decks["deck.objectives"].unresolved == ["x"]
    assert decks["deck.action_cards_te"].cards == ["ac.sabotage.thunders_edge"]
    assert decks["deck.action_cards_te"].unresolved == ["missing1"]


def test_ruleset_decks_pick_the_right_printing(tmp_path):
    from warlord.kb.store import KnowledgeBase
    from warlord.ruleset import POK_RULESET, THUNDERS_EDGE_RULESET

    r = _checkout(tmp_path)
    kb = KnowledgeBase()
    kb.add_many([*r.records, *r.decks])
    kb.finalize()
    assert kb.card_lookup("Sabotage", ruleset=THUNDERS_EDGE_RULESET)["match"]["id"] == "ac.sabotage.thunders_edge"
    assert kb.card_lookup("Sabotage", ruleset=POK_RULESET)["match"]["id"] == "ac.sabotage"
    assert kb.card_lookup("Construction", ruleset=THUNDERS_EDGE_RULESET)["match"]["id"] == "sc.te4construction"
    assert kb.card_lookup("Construction", ruleset=POK_RULESET)["match"]["id"] == "sc.pok4construction"
    # Faction cards are not deck-filtered, but expansion tags still apply.
    assert kb.card_lookup("Psychospore", ruleset=THUNDERS_EDGE_RULESET)["match"]["id"] == "breakthrough.psychospore"
    pok = kb.card_lookup("Psychospore", ruleset=POK_RULESET)
    assert pok["match"] is None and pok["not_in_this_game"]["ruleset"] == "thunders_edge"


def _write(base, folder, name, rows):
    d = base / folder
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(json.dumps(rows))


def test_factions_tiles_planets_objectives(tmp_path):
    data = tmp_path / "data"
    _write(data, "factions", "base.json", [{
        "alias": "hacan", "factionName": "The Emirates of Hacan", "homeSystem": "16", "startingFleet": "2 cv",
        "commodities": 6, "factionTech": ["qdn"], "homePlanets": ["arretze"], "abilities": ["master_of_trade"],
        "leaders": ["hacanagent"], "promissoryNotes": ["convoys"], "units": ["carrier"], "source": "base"}])
    _write(data, "abilities", "base.json", [{"id": "master_of_trade", "name": "Masters of Trade", "faction": "hacan", "permanentEffect": "T", "source": "base"}])
    _write(data, "public_objectives", "po.json", [
        {"alias": "corner", "name": "Corner the Market", "phase": "Status", "text": "T", "points": 1, "source": "base"},
        {"alias": "found", "name": "Found a Golden Age", "phase": "Status", "text": "T", "points": 2, "source": "base"}])
    _write(data, "secret_objectives", "so.json", [{"alias": "ans", "name": "Adapt New Strategies", "phase": "Status", "text": "T", "points": 1, "source": "base"}])
    _write(data, "explores", "ex.json", [
        {"id": "frln1", "name": "Freelancers", "type": "Cultural", "resolution": "Instant", "text": "T", "source": "pok"},
        {"id": "frln2", "name": "Freelancers", "type": "Cultural", "resolution": "Instant", "text": "T", "source": "pok"}])
    _write(data, "units", "u.json", [
        {"id": "naalu_mech", "baseType": "mech", "asyncId": "mf", "name": "Iconoclast", "faction": "naalu", "source": "pok"},
        {"id": "naalu_mech_omega", "baseType": "mech", "asyncId": "mf", "name": "Iconoclast", "faction": "naalu", "source": "codex3"},
        {"id": "naalu_mech_te", "baseType": "mech", "asyncId": "mf", "name": "Iconoclast", "faction": "naalu", "source": "thunders_edge", "homebrewReplacesID": "naalu_mech_omega"},
        {"id": "absol_naalu_mech", "baseType": "mech", "asyncId": "mf", "name": "Absol Thing", "faction": "naalu", "source": "pok"}])
    _write(tmp_path, "systems", "25.json", {"id": "25", "name": "Quann", "aliases": ["25"], "wormholes": ["BETA"], "planets": ["quann"], "source": "base", "tileBack": "blue"})
    _write(tmp_path, "systems", "83a.json", {"id": "83a", "name": "Hyperlane", "aliases": ["83a"], "isHyperlane": True, "wormholes": [], "planets": [], "source": "pok"})
    _write(tmp_path, "systems", "41.json", {"id": "41", "name": "Gravity Rift", "aliases": ["41"], "isGravityRift": True, "source": "base"})
    _write(tmp_path, "planets", "quann.json", {"id": "quann", "tileId": "25", "name": "Quann", "aliases": ["quann"], "resources": 2, "influence": 1, "planetType": "CULTURAL", "source": "base"})
    row = ";".join(["0,0,0,0,0,0"] * 6)
    (data / "hyperlanes.properties").write_text(f"83a={row}\n83a60={row}\n")

    r = import_checkout(data)
    by_id = {c.id: c for c in r.records}
    assert by_id["faction.hacan"].stats["home_system"] == "16"
    assert by_id["ability.master_of_trade"].faction == "hacan"
    assert by_id["obj.found_a_golden_age"].stats["stage"] == 2
    assert "so.adapt_new_strategies" in by_id
    assert by_id["explore.freelancers"].aliases == ["frln1", "frln2"]
    assert by_id["tile.25"].stats == {"planets": ["planet.quann"], "wormholes": ["beta"], "tile_back": "blue"}
    assert by_id["tile.41"].stats["anomalies"] == ["gravity_rift"]
    assert set(by_id["tile.83a"].stats["lanes"]) == {"0", "60"}
    assert by_id["planet.quann"].stats == {"resources": 2, "influence": 1, "traits": ["cultural"], "tile": "25"}
    assert by_id["unit.naalu_mech_te"].supersedes == ["unit.naalu_mech_omega", "unit.naalu_mech"]
    assert "unit.absol_naalu_mech" not in by_id

    from warlord.kb.store import KnowledgeBase
    from warlord.ruleset import POK_RULESET, Ruleset, THUNDERS_EDGE_RULESET

    kb = KnowledgeBase()
    kb.add_many(r.records)
    kb.finalize()
    assert kb.card_lookup("Iconoclast", ruleset=THUNDERS_EDGE_RULESET)["match"]["id"] == "unit.naalu_mech_te"
    assert kb.card_lookup("Iconoclast", ruleset=POK_RULESET)["match"]["id"] == "unit.naalu_mech_omega"
    assert kb.card_lookup("Iconoclast", ruleset=Ruleset())["match"]["id"] == "unit.naalu_mech"  # no Codex
    assert kb.card_lookup("Quann", ruleset=POK_RULESET)["match"]["id"] == "planet.quann"
