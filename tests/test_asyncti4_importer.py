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
    assert [c.id for c in r.records] == ["ac.sabotage"]
    assert r.records[0].aliases == ["sabo1", "sabo2"]
    assert r.merged_copies == 1
    assert r.skipped_sources == {"asteroid": 1, "codex1": 1}


def test_codex_opt_in():
    r = import_rows("action_cards", ACTION_CARDS, include={"base", "pok", "codex"})
    assert {c.ruleset for c in r.records} == {"base", "codex"}


def test_units_and_placeholders(tmp_path):
    (tmp_path / "units").mkdir()
    (tmp_path / "units" / "baseUnits.json").write_text(json.dumps(UNITS))
    r = import_checkout(tmp_path)
    (card,) = r.records
    assert card.id == "unit.dreadnought"
    assert card.stats["combat"] == 5 and card.stats["sustain_damage"] is True
