from warlord.engine.objectives import CHECKERS, objective_progress
from warlord.kb.records import ComponentCard
from warlord.kb.store import KnowledgeBase
from warlord.state.perspective import project
from warlord.state.schema import (
    CommandTokens, GameState, Meta, ObjectivesState, OwnedTech, PlanetState, PlayerState, ScoredObjective, SystemState, TurnState,
)


def kb():
    k = KnowledgeBase()
    planets = {"homea": (3, 2, []), "homeb": (2, 2, []), "p1": (1, 1, ["cultural"]), "p2": (2, 0, ["cultural"]),
               "p3": (0, 3, ["cultural"]), "p4": (3, 1, ["cultural"]), "mecatol_rex": (1, 6, [])}
    for name, (r, i, traits) in planets.items():
        k.add(ComponentCard(id=f"planet.{name}", type="planet", ruleset="base", name=name, text="",
                            stats={"resources": r, "influence": i, "traits": traits, **({"tech_specialties": ["biotic"]} if name == "p1" else {})}))
    k.add(ComponentCard(id="faction.alpha", type="faction", ruleset="base", name="Alpha", text="", faction="alpha", stats={"home_planets": ["homea"]}))
    k.add(ComponentCard(id="faction.beta", type="faction", ruleset="base", name="Beta", text="", faction="beta", stats={"home_planets": ["homeb"]}))
    for tid, kinds in {"tech.g1": ["BIOTIC"], "tech.g2": ["BIOTIC"], "tech.b1": ["PROPULSION"], "tech.b2": ["PROPULSION"],
                       "tech.cv2": ["UNITUPGRADE"], "tech.dn2": ["UNITUPGRADE"]}.items():
        k.add(ComponentCard(id=tid, type="technology", ruleset="base", name=tid, text="", stats={"tech_types": kinds}))
    for alias, oid in {"corner": "obj.corner_the_market", "amass_wealth": "obj.amass_wealth", "diversify": "obj.diversify_research",
                       "develop": "obj.develop_weaponry", "raise_fleet": "obj.raise_a_fleet", "intimidate": "obj.intimidate_council",
                       "conquer": "obj.conquer_the_weak", "expand_borders": "obj.expand_borders", "outer_rim": "obj.populate_the_outer_rim"}.items():
        k.add(ComponentCard(id=oid, type="objective", ruleset="base", name=oid, aliases=[alias], text="", stats={"points": 1}))
    k.finalize()
    return k


KB = kb()


def state(tg=3, techs=(), ships=None):
    a = "a"
    return GameState(
        meta=Meta(game_id="o", players=2),
        turn=TurnState(round=3, phase="status", active_player=a),
        map=[
            SystemState(position="000", tile="18", planets={"mecatol_rex": PlanetState()}),
            SystemState(position="101", tile="t", planets={"p1": PlanetState(controller=a), "p2": PlanetState(controller=a)},
                        space={a: ships or {"dreadnought": 2, "carrier": 3}}),
            SystemState(position="102", tile="t", planets={"p3": PlanetState(controller=a)}, space={a: {"cruiser": 1}}),
            SystemState(position="103", tile="t", planets={"p4": PlanetState(controller=a)}),
            SystemState(position="104", tile="t", planets={"homeb": PlanetState(controller=a)}),
            SystemState(position="201", tile="t", planets={"homea": PlanetState(controller=a)}),
        ],
        players={
            a: PlayerState(faction="alpha", trade_goods=tg, techs=[OwnedTech(id=t) for t in techs],
                           command_tokens=CommandTokens(tactic=2, fleet=3, strategy=1)),
            "b": PlayerState(faction="beta", scored=[ScoredObjective(id="obj.expand_borders")]),
        },
        objectives=ObjectivesState(public_revealed=["obj.corner_the_market", "obj.amass_wealth"]),
    )


def progress(s, *ids):
    return {e["id"]: e for e in objective_progress(project(s, "a"), KB, "a", list(ids))}


def test_all_forty_pok_objectives_have_checkers():
    assert len(CHECKERS) == 40


def test_control_and_traits():
    p = progress(state(), "obj.corner_the_market", "obj.conquer_the_weak", "obj.expand_borders")
    assert p["obj.corner_the_market"]["status"] == "can_score" and p["obj.corner_the_market"]["detail"].startswith("4 of 4")
    assert p["obj.conquer_the_weak"]["status"] == "can_score"  # a controls homeb, beta's home planet
    assert p["obj.expand_borders"]["detail"].startswith("4 of 6")  # homea and homeb are home systems


def test_spending_keeps_resources_and_influence_apart():
    # Ready: homea 3/2, homeb 2/2, p1 1/1, p2 2/0, p3 0/3, p4 3/1. Needs 3 res + 3 inf + 3 TG.
    assert progress(state(tg=3), "obj.amass_wealth")["obj.amass_wealth"]["status"] == "can_score"
    assert progress(state(tg=2), "obj.amass_wealth")["obj.amass_wealth"]["status"] == "not_yet"


def test_technology_and_fleets():
    s = state(techs=("tech.g1", "tech.g2", "tech.b1", "tech.b2", "tech.cv2"))
    p = progress(s, "obj.diversify_research", "obj.develop_weaponry", "obj.raise_a_fleet", "obj.intimidate_council")
    assert p["obj.diversify_research"]["status"] == "can_score"
    assert p["obj.develop_weaponry"]["detail"].startswith("1 of 2")
    assert p["obj.raise_a_fleet"]["status"] == "can_score"  # 5 non-fighter ships in 101
    assert p["obj.intimidate_council"]["status"] == "can_score"  # ships in 101 and 102, both next to Mecatol Rex


def test_scored_and_unknown():
    s = state()
    s.players["a"].scored = [ScoredObjective(id="obj.corner_the_market")]
    p = progress(s, "obj.corner_the_market", "obj.not_real")
    assert p["obj.corner_the_market"]["status"] == "scored" and p["obj.not_real"]["status"] == "unknown"
