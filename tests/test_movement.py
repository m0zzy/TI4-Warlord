"""Movement checks on small hand-built maps.

Positions (AsyncTI4 numbering): 000 is the center; 101 is north of it, 201
north of 101, 301 north of 201. 102 is northeast of 000. 202 touches both 101
and 201.
"""

from warlord.engine.validate import validate_action
from warlord.kb.records import ComponentCard
from warlord.kb.store import KnowledgeBase
from warlord.state.perspective import project
from warlord.state.schema import GameState, Meta, PlayerState, SystemState, TurnState


def game(systems, techs=(), upgrades=(), fleet=3, used=(), laws=()):
    from warlord.state.schema import CommandTokens, LawInPlay, OwnedTech

    return GameState(
        meta=Meta(game_id="m", players=2),
        turn=TurnState(round=1, phase="action", active_player="a", used_this_turn=list(used)),
        map=[SystemState(position=p, tile="t", **kw) for p, kw in systems.items()],
        players={
            "a": PlayerState(faction="hacan", techs=[OwnedTech(id=t) for t in techs], unit_upgrades=list(upgrades),
                             command_tokens=CommandTokens(tactic=3, fleet=fleet, strategy=2)),
            "b": PlayerState(faction="sol"),
        },
        laws=[LawInPlay(id=l) for l in laws],
    )


def check(state, moves, activate="301", kb=None):
    view = project(state, "a")
    return validate_action(view, {"type": "tactical", "player": "a", "activate": activate, "moves": moves}, kb=kb)


LINE = {"000": {}, "101": {}, "201": {}, "301": {}}  # a straight line north: 000 -> 101 -> 201 -> 301


def test_move_value_limits_range():
    s = game({**LINE, "000": {"space": {"a": {"carrier": 1, "cruiser": 1}}}})
    r = check(s, [{"from": "000", "units": {"cruiser": 1}}])  # cruiser I, move 2, needs 3
    assert r.legal is False and "needs 3 move and has 2" in r.violations[0].message
    s2 = game({**LINE, "000": {"space": {"a": {"cruiser": 1}}}}, upgrades=["cruiser_2"])
    assert check(s2, [{"from": "000", "units": {"cruiser": 1}}]).legal is True  # cruiser II, move 3


def test_gravity_drive_lifts_one_ship_once():
    s = game({**LINE, "101": {"space": {"a": {"carrier": 1}}}}, techs=["tech.gravity_drive"])
    assert check(s, [{"from": "101", "units": {"carrier": 1}}]).legal is True  # carrier I: 1 + 1
    two = game({**LINE, "101": {"space": {"a": {"carrier": 1, "dreadnought": 1}}}}, techs=["tech.gravity_drive"])
    r = check(two, [{"from": "101", "units": {"carrier": 1, "dreadnought": 1}}])
    assert r.legal is False and "Gravity Drive covers only one ship" in r.violations[0].message
    used = game({**LINE, "101": {"space": {"a": {"carrier": 1}}}}, techs=["tech.gravity_drive"], used=["a:tech.gravity_drive"])
    assert check(used, [{"from": "101", "units": {"carrier": 1}}]).legal is False


def test_other_players_ships_block_the_path():
    s = game({**LINE, "000": {"space": {"a": {"cruiser": 1}}}, "101": {"space": {"b": {"destroyer": 1}}}},
             upgrades=["cruiser_2"])
    r = check(s, [{"from": "000", "units": {"cruiser": 1}}])
    assert r.legal is False and "No legal path" in r.violations[0].message
    lwd = game({**LINE, "000": {"space": {"a": {"cruiser": 1}}}, "101": {"space": {"b": {"destroyer": 1}}}},
               upgrades=["cruiser_2"], techs=["tech.light_wave_deflector"])
    assert check(lwd, [{"from": "000", "units": {"cruiser": 1}}]).legal is True
    # Other players' ships in the active system itself do not block (that is a combat).
    target = game({**LINE, "201": {"space": {"a": {"cruiser": 1}}}, "301": {"space": {"b": {"fighter": 1}}}})
    assert check(target, [{"from": "201", "units": {"cruiser": 1}}]).legal is True


def test_anomalies():
    base = {"000": {}, "101": {}, "201": {"space": {"a": {"destroyer": 1}}}, "301": {}}
    asteroid = game({**base, "301": {"anomaly": "asteroid_field"}})
    assert check(asteroid, [{"from": "201", "units": {"destroyer": 1}}]).legal is False
    amd = game({**base, "301": {"anomaly": "asteroid_field"}}, techs=["tech.antimass_deflectors"])
    assert check(amd, [{"from": "201", "units": {"destroyer": 1}}]).legal is True
    supernova = game({**base, "301": {"anomaly": "supernova"}})
    assert check(supernova, [{"from": "201", "units": {"destroyer": 1}}]).legal is False
    into_nebula = game({**base, "301": {"anomaly": "nebula"}})
    assert check(into_nebula, [{"from": "201", "units": {"destroyer": 1}}]).legal is True  # nebula as the active system
    through_nebula = game({**LINE, "101": {"space": {"a": {"destroyer": 1}}}, "201": {"anomaly": "nebula"}})
    assert check(through_nebula, [{"from": "101", "units": {"destroyer": 1}}]).legal is False
    from_nebula = game({**LINE, "101": {"anomaly": "nebula", "space": {"a": {"destroyer": 1}}}})
    r = check(from_nebula, [{"from": "101", "units": {"destroyer": 1}}])
    assert r.legal is False and "has 1" in r.violations[0].message  # move 1 when starting in a nebula


