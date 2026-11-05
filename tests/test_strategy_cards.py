from warlord.engine.strategy_cards import card_kind
from warlord.engine.validate import validate_action
from warlord.kb.records import ComponentCard
from warlord.kb.store import KnowledgeBase
from warlord.state.perspective import project
from warlord.state.schema import (
    CommandTokens, GameState, HeldStrategyCard, Meta, OwnedTech, PlanetState, PlayerState, SystemState, TurnState,
)


def _kb():
    kb = KnowledgeBase()
    cards = [
        ("planet.home", "planet", {"resources": 3, "influence": 4}),
        ("planet.ridge", "planet", {"resources": 4, "influence": 2, "tech_specialties": ["propulsion"]}),
        ("planet.mecatol_rex", "planet", {"resources": 1, "influence": 6}),
        ("tech.gravity_drive", "technology", {"tech_types": ["PROPULSION"], "requirements": "B"}),
        ("tech.antimass_deflectors", "technology", {"tech_types": ["PROPULSION"]}),
        ("tech.fleet_logistics", "technology", {"tech_types": ["PROPULSION"], "requirements": "BB"}),
        ("tech.sarween_tools", "technology", {"tech_types": ["CYBERNETIC"]}),
    ]
    for cid, kind, stats in cards:
        kb.add(ComponentCard(id=cid, type=kind, ruleset="base", name=cid.split(".")[1].replace("_", " ").title(), text="", stats=stats))
    kb.add(ComponentCard(id="tech.quantum_datahub_node", type="technology", ruleset="base", name="Quantum Datahub Node",
                         text="", faction="hacan", stats={"tech_types": ["CYBERNETIC"], "requirements": "YYY"}))
    kb.finalize()
    return kb


KB = _kb()


def game(card, techs=("tech.antimass_deflectors",), tg=0, exhausted=(), speaker="b"):
    def planet(name, **kw):
        return PlanetState(controller="a", exhausted=name in exhausted, **kw)

    return GameState(
        meta=Meta(game_id="sc", players=2),
        turn=TurnState(round=2, phase="action", active_player="a", speaker=speaker),
        map=[
            SystemState(position="000", tile="18", planets={"mecatol_rex": planet("mecatol_rex")}),
            SystemState(position="101", tile="t", command_tokens=["a"], space={"a": {"cruiser": 1}},
                        planets={"home": planet("home", units={"a": {"space_dock": 1, "pds": 1}})}),
            SystemState(position="201", tile="t", planets={"ridge": planet("ridge")}),
            SystemState(position="301", tile="t", space={"a": {"cruiser": 1}}),
        ],
        players={
            "a": PlayerState(faction="hacan", trade_goods=tg, techs=[OwnedTech(id=t) for t in techs],
                             strategy_cards=[HeldStrategyCard(id=card)], command_tokens=CommandTokens(tactic=0, fleet=3, strategy=2)),
            "b": PlayerState(faction="sol"),
        },
    )


def run(state, card, primary):
    return validate_action(project(state, "a"), {"type": "strategic", "player": "a", "card": card, "primary": primary}, kb=KB)


def test_card_kind():
    assert card_kind("sc.te6warfare") == ("warfare", True) and card_kind("sc.pok6warfare") == ("warfare", False)
    assert card_kind("trade") == ("trade", False)


def test_leadership():
    s = game("sc.pok1leadership", tg=2)
    # 4 influence (home) + 2 trade goods = 6: 3 + 2 tokens.
    assert run(s, "sc.pok1leadership", {"influence_from": ["home"], "trade_goods": 2, "tokens": {"tactic": 3, "fleet": 2}}).legal is True
    wrong = run(s, "sc.pok1leadership", {"influence_from": ["home"], "tokens": {"tactic": 5}})
    assert wrong.legal is False and "gives 4 command tokens" in wrong.violations[0].message


def test_diplomacy():
    s = game("sc.pok2diplomacy", exhausted=("ridge",))
    assert run(s, "sc.pok2diplomacy", {"system": "101", "ready": ["ridge"]}).legal is True
    assert run(s, "sc.pok2diplomacy", {"system": "000"}).legal is False  # Mecatol Rex system
    assert run(s, "sc.pok2diplomacy", {"system": "301"}).legal is False  # no planet there
    assert run(s, "sc.pok2diplomacy", {"system": "101", "ready": ["home"]}).legal is False  # home is not exhausted


