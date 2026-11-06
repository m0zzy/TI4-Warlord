from warlord.engine.combat import Side, combat_sim, expected_hits_per_round, hit_points
from warlord.engine.units import stats_for


def test_unit_upgrades():
    assert stats_for("destroyer").afb == 9
    assert stats_for("destroyer", ["destroyer_2"]).afb == 6
    assert stats_for("dd", ["destroyer2"]).afb_dice == 3
    assert stats_for("carrier", ["carrier_2"]).combat == 9


def test_hit_points_and_expected_hits():
    side = Side.from_dict({"units": {"dreadnought": 2, "fighter": 3, "infantry": 4}, "damaged": {"dreadnought": 1}})
    assert hit_points(side) == 2 + 1 + 3  # ground forces are not in space combat
    assert abs(expected_hits_per_round(side) - (2 * 0.6 + 3 * 0.2)) < 1e-9


def test_deterministic_with_seed():
    a = {"units": {"cruiser": 2}}
    d = {"units": {"destroyer": 2}}
    assert combat_sim(a, d, seed=7, trials=500) == combat_sim(a, d, seed=7, trials=500)


def test_probabilities_sum_to_one():
    r = combat_sim({"units": {"carrier": 1, "fighter": 3}}, {"units": {"cruiser": 2}}, seed=1, trials=1000)
    assert abs(r["attacker_win"] + r["draw"] + r["defender_win"] - 1) < 1e-3


def test_overwhelming_force_wins():
    r = combat_sim({"units": {"war_sun": 2, "dreadnought": 3}}, {"units": {"fighter": 2}}, seed=3, trials=1000)
    assert r["attacker_win"] > 0.99


def test_no_defending_ships_is_attacker_win():
    r = combat_sim({"units": {"cruiser": 1}}, {"units": {"pds": 0}}, seed=1, trials=100, space_cannon=False)
    assert r["attacker_win"] == 1.0


def test_afb_only_hits_fighters():
    # Destroyer IIs' barrage clears fighters but cannot touch the dreadnought.
    r = combat_sim(
        {"units": {"destroyer": 4}, "upgrades": ["destroyer_2"]},
        {"units": {"fighter": 2, "dreadnought": 1}},
        seed=5, trials=2000,
    )
    assert r["expected_survivors"]["defender"].get("fighter", 0) < 0.3


def test_space_cannon_hurts_attacker():
    base = combat_sim({"units": {"cruiser": 2}}, {"units": {"cruiser": 2}}, seed=9, trials=3000, space_cannon=True)
    with_pds = combat_sim({"units": {"cruiser": 2}}, {"units": {"cruiser": 2, "pds": 2}}, seed=9, trials=3000)
    assert with_pds["attacker_win"] < base["attacker_win"]


def test_sustain_damage_absorbs_hits():
    healthy = combat_sim({"units": {"dreadnought": 1}}, {"units": {"cruiser": 1}}, seed=2, trials=3000)
    damaged = combat_sim({"units": {"dreadnought": 1}, "damaged": {"dreadnought": 1}}, {"units": {"cruiser": 1}}, seed=2, trials=3000)
    assert healthy["attacker_win"] > damaged["attacker_win"]


def test_ground_combat_basics():
    from warlord.engine.combat import ground_combat_sim

    strong = ground_combat_sim({"units": {"infantry": 4}}, {"units": {"infantry": 1}}, seed=1, trials=2000)
    assert strong["attacker_takes_planet"] > 0.95
    shield = ground_combat_sim({"units": {"infantry": 2}}, {"units": {"infantry": 2, "pds": 1}},
                               bombard={"units": {"dreadnought": 2}}, seed=1, trials=500)
    assert shield["bombardment"] == "blocked by Planetary Shield"
    sun = ground_combat_sim({"units": {"infantry": 2}}, {"units": {"infantry": 2, "pds": 1}},
                            bombard={"units": {"war_sun": 1}}, seed=1, trials=2000)
    assert sun["bombardment"].endswith("expected hits")
    no_pds = ground_combat_sim({"units": {"infantry": 2}}, {"units": {"infantry": 2, "pds": 1}}, seed=2, trials=3000)
    pds_off = ground_combat_sim({"units": {"infantry": 2}}, {"units": {"infantry": 2, "pds": 1}},
                                space_cannon_defense=False, seed=2, trials=3000)
    assert no_pds["attacker_takes_planet"] < pds_off["attacker_takes_planet"]  # space cannon defense hurts


def test_combat_from_game(kb, store):
    import json

    from warlord.tools.registry import ToolContext, run_tool

    ctx = ToolContext(kb=kb, store=store, game_id="pbd00000", viewer="p3")
    out, err = run_tool(ctx, "combat_sim", {"from_game": {"system": "201", "attacker_units": {"carrier": 1, "dreadnought": 1}},
                                            "trials": 500, "seed": 1})
    data = json.loads(out)
    assert not err and data["inputs"]["defender"]["units"] == {"destroyer": 1, "fighter": 1}
    assert data["inputs"]["defender_space_cannon"] == {"pds": 1}  # the PDS on Quann fires at the attackers
    assert data["inputs"]["attacker"]["upgrades"] == ["carrier_2"]
    out, err = run_tool(ctx, "combat_sim", {"from_game": {"system": "201", "kind": "ground", "planet": "quann",
                                                          "attacker_units": {"infantry": 3},
                                                          "bombard_units": {"dreadnought": 1}}, "trials": 500, "seed": 1})
    data = json.loads(out)
    assert not err and data["kind"] == "ground" and data["bombardment"] == "blocked by Planetary Shield"
    _, err = run_tool(ctx, "combat_sim", {"from_game": {"system": "102"}})
    assert err  # nobody to fight there