def test_gravity_rift_extends_range_and_warns():
    s = game({**LINE, "101": {"space": {"a": {"carrier": 1}}}, "201": {"anomaly": "gravity_rift"}})
    r = check(s, [{"from": "101", "units": {"carrier": 1}}])  # carrier I: enter rift (1), leave it free
    assert r.legal is True and "gravity rift" in r.warnings[0].message


def test_wormholes_connect_far_systems():
    s = game({"000": {"space": {"a": {"carrier": 1}}, "wormholes": ["alpha"]}, "101": {}, "201": {}, "301": {"wormholes": ["alpha"]}})
    assert check(s, [{"from": "000", "units": {"carrier": 1}}]).legal is True


def test_transport_and_capacity():
    near = {"000": {}, "101": {}, "201": {"space": {"a": {"carrier": 1, "fighter": 6, "infantry": 4}}}, "301": {}}
    over = game(near)
    r = check(over, [{"from": "201", "units": {"carrier": 1, "fighter": 3, "infantry": 2}}])  # carrier I capacity 4
    assert r.legal is False and "can transport 4" in r.violations[0].message
    alone = check(game(near), [{"from": "201", "units": {"fighter": 2}}])
    assert alone.legal is False and "must be transported" in alone.violations[0].message
    f2 = game(near, upgrades=["fighter_2"])
    r = check(f2, [{"from": "201", "units": {"fighter": 2}}])  # Fighter II moves on its own
    assert r.legal is True and r.warnings == []  # 2 excess fighters count against a fleet pool of 3


def test_fleet_pool_and_excess_capacity_warn():
    s = game({**LINE, "201": {"space": {"a": {"cruiser": 3}}}, "301": {"space": {"a": {"destroyer": 1}}}}, fleet=3)
    r = check(s, [{"from": "201", "units": {"cruiser": 3}}])
    assert r.legal is True and any("fleet pool has 3" in w.message for w in r.warnings)


def test_structures_cannot_move():
    s = game({**LINE, "201": {"space": {"a": {"carrier": 1, "space_dock": 1}}}})
    assert check(s, [{"from": "201", "units": {"carrier": 1, "space_dock": 1}}]).legal is False


def test_unmodelled_cards_make_failures_unchecked():
    kb = KnowledgeBase()
    kb.add(ComponentCard(id="agenda.wormhole_reconstruction", type="agenda", ruleset="pok", name="Wormhole Reconstruction",
                         text="All systems that contain either an alpha or beta wormhole are adjacent to each other."))
    kb.finalize()
    s = game({**LINE, "000": {"space": {"a": {"cruiser": 1}}}}, laws=["agenda.wormhole_reconstruction"])
    r = check(s, [{"from": "000", "units": {"cruiser": 1}}], kb=kb)
    assert r.legal is None and any("wormhole_reconstruction" in u for u in r.unchecked)
    near = game({**LINE, "201": {"space": {"a": {"cruiser": 1}}}}, laws=["agenda.wormhole_reconstruction"])
    assert check(near, [{"from": "201", "units": {"cruiser": 1}}], kb=kb).legal is True  # adds adjacency: cannot forbid a reachable move


def test_off_grid_positions_are_unchecked():
    s = game({**LINE, "frac1": {"space": {"a": {"cruiser": 1}}}})
    r = check(s, [{"from": "frac1", "units": {"cruiser": 1}}])
    assert r.legal is None and "Fracture" in r.unchecked[0]


def test_restrictive_cards_block_verification_only():
    kb = KnowledgeBase()
    kb.add(ComponentCard(id="agenda.enforced_travel_ban", type="agenda", ruleset="base", name="Enforced Travel Ban",
                         text="For: Alpha and beta wormholes have no effect during movement."))
    kb.add(ComponentCard(id="tech.aerie_hololattice", type="technology", ruleset="pok", name="Aerie Hololattice",
                         faction="argent", text="Other players cannot move ships through systems that contain your structures."))
    kb.finalize()
    from warlord.state.schema import OwnedTech

    law = game({**LINE, "201": {"space": {"a": {"cruiser": 1}}}}, laws=["agenda.enforced_travel_ban"])
    r = check(law, [{"from": "201", "units": {"cruiser": 1}}], kb=kb)
    assert r.legal is None and "enforced_travel_ban" in r.unchecked[0]
    rival = game({**LINE, "201": {"space": {"a": {"cruiser": 1}}}})
    rival.players["b"].techs = [OwnedTech(id="tech.aerie_hololattice")]
    assert check(rival, [{"from": "201", "units": {"cruiser": 1}}], kb=kb).legal is None
    # A restrictive card cannot rescue a move the rules already refuse.
    far = game({**LINE, "000": {"space": {"a": {"carrier": 1}}}}, laws=["agenda.enforced_travel_ban"])
    assert check(far, [{"from": "000", "units": {"carrier": 1}}], kb=kb).legal is False


def test_off_grid_home_systems_connect_by_wormhole():
    s = game({**LINE, "tr": {"wormholes": ["delta"], "space": {"a": {"carrier": 1}}}, "301": {"wormholes": ["delta"]}})
    assert check(s, [{"from": "tr", "units": {"carrier": 1}}]).legal is True


def test_wormhole_tokens_are_imported():
    from warlord.state.importers.asyncti4 import wormhole_from_token

    assert [wormhole_from_token(t) for t in ("creussalpha", "token_gamma.png", "ingress", "frontier")] == [
        "alpha", "gamma", None, None]
