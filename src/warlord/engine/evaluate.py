"""evaluate_position: a heuristic picture of one player's position. Not a verdict.

From the asking player's view only:
* the VP race: every player's points against the target, and who has
  scored which public objectives;
* spending power: resources and influence on unexhausted planets, trade
  goods, commodities and command tokens;
* threats: other players' ship groups that could reach a system holding
  the player's planets or ships on their next tactical action, with
  expected hits per round. This ignores their command tokens, which return
  at the end of the round.

It does not judge objective progress (each objective's requirement would
need modelling), score positions, or recommend moves: the agent weighs
these numbers.
"""

from __future__ import annotations

from warlord.engine.combat import Side, expected_hits_per_round
from warlord.engine.geometry import system_adjacency
from warlord.engine.movement import SHIPS, MovementContext, _tile_info, path_cost
from warlord.engine.units import canonical, faction_unit_overrides


def _planet_value(kb, name: str, key: str) -> int:
    rec = kb.get(f"planet.{name.lower()}") if kb is not None else None
    value = (getattr(rec, "stats", {}) or {}).get(key)
    return int(value) if isinstance(value, (int, float)) else 0


def evaluate_position(view, kb, player: str | None = None) -> dict:
    player = player or view.viewer
    me = view.players[player]

    race = sorted(
        ({"player": pid, "faction": p.faction, "vp": p.vp, "to_win": max(0, view.meta.vp_target - p.vp)}
         for pid, p in view.players.items()),
        key=lambda r: -r["vp"],
    )

    planets = [(s.position, name, p) for s in view.map for name, p in s.planets.items() if p.controller == player]
    ready = [(pos, name) for pos, name, p in planets if not p.exhausted]
    spend = {
        "resources": sum(_planet_value(kb, n, "resources") for _, n in ready),
        "influence": sum(_planet_value(kb, n, "influence") for _, n in ready),
        "trade_goods": me.trade_goods,
        "commodities": me.commodities.current,
        "command_tokens": me.command_tokens.model_dump(),
        "planets_ready": len(ready),
        "planets_total": len(planets),
    }
    if kb is None or not any(kb.get(f"planet.{n.lower()}") for _, n in ready):
        spend["note"] = "planet values unknown without the knowledge base"

    threats = _threats(view, kb, player)
    revealed = {o: [pid for pid, p in view.players.items() if o in p.scored] for o in view.objectives.public_revealed}
    return {
        "player": player,
        "round": view.turn.round,
        "vp_target": view.meta.vp_target,
        "vp_race": race,
        "public_objectives": [
            {"id": o, "scored_by": who, "scored_by_you": player in who} for o, who in revealed.items()
        ],
        "secret_objectives_in_hand": len(view.hand.secret_objectives) if view.viewer == player else None,
        "spending_power": spend,
        "threats": threats,
        "not_assessed": ["objective progress", "agenda and law effects", "action cards in opponents' hands"],
    }


def _threats(view, kb, player: str, limit: int = 10) -> list[dict]:
    mine = {
        s.position for s in view.map
        if any(p.controller == player for p in s.planets.values()) or s.space.get(player)
    }
    adjacency = system_adjacency(view.map, _tile_info(kb))
    out = []
    for pid, p in view.players.items():
        if pid == player:
            continue
        mc = MovementContext.build(view, pid, kb)
        mc.gravity_drive = any(t.id == "tech.gravity_drive" for t in p.techs)  # next turn: unused again
        overrides = faction_unit_overrides(kb, p.faction, p.unit_upgrades)
        for s in view.map:
            ships = {u: n for u, n in s.space.get(pid, {}).items() if canonical(u) in SHIPS and n > 0}
            if not ships:
                continue
            for target in mine:
                if target == s.position:
                    continue
                found = path_cost(mc, s.position, target, adjacency)
                if found is None:
                    continue
                movers = {u: n for u, n in ships.items()
                          if canonical(u) != "fighter" and mc.stats(u).move + (1 if mc.gravity_drive else 0) >= found[0]}
                if not movers:
                    continue
                side = Side(units=movers, upgrades=p.unit_upgrades, overrides=overrides)
                out.append({
                    "by": pid, "from": s.position, "to": target, "ships": movers,
                    "expected_hits_per_round": round(expected_hits_per_round(side), 2),
                })
    out.sort(key=lambda t: -t["expected_hits_per_round"])
    return out[:limit]
