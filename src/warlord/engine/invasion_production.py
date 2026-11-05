"""Invasion and production checks for a tactical action (LRR 49, 67, 68, 14, 26, 27).

Both steps come after space combat, so where the active system holds other
players' ships, what is allowed depends on the combat's result. The engine
says so as an unchecked aspect instead of guessing.

Invasion (49.2): committed ground forces must be in the active system's space
area once movement is done, and the planet must be in the active system.
Mecatol Rex needs the custodians token gone first (27.1); the state does not
track that token, so committing there stays unchecked.

Production (67, 68):
* only units with PRODUCTION in the active system can produce. A space dock's
  value is its planet's resources plus 2, or plus 4 for Space Dock II;
* the units produced cannot exceed the combined value, with each fighter and
  infantry counting (68.1b);
* the cost is paid from the player's own unexhausted planets and trade goods.
  Fighters and infantry cost per pair (67.2), and Sarween Tools takes 1 off;
* structures have no cost and cannot be produced (26.3);
* ships cannot be produced while other players' ships are present (67.6, 14.1);
* a War Sun needs its technology, and there is only one flagship.

Reinforcement limits are warnings: a player may take a unit back from a
system without their command token to produce it (67.5a). The same text scan
as for movement flags cards that change production or cost.
"""

from __future__ import annotations

import math
import re

from warlord.engine.movement import GROUND, SHIPS, MovementContext, MoveFinding
from warlord.engine.units import canonical

REINFORCEMENTS = {
    "carrier": 4,
    "cruiser": 8,
    "destroyer": 8,
    "dreadnought": 5,
    "war_sun": 2,
    "flagship": 1,
    "mech": 4,
    "pds": 6,
    "space_dock": 3,
}
PAIRED = {"fighter", "infantry"}
MECATOL = {"mr", "mrte", "mecatol_rex", "mecatolrex"}
MODELLED_PRODUCTION = {"tech.sarween_tools"}
_PRODUCTION_TEXT = re.compile(r"\bPRODUCTION\b|\bproduce|\bcost\b", re.IGNORECASE)


def _enemy_ships(system, player: str) -> bool:
    return any(
        owner != player and any(n > 0 and canonical(u) in SHIPS for u, n in units.items())
        for owner, units in system.space.items()
    )


def _arrived(system, action, player: str) -> dict[str, int]:
    """The player's units in the active system's space area once movement is done."""
    out = dict(system.space.get(player, {}))
    for mv in action.moves:
        for u, n in mv.units.items():
            out[u] = out.get(u, 0) + n
    return out


def check_invasion(view, action, kb, mc: MovementContext) -> list[MoveFinding]:
    findings: list[MoveFinding] = []
    system = view.system(action.activate)
    if system is None or not action.invade:
        return findings
    if _enemy_ships(system, action.player):
        findings.append(MoveFinding(
            "unchecked", "invasion",
            f"invasion depends on the space combat in {action.activate}: ground forces in space are lost if the "
            "active player loses it",
        ))
        return findings

    in_space = _arrived(system, action, action.player)
    committed: dict[str, int] = {"infantry": 0, "mech": 0}
    for inv in action.invade:
        if inv.planet not in system.planets:
            findings.append(MoveFinding(
                "violation", "invasion",
                f"{inv.planet} is not a planet in the active system {action.activate}.",
            ))
            continue
        if inv.planet.lower() in MECATOL:
            findings.append(MoveFinding(
                "unchecked", "invasion",
                "whether the custodians token is still on Mecatol Rex (it must be removed by spending 6 influence first)",
            ))
        committed["infantry"] += inv.infantry
        committed["mech"] += inv.mech
    for unit, n in committed.items():
        have = sum(c for u, c in in_space.items() if canonical(u) == unit)
        if n > have:
            findings.append(MoveFinding(
                "violation", "invasion",
                f"{n} {unit} committed, but only {have} are in the space area of {action.activate} after movement.",
            ))
    return findings


def _planet_resources(kb, name: str) -> int | None:
    rec = kb.get(f"planet.{name.lower()}") if kb is not None else None
    value = (getattr(rec, "stats", {}) or {}).get("resources")
    return int(value) if isinstance(value, (int, float)) else None


def _production_capacity(view, action, kb, mc: MovementContext) -> tuple[int, list[str]]:
    """Combined PRODUCTION in the active system, and anything that kept it from being exact."""
    system = view.system(action.activate)
    total, unknown = 0, []
    holders = [("space", system.space.get(action.player, {}))]
    holders += [
        (name, p.units.get(action.player, {}))
        for name, p in system.planets.items()
        if p.controller in (None, action.player)
    ]
    for where, units in holders:
        for u, n in units.items():
            st = mc.stats(u)
            if not n or st.production is None:
                continue
            value = st.production
            if st.production_plus_resources:
                res = _planet_resources(kb, where) if where != "space" else 0
                if res is None:
                    unknown.append(f"resources of {where}")
                    res = 0
                value += res
            total += value * n
    return total, unknown


def _production_cost(units: dict[str, int], mc: MovementContext, sarween: bool) -> float:
    cost = 0.0
    for u, n in units.items():
        base = canonical(u)
        each = mc.stats(u).cost
        if base in PAIRED:
            cost += math.ceil(n / 2) * each * 2
        else:
            cost += each * n
    if sarween and cost:
        cost = max(0.0, cost - 1)
    return cost