def test_politics_and_trade():
    s = game("sc.pok3politics")
    assert run(s, "sc.pok3politics", {"speaker": "a"}).legal is True
    assert run(s, "sc.pok3politics", {"speaker": "b"}).legal is False  # already the speaker
    t = game("sc.pok5trade")
    assert run(t, "sc.pok5trade", {"chosen_players": ["b"]}).legal is True
    assert run(t, "sc.pok5trade", {"chosen_players": ["a"]}).legal is False


def test_construction():
    s = game("sc.pok4construction")
    assert run(s, "sc.pok4construction", {"place": [{"structure": "space_dock", "planet": "ridge"}, {"structure": "pds", "planet": "home"}]}).legal is True
    assert run(s, "sc.pok4construction", {"place": [{"structure": "space_dock", "planet": "home"}]}).legal is False  # one dock per planet
    two = run(s, "sc.pok4construction", {"place": [{"structure": "pds", "planet": "home"}, {"structure": "pds", "planet": "home"}]})
    assert two.legal is False and "at most 2 pds" in two.violations[0].message
    assert run(s, "sc.pok4construction", {"place": [{"structure": "pds", "planet": "ridge"}, {"structure": "space_dock", "planet": "mecatol_rex"}]}).legal is False
    te = game("sc.te4construction")
    assert run(te, "sc.te4construction", {"place": [{"structure": "pds", "planet": "ridge"}, {"structure": "space_dock", "planet": "ridge"}]}).legal is True


def test_warfare_pok_and_te():
    s = game("sc.pok6warfare")
    assert run(s, "sc.pok6warfare", {"remove_token_from": "101", "to_pool": "tactic"}).legal is True
    assert run(s, "sc.pok6warfare", {"remove_token_from": "201"}).legal is False
    te = game("sc.te6warfare")
    # A tactical action into 101, which already has a's token, with no tactic tokens: allowed by TE Warfare.
    # The cruiser (move 2) goes 301 -> 201 -> 101.
    ok = run(te, "sc.te6warfare", {"tactical": {"activate": "101", "moves": [{"from": "301", "units": {"cruiser": 1}}]}})
    assert ok.legal is True, ok
    far = run(te, "sc.te6warfare", {"tactical": {"activate": "000", "moves": [{"from": "301", "units": {"cruiser": 1}}]}})
    assert far.legal is False and far.violations[0].message.startswith("Warfare's tactical action")


def test_technology_prerequisites_specialties_and_payment():
    s = game("sc.pok7technology", tg=3)
    assert run(s, "sc.pok7technology", {"research": ["tech.gravity_drive"]}).legal is True  # needs B: has Antimass
    assert run(s, "sc.pok7technology", {"research": ["tech.fleet_logistics"]}).legal is False  # needs BB
    spec = run(s, "sc.pok7technology", {"research": ["tech.fleet_logistics"], "specialties": ["ridge"]})
    assert spec.legal is True  # Ridge's propulsion specialty covers the second B
    two = run(s, "sc.pok7technology", {"research": ["tech.gravity_drive", "tech.fleet_logistics"], "pay_with": ["home", "tg:3"]})
    assert two.legal is True  # Gravity Drive counts toward Fleet Logistics; 3 + 3 = 6 resources
    poor = run(s, "sc.pok7technology", {"research": ["tech.gravity_drive", "tech.sarween_tools"], "pay_with": ["home"]})
    assert poor.legal is False and "costs 6 resources" in poor.violations[0].message
    assert run(s, "sc.pok7technology", {"research": ["tech.antimass_deflectors"]}).legal is False  # already owned
    other = game("sc.pok7technology")
    other.players["a"].faction = "sol"
    assert run(other, "sc.pok7technology", {"research": ["tech.quantum_datahub_node"]}).legal is False  # Hacan only


def test_imperial():
    s = game("sc.pok8imperial")
    s.objectives.public_revealed = ["obj.corner"]
    r = run(s, "sc.pok8imperial", {"score": "obj.corner"})
    assert r.legal is None and "requirements" in r.unchecked[0]
    assert "controls Mecatol Rex: +1 VP" in r.warnings[0].message
    assert run(s, "sc.pok8imperial", {"score": "obj.other"}).legal is False
