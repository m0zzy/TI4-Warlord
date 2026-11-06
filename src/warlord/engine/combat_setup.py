"""Build combat_sim inputs from the game, so the odds use the real units.

Given a system (and a planet, for ground combat), this reads the asking
player's view for:
* both sides' units, damaged units, unit upgrades, and faction units
  (flagships, mechs, special ships) from the knowledge base;
* in space: the defender's space cannon. That is PDS on their planets in
  the system, plus PDS with Deep Space Cannon (PDS II) in adjacent systems,
  at -1 if the attacker owns Antimass Deflectors;
* on the ground: the defender's ground forces and PDS on the planet, and
  the attacker's ships in the system, for bombardment.

``attacker_units`` replaces the attacker's units in the system, which is how
a planned attack is described: the ships (or ground forces) that would arrive.
For a planned invasion, ``bombard_units`` names the ships that would be there.
"""

from __future__ import annotations

from warlord.engine.combat import Side
from warlord.engine.geometry import system_adjacency
from warlord.engine.movement import GROUND, SHIPS, _tile_info
from warlord.engine.units import canonical, faction_unit_overrides


class SetupError(ValueError):
    pass


def _side(view, kb, player: str, units: dict, damaged: dict | None = None) -> Side:
    p = view.players[player]
    return Side(
        units={canonical(u): int(n) for u, n in units.items() if int(n) > 0},
        upgrades=list(p.unit_upgrades),
        damaged={canonical(u): int(n) for u, n in (damaged or {}).items()},
        overrides=faction_unit_overrides(kb, p.faction, p.unit_upgrades),
    )


def sides_from_game(
    view,
    kb,
    system: str,
    attacker: str | None = None,
    defender: str | None = None,
    attacker_units: dict | None = None,
    kind: str = "space",
    planet: str | None = None,
    bombard_units: dict | None = None,
) -> dict:
    s = view.system(system)
    if s is None:
        raise SetupError(f"There is no system at {system!r}.")
    attacker = attacker or view.turn.active_player or view.viewer
    if attacker not in view.players:
        raise SetupError(f"No seat {attacker!r}.")

    if kind == "ground":
        if planet not in s.planets:
            raise SetupError(f"{planet!r} is not a planet in {system}.")
        on_planet = s.planets[planet].units
        defender = defender or next((o for o, u in on_planet.items() if o != attacker and u), None)
        if defender is None:
            raise SetupError(f"No other player has units on {planet}.")
        att = attacker_units if attacker_units is not None else {
            u: n for u, n in s.space.get(attacker, {}).items() if canonical(u) in GROUND
        }
        defending = {u: n for u, n in on_planet.get(defender, {}).items() if canonical(u) in GROUND | {"pds"}}
        ships = bombard_units if bombard_units is not None else {
            u: n for u, n in s.space.get(attacker, {}).items() if canonical(u) in SHIPS
        }
        return {
            "kind": "ground",
            "attacker": _side(view, kb, attacker, att),
            "defender": _side(view, kb, defender, defending) if defender in view.players else Side(units=defending),
            "bombard": _side(view, kb, attacker, ships) if ships else None,
            "who": {"attacker": attacker, "defender": defender, "system": system, "planet": planet},
        }

    defender = defender or next(
        (o for o, u in s.space.items() if o != attacker and any(canonical(x) in SHIPS for x in u)), None
    )
    if defender is None:
        raise SetupError(f"No other player has ships in {system}.")
    att = attacker_units if attacker_units is not None else s.space.get(attacker, {})
    att_side = _side(view, kb, attacker, {u: n for u, n in att.items() if canonical(u) in SHIPS},
                     s.damaged.get(attacker) if attacker_units is None else None)
    if defender in view.players:
        def_side = _side(view, kb, defender, {u: n for u, n in s.space.get(defender, {}).items()
                                              if canonical(u) in SHIPS}, s.damaged.get(defender))
    else:  # neutral units (Thunder's Edge)
        def_side = Side(units={canonical(u): n for u, n in s.space.get(defender, {}).items() if canonical(u) in SHIPS})

    # The defender's space cannon: PDS on their planets here, and Deep Space Cannon PDS next door.
    cannon: dict[str, int] = {}
    for p in s.planets.values():
        for u, n in p.units.get(defender, {}).items():
            if canonical(u) == "pds":
                cannon["pds"] = cannon.get("pds", 0) + n
    if defender in view.players:
        upgrades = view.players[defender].unit_upgrades
        deep = any(canonical(x).startswith("pds") and x.endswith("2") for x in upgrades)
        if deep:
            adjacency = system_adjacency(view.map, _tile_info(kb))
            for pos in adjacency.get(system, ()):
                for p in view.system(pos).planets.values():
                    for u, n in p.units.get(defender, {}).items():
                        if canonical(u) == "pds":
                            cannon["pds"] = cannon.get("pds", 0) + n
    def_side.units.pop("pds", None)
    def_side.space_cannon_units = cannon
    antimass = any(t.id == "tech.antimass_deflectors" for t in view.players[attacker].techs)
    if antimass:
        def_side.space_cannon_modifier -= 1
    return {
        "kind": "space",
        "attacker": att_side,
        "defender": def_side,
        "who": {"attacker": attacker, "defender": defender, "system": system,
                "defender_space_cannon": cannon, "antimass_deflectors": antimass},
    }


def describe(side: Side | None) -> dict | None:
    if side is None:
        return None
    out = {"units": side.units}
    if side.damaged:
        out["damaged"] = side.damaged
    if side.upgrades:
        out["upgrades"] = side.upgrades
    if side.overrides:
        out["faction_units"] = sorted(side.overrides)
    if side.space_cannon_units:
        out["space_cannon"] = side.space_cannon_units
    return out