def check_production(view, action, kb, mc: MovementContext) -> list[MoveFinding]:
    findings: list[MoveFinding] = []
    system = view.system(action.activate)
    if system is None or not action.produce:
        return findings
    me = view.players[action.player]
    enabling = sorted(
        rid
        for rid in set(mc.enabling) | {t.id for t in me.techs} | set(me.relics)
        if rid not in MODELLED_PRODUCTION and kb is not None
        and _PRODUCTION_TEXT.search(getattr(kb.get(rid), "text", "") or "")
    )

    def refuse(msg: str) -> None:
        if enabling:
            findings.append(MoveFinding("unchecked", "production", f"{msg} (may be allowed by {', '.join(enabling)})"))
        else:
            findings.append(MoveFinding("violation", "production", msg))

    produced: dict[str, int] = {}
    for entry in action.produce:
        if entry.where != action.activate and entry.where not in system.planets:
            refuse(f"Units are produced in the active system ({action.activate}), not {entry.where}.")
        for u, n in entry.units.items():
            if n > 0:
                produced[canonical(u)] = produced.get(canonical(u), 0) + n

    for u in produced:
        st = mc.stats(u)
        if st.is_structure:
            findings.append(MoveFinding(
                "violation", "production",
                f"{u} is a structure: it has no cost and cannot be produced (structures are placed by Construction).",
            ))
        elif u not in SHIPS | GROUND:
            findings.append(MoveFinding("unchecked", "production", f"producing {u} (unit type not modelled)"))
    if produced.get("war_sun") and not _owns_war_sun(kb, me):
        refuse("War suns can only be produced after researching the War Sun technology.")
    on_board = _on_board(view, action.player)
    if produced.get("flagship") and (on_board.get("flagship", 0) + produced["flagship"] > 1):
        refuse("A player has only one flagship.")

    ships = {u: n for u, n in produced.items() if u in SHIPS}
    if ships and _enemy_ships(system, action.player):
        findings.append(MoveFinding(
            "unchecked", "production",
            f"producing ships depends on the space combat in {action.activate}: ships cannot be produced while "
            "other players' ships are there (ground forces still can)",
        ))

    capacity, unknown = _production_capacity(view, action, kb, mc)
    count = sum(produced.values())
    if capacity == 0 and not unknown:
        refuse(f"{action.player} has no unit with PRODUCTION in {action.activate}.")
    elif count > capacity and not unknown:
        refuse(f"Production limit in {action.activate} is {capacity}; {count} units were produced.")
    elif unknown:
        findings.append(MoveFinding("unchecked", "production", "production limit: " + ", ".join(unknown) + " not known"))

    sarween = any(t.id == "tech.sarween_tools" for t in me.techs)
    cost = _production_cost(produced, mc, sarween)
    findings += _payment(view, action, kb, cost, refuse)

    for u, n in produced.items():
        cap = REINFORCEMENTS.get(u)
        if cap is not None and on_board.get(u, 0) + n > cap:
            findings.append(MoveFinding(
                "warning", "production.reinforcements",
                f"Only {cap} {u} exist and {on_board.get(u, 0)} are on the board: to produce {n}, take one back "
                "from a system without your command token first (67.5).",
            ))
    return findings


def _payment(view, action, kb, cost: float, refuse) -> list[MoveFinding]:
    findings: list[MoveFinding] = []
    if cost <= 0:
        return findings
    me = view.players[action.player]
    controlled = {
        name: p for s in view.map for name, p in s.planets.items() if p.controller == action.player
    }
    pay = [p for e in action.produce for p in e.pay_with]
    if not pay:
        # No payment named: check the player can afford it at all.
        total, unknown = me.trade_goods, []
        for name, p in controlled.items():
            if p.exhausted:
                continue
            res = _planet_resources(kb, name)
            if res is None:
                unknown.append(name)
            else:
                total += res
        if unknown:
            findings.append(MoveFinding("unchecked", "production.cost", f"resources of {', '.join(unknown)}"))
        elif total < cost:
            refuse(f"The units cost {cost:g}, and {action.player} has only {total} resources and trade goods available.")
        return findings

    paid, tg = 0, 0
    for item in pay:
        if item.startswith("tg:"):
            tg += int(item.split(":", 1)[1] or 0)
            continue
        p = controlled.get(item)
        if p is None:
            refuse(f"{action.player} does not control {item}, so it cannot pay for production.")
            continue
        if p.exhausted:
            refuse(f"{item} is already exhausted.")
            continue
        res = _planet_resources(kb, item)
        if res is None:
            findings.append(MoveFinding("unchecked", "production.cost", f"resources of {item}"))
            return findings
        paid += res
    if tg > me.trade_goods:
        refuse(f"{action.player} has {me.trade_goods} trade goods, not {tg}.")
    if paid + tg < cost:
        refuse(f"The units cost {cost:g}; the named payment gives {paid + tg}.")
    return findings


def _owns_war_sun(kb, player) -> bool:
    """The War Sun unit upgrade, or a faction whose own units include a war sun (e.g. Muaat)."""
    if any(canonical(x).startswith("war_sun") or x.startswith("warsun") for x in player.unit_upgrades):
        return True
    if kb is None:
        return False
    rec = kb.get(f"faction.{player.faction}")
    for uid in (getattr(rec, "stats", {}) or {}).get("units") or []:
        base = (getattr(kb.get(f"unit.{uid}"), "stats", {}) or {}).get("base_type")
        if canonical(str(base or "")) == "war_sun" and uid != "nowarsun":
            return True
    return False


def _on_board(view, player: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for s in view.map:
        for units in [s.space.get(player, {})] + [p.units.get(player, {}) for p in s.planets.values()]:
            for u, n in units.items():
                counts[canonical(u)] = counts.get(canonical(u), 0) + n
    return counts
