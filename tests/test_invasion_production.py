from warlord.engine.validate import validate_action
from warlord.kb.records import ComponentCard
from warlord.kb.store import KnowledgeBase
from warlord.state.perspective import project
from warlord.state.schema import (
    CommandTokens, GameState, Meta, OwnedTech, PlanetState, PlayerState, SystemState, TurnState,
)


def kb_with_planets(**resources):
    kb = KnowledgeBase()
    for name, res in resources.items():
        kb.add(ComponentCard(id=f"planet.{name}", type="planet", ruleset="base", name=name.title(), text="",
                             stats={"resources": res, "influence": 1}))
    kb.finalize()
    return kb


KB = kb_with_planets(home=4, arc=2, far=3, mecatol_rex=1)


def game(home_units=None, space=None, enemy_space=None, tg=0, techs=(), upgrades=(), extra_planets=None, home_exhausted=False):
    planets = {"home": PlanetState(controller="a", exhausted=home_exhausted, units={"a": home_units or {"space_dock": 1, "infantry": 1}})}
    planets.update(extra_planets or {})
    active = SystemState(position="101", tile="t", planets=planets, space={"a": space or {}, **({"b": enemy_space} if enemy_space else {})})
    return GameState(
        meta=Meta(game_id="ip", players=2),
        turn=TurnState(round=2, phase="action", active_player="a"),
        map=[SystemState(position="000", tile="t", planets={"far": PlanetState(controller="a")}),
             active, SystemState(position="201", tile="t")],
        players={
            "a": PlayerState(faction="hacan", trade_goods=tg, techs=[OwnedTech(id=t) for t in techs],
                             unit_upgrades=list(upgrades), command_tokens=CommandTokens(tactic=3, fleet=5, strategy=2)),
            "b": PlayerState(faction="sol"),
        },
    )


def run(state, **action):
    view = project(state, "a")
    return validate_action(view, {"type": "tactical", "player": "a", "activate": "101", **action}, kb=KB)


def test_production_within_limit_and_paid_is_verified():
    # Space dock on a 4-resource planet: production 6. Two carriers (6) + 2 fighters (1) cost 7,
    # paid by home (4) + far (3, another system is fine for paying) + 1 trade good.
    r = run(game(tg=1), produce=[{"where": "101", "units": {"carrier": 2, "fighter": 2}, "pay_with": ["home", "far", "tg:1"]}])
    assert r.legal is True and r.coverage == "engine"


def test_payment_rules():
    ok = run(game(tg=1), produce=[{"where": "101", "units": {"carrier": 1}, "pay_with": ["far"]}])
    assert ok.legal is True  # cost 3, far gives 3
    short = run(game(), produce=[{"where": "101", "units": {"carrier": 1}, "pay_with": ["arc"]}])
    assert short.legal is False and "does not control arc" in short.violations[0].message
    exhausted = run(game(home_exhausted=True), produce=[{"where": "101", "units": {"dreadnought": 1}, "pay_with": ["home"]}])
    assert exhausted.legal is False and "already exhausted" in exhausted.violations[0].message
    broke = run(game(home_exhausted=True), produce=[{"where": "101", "units": {"war_sun": 1}}])
    assert broke.legal is False
    afford = run(game(tg=2), produce=[{"where": "101", "units": {"dreadnought": 1, "infantry": 2}}])
    assert afford.legal is True  # no payment named: 4 + 3 + 2 TG covers 4 + 1


def test_pairs_and_sarween():
    one = run(game(), produce=[{"where": "101", "units": {"fighter": 1}, "pay_with": ["tg:1"]}])
    assert one.legal is False  # no trade goods
    paired = run(game(tg=1), produce=[{"where": "101", "units": {"fighter": 2}, "pay_with": ["tg:1"]}])
    assert paired.legal is True  # 2 fighters cost 1
    sarween = run(game(tg=2, techs=["tech.sarween_tools"]), produce=[{"where": "101", "units": {"cruiser": 1}, "pay_with": ["tg:1"]}])
    assert sarween.legal is True  # cruiser 2 - 1


def test_production_limit_and_structures():
    over = run(game(tg=20), produce=[{"where": "101", "units": {"fighter": 8}}])
    assert over.legal is False and "Production limit in 101 is 6" in over.violations[0].message
    dock2 = run(game(tg=20, upgrades=["space_dock_2"]), produce=[{"where": "101", "units": {"fighter": 8}}])
    assert dock2.legal is True  # 4 + 4 = 8
    structure = run(game(tg=5), produce=[{"where": "101", "units": {"pds": 1}}])
    assert structure.legal is False and "structure" in structure.violations[0].message
    none = run(game(home_units={"infantry": 2}, tg=5), produce=[{"where": "101", "units": {"infantry": 2}}])
    assert none.legal is False and "no unit with PRODUCTION" in none.violations[0].message
    elsewhere = run(game(tg=5), produce=[{"where": "201", "units": {"infantry": 2}}])
    assert elsewhere.legal is False


def test_war_sun_needs_its_technology():
    assert run(game(tg=20), produce=[{"where": "101", "units": {"war_sun": 1}}]).legal is False
    assert run(game(tg=20, upgrades=["war_sun"]), produce=[{"where": "101", "units": {"war_sun": 1}}]).legal is True


def test_enemy_ships_make_ship_production_depend_on_combat():
    r = run(game(tg=5, enemy_space={"destroyer": 1}), produce=[{"where": "101", "units": {"cruiser": 1}}])
    assert r.legal is None and "space combat" in r.unchecked[0]
    ground = run(game(tg=5, enemy_space={"destroyer": 1}), produce=[{"where": "101", "units": {"infantry": 2}}])
    assert ground.legal is True  # ground forces can still be produced


def test_invasion_commit():
    ok = run(game(space={"carrier": 1, "infantry": 2}), invade=[{"planet": "home", "infantry": 2}])
    assert ok.legal is True and "invasion_commit" in ok.checked  # dice decide the result, not the legality
    too_many = run(game(space={"carrier": 1, "infantry": 1}), invade=[{"planet": "home", "infantry": 2}])
    assert too_many.legal is False and "only 1 are in the space area" in too_many.violations[0].message
    wrong = run(game(space={"carrier": 1, "infantry": 1}), invade=[{"planet": "far", "infantry": 1}])
    assert wrong.legal is False and "not a planet in the active system" in wrong.violations[0].message
    moved = run(game(), moves=[{"from": "000", "units": {}}], invade=[{"planet": "home", "infantry": 1}])
    assert moved.legal is False  # the infantry on Home are on the planet, not in space


def test_invasion_with_enemy_ships_depends_on_combat():
    r = run(game(space={"carrier": 1, "infantry": 2}, enemy_space={"cruiser": 1}), invade=[{"planet": "home", "infantry": 2}])
    assert r.legal is None and "space combat" in r.unchecked[0]


def test_mecatol_needs_the_custodians_check():
    planets = {"mecatol_rex": PlanetState(controller=None)}
    r = run(game(space={"carrier": 1, "infantry": 1}, extra_planets=planets), invade=[{"planet": "mecatol_rex", "infantry": 1}])
    assert r.legal is None and any("custodians" in u for u in r.unchecked)


def test_reinforcement_warning():
    r = run(game(tg=20, space={"carrier": 4}), produce=[{"where": "101", "units": {"carrier": 1}}])
    assert r.legal is True and "Only 4 carrier exist" in r.warnings[0].message
