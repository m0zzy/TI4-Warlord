"""Unit statistics for combat math.

Defaults are the base-game and PoK generic units (I and II), matching the
AsyncTI4 ``units/baseUnits.json`` data. Faction units and flagships differ, so
callers pass overrides (or load them from the knowledge base) for those.
"""

from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class UnitStats:
    name: str
    combat: int | None = None  # hits on this value or higher
    combat_dice: int = 1
    afb: int | None = None
    afb_dice: int = 0
    space_cannon: int | None = None
    space_cannon_dice: int = 0
    bombard: int | None = None
    bombard_dice: int = 0
    sustain: bool = False
    cost: float = 0.0
    is_ship: bool = False
    is_ground: bool = False

    def with_(self, **kw) -> "UnitStats":
        return replace(self, **kw)


_I = {
    "fighter": UnitStats("fighter", combat=9, cost=0.5, is_ship=True),
    "destroyer": UnitStats("destroyer", combat=9, afb=9, afb_dice=2, cost=1, is_ship=True),
    "cruiser": UnitStats("cruiser", combat=7, cost=2, is_ship=True),
    "carrier": UnitStats("carrier", combat=9, cost=3, is_ship=True),
    "dreadnought": UnitStats("dreadnought", combat=5, bombard=5, bombard_dice=1, sustain=True, cost=4, is_ship=True),
    "war_sun": UnitStats("war_sun", combat=3, combat_dice=3, bombard=3, bombard_dice=3, sustain=True, cost=12, is_ship=True),
    "flagship": UnitStats("flagship", combat=7, combat_dice=2, sustain=True, cost=8, is_ship=True),
    "infantry": UnitStats("infantry", combat=8, cost=0.5, is_ground=True),
    "mech": UnitStats("mech", combat=6, sustain=True, cost=2, is_ground=True),
    "pds": UnitStats("pds", space_cannon=6, space_cannon_dice=1),
    "space_dock": UnitStats("space_dock"),
}

_II = {
    "fighter": _I["fighter"].with_(combat=8),
    "destroyer": _I["destroyer"].with_(combat=8, afb=6, afb_dice=3),
    "cruiser": _I["cruiser"].with_(combat=6),
    "carrier": _I["carrier"],
    "dreadnought": _I["dreadnought"],
    "infantry": _I["infantry"].with_(combat=7),
    "pds": _I["pds"].with_(space_cannon=5),
}

ALIASES = {"warsun": "war_sun", "dread": "dreadnought", "dn": "dreadnought", "dd": "destroyer",
           "ca": "cruiser", "cv": "carrier", "ff": "fighter", "gf": "infantry", "inf": "infantry",
           "fs": "flagship", "ws": "war_sun", "mf": "mech", "pd": "pds", "spacedock": "space_dock"}

# Order in which a player gives up units to hits, cheapest first.
LOSS_ORDER = ["fighter", "infantry", "destroyer", "cruiser", "carrier", "mech", "dreadnought", "flagship", "war_sun"]


def canonical(unit: str) -> str:
    u = unit.lower().strip().replace(" ", "_")
    return ALIASES.get(u, u)


def stats_for(
    unit: str,
    upgrades: list[str] | None = None,
    overrides: dict[str, dict] | None = None,
) -> UnitStats:
    """Stats for ``unit`` given a player's ``unit_upgrades`` (e.g. ["carrier_2"])."""
    u = canonical(unit)
    upgraded = any(_upgrade_target(x) == u for x in (upgrades or []))
    base = (_II if upgraded else _I).get(u) or _I.get(u)
    if base is None:
        base = UnitStats(u)
    if overrides and u in overrides:
        base = base.with_(**overrides[u])
    return base


def _upgrade_target(upgrade: str) -> str | None:
    """Map an upgrade id such as carrier_2, carrier2 or carrier_ii to its unit."""
    x = upgrade.lower().strip()
    for suffix in ("_ii", "_2", "2"):
        if x.endswith(suffix):
            return canonical(x[: -len(suffix)])
    return None
