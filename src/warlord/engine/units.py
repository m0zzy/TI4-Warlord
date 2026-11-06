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
    move: int = 0
    capacity: int = 0
    independent: bool = False  # a fighter that may move without being transported (Fighter II)
    is_structure: bool = False
    production: int | None = None  # PRODUCTION value
    production_plus_resources: bool = False  # space docks: value + the planet's resources
    fighter_capacity: int = 0  # capacity for fighters only (space docks: 3)

    def with_(self, **kw) -> "UnitStats":
        return replace(self, **kw)


_I = {
    "fighter": UnitStats("fighter", combat=9, cost=0.5, is_ship=True),
    "destroyer": UnitStats("destroyer", combat=9, afb=9, afb_dice=2, cost=1, is_ship=True, move=2),
    "cruiser": UnitStats("cruiser", combat=7, cost=2, is_ship=True, move=2),
    "carrier": UnitStats("carrier", combat=9, cost=3, is_ship=True, move=1, capacity=4),
    "dreadnought": UnitStats(
        "dreadnought", combat=5, bombard=5, bombard_dice=1, sustain=True, cost=4, is_ship=True, move=1, capacity=1
    ),
    "war_sun": UnitStats(
        "war_sun", combat=3, combat_dice=3, bombard=3, bombard_dice=3, sustain=True, cost=12, is_ship=True,
        move=2, capacity=6,
    ),
    # Generic stand-in; real flagships differ by faction (see faction_unit_overrides).
    "flagship": UnitStats("flagship", combat=7, combat_dice=2, sustain=True, cost=8, is_ship=True, move=1, capacity=3),
    "infantry": UnitStats("infantry", combat=8, cost=0.5, is_ground=True),
    "mech": UnitStats("mech", combat=6, sustain=True, cost=2, is_ground=True),
    "pds": UnitStats("pds", space_cannon=6, space_cannon_dice=1, is_structure=True),
    "space_dock": UnitStats("space_dock", is_structure=True, production=2, production_plus_resources=True, fighter_capacity=3),
}

_II = {
    "fighter": _I["fighter"].with_(combat=8, move=2, independent=True),
    "destroyer": _I["destroyer"].with_(combat=8, afb=6, afb_dice=3),
    "cruiser": _I["cruiser"].with_(combat=6, move=3, capacity=1),
    "carrier": _I["carrier"].with_(move=2, capacity=6),
    "dreadnought": _I["dreadnought"].with_(move=2),
    "infantry": _I["infantry"].with_(combat=7),
    "pds": _I["pds"].with_(space_cannon=5),
    "space_dock": _I["space_dock"].with_(production=4),
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
    upgraded = any((t := _upgrade_target(x)) == u or (t or "").endswith("_" + u) for x in (upgrades or []))
    base = (_II if upgraded else _I).get(u) or _I.get(u)
    if base is None:
        base = UnitStats(u)
    if overrides and u in overrides:
        base = base.with_(**overrides[u])
    return base


_KB_STAT_FIELDS = {
    "combat": "combat",
    "combat_dice": "combat_dice",
    "afb": "afb",
    "afb_dice": "afb_dice",
    "bombard": "bombard",
    "bombard_dice": "bombard_dice",
    "space_cannon": "space_cannon",
    "space_cannon_dice": "space_cannon_dice",
    "sustain_damage": "sustain",
    "cost": "cost",
    "move": "move",
    "capacity": "capacity",
}


def faction_unit_overrides(kb, faction: str | None, upgrades: list[str] | None = None) -> dict[str, dict]:
    """Stat overrides for a faction's own units (flagship, mech, special ships),
    from the knowledge base's faction and unit records."""
    if kb is None or not faction:
        return {}
    rec = kb.get(f"faction.{faction}")
    unit_ids = (getattr(rec, "stats", {}) or {}).get("units") or []
    out: dict[str, dict] = {}
    for uid in unit_ids:
        unit = kb.get(f"unit.{uid}")
        stats = getattr(unit, "stats", None) or {}
        base = canonical(str(stats.get("base_type") or ""))
        if not base or base not in _I:
            continue
        if any(_upgrade_target(x) in (base, canonical(uid)) for x in upgrades or []) and stats.get("upgrades_to"):
            upgraded = kb.get(f"unit.{stats['upgrades_to']}")
            stats = getattr(upgraded, "stats", None) or stats
        fields = {ours: stats[theirs] for theirs, ours in _KB_STAT_FIELDS.items() if theirs in stats}
        if "production" in stats:
            value = str(stats["production"]).strip()
            if value.startswith("+") and value[1:].isdigit():
                fields.update(production=int(value[1:]), production_plus_resources=True)
            elif value.isdigit():
                fields.update(production=int(value), production_plus_resources=False)
        if base == "fighter" and fields.get("move"):
            fields["independent"] = True
        if fields:
            out[base] = fields
    return out


def _upgrade_target(upgrade: str) -> str | None:
    """Map an upgrade id such as carrier_2, carrier2 or carrier_ii to its unit."""
    x = upgrade.lower().strip()
    for suffix in ("_ii", "_2", "2"):
        if x.endswith(suffix):
            return canonical(x[: -len(suffix)])
    return None
