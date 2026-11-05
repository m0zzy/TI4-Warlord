"""legal_actions: what a player can do now, as option families.

Tactical actions branch too far to list one by one, so options come grouped:

* ``tactical``: systems the player can activate, with the ship groups that can
  reach each one (from the movement engine) and whether they could produce
  or invade there;
* ``strategic``: strategy cards whose primary ability is unused;
* ``component``: exhaustible cards with an ACTION ability (technologies,
  relics, agents, action cards in the player's own hand);
* ``responses``: cards in hand whose timing fits the open window, if any;
* ``pass``: whether passing is allowed now, and why not.

Every option is a starting point: draft a concrete action from it, then run
it through validate_action before recommending it.
"""

from __future__ import annotations

import re

from warlord.engine.geometry import system_adjacency
from warlord.engine.invasion_production import _enemy_ships
from warlord.engine.movement import SHIPS, MovementContext, _tile_info, path_cost
from warlord.engine.units import canonical
from warlord.engine.validate import validate_action

_ACTION_TEXT = re.compile(r"(?:^|\n)\s*ACTION\s*:", re.IGNORECASE)
INITIATIVE = {
    "leadership": 1, "diplomacy": 2, "politics": 3, "construction": 4,
    "trade": 5, "warfare": 6, "technology": 7, "imperial": 8,
}


def initiative(card_id: str, kb=None) -> int | None:
    rec = kb.get(card_id) if kb is not None else None
    value = (getattr(rec, "stats", {}) or {}).get("initiative")
    if isinstance(value, int):
        return value
    name = card_id.split(".")[-1].lower()
    for key, n in INITIATIVE.items():
        if key in name:
            return n
    m = re.search(r"(\d+)", name)
    return int(m.group(1)) if m else None


def _ship_groups(view, player: str):
    """(position, ships) for each system where the player has ships that may move."""
    for s in view.map:
        ships = {u: n for u, n in s.space.get(player, {}).items() if canonical(u) in SHIPS and n > 0}
        if ships and player not in s.command_tokens:
            yield s.position, ships


def tactical_options(view, kb, player: str, limit: int = 25) -> list[dict]:
    me = view.players[player]
    if me.command_tokens.tactic < 1:
        return []
    mc = MovementContext.build(view, player, kb)
    adjacency = system_adjacency(view.map, _tile_info(kb))
    groups = list(_ship_groups(view, player))
    out = []
    for target in view.map:
        if player in target.command_tokens or target.hyperlane or target.position.startswith("frac"):
            continue
        reach = []
        for origin, ships in groups:
            if origin == target.position:
                continue
            found = path_cost(mc, origin, target.position, adjacency)
            if found is None:
                continue
            cost = found[0]
            movers = {}
            for u, n in ships.items():
                st = mc.stats(u)
                move = st.move if canonical(u) != "fighter" or st.independent else 0
                if canonical(u) == "fighter" and not st.independent:
                    continue  # carried, not moving on its own
                if move >= cost or (mc.gravity_drive and move + 1 >= cost and n == 1):
                    movers[u] = n
            if movers:
                reach.append({"from": origin, "ships": movers, "distance": cost})
        mine_there = any(canonical(u) in SHIPS for u in target.space.get(player, {}))
        produce = any(
            mc.stats(u).production is not None
            for holder in [target.space.get(player, {})] + [p.units.get(player, {}) for p in target.planets.values()]
            for u in holder
        )
        if not (reach or produce or mine_there):
            continue
        out.append({
            "system": target.position,
            "reachable_by": sorted(reach, key=lambda r: r["distance"]),
            "can_produce": produce,
            "other_players_ships": _enemy_ships(target, player),
            "planets": {
                name: (p.controller or "nobody") for name, p in target.planets.items()
            },
        })
    # Systems with something going on first: enemies, own planets, production.
    out.sort(key=lambda o: (not o["other_players_ships"], not o["can_produce"], -len(o["reachable_by"]), o["system"]))
    notes = [f"range may be extended by {', '.join(mc.enabling)}"] if mc.enabling else []
    if notes and out:
        out[0] = {**out[0], "note": notes[0]}
    return out[:limit]


def component_options(view, kb, player: str) -> list[dict]:
    me = view.players[player]
    cards: list[tuple[str, bool]] = [(t.id, t.exhausted) for t in me.techs]
    cards += [(r, False) for r in me.relics]
    if view.viewer == player:
        cards += [(a, False) for a in view.hand.action_cards]
    out = []
    for cid, exhausted in cards:
        rec = kb.get(cid) if kb is not None else None
        text = getattr(rec, "text", "") or ""
        timing = getattr(rec, "timing", None)
        is_action = bool(_ACTION_TEXT.search(text)) or (timing is not None and timing.window == "action")
        if is_action and not exhausted:
            out.append({"source": cid, "name": getattr(rec, "name", cid)})
    if me.leaders.agent == "ready":
        out.append({"source": f"leader.{me.faction}agent", "name": "agent (if its ability is an ACTION)"})
    return out


def response_options(view, kb, player: str) -> list[dict]:
    window = view.turn.open_window
    if window is None or (window.awaiting and player not in window.awaiting) or view.viewer != player:
        return []
    out = []
    for cid in view.hand.action_cards:
        rec = kb.get(cid) if kb is not None else None
        timing = getattr(rec, "timing", None)
        if timing is not None and timing.window == window.timing:
            out.append({"play": cid, "name": getattr(rec, "name", cid), "trigger": timing.trigger,
                        "note": "timing word matches; check the trigger against the event"})
    out.append({"play": None, "name": "decline"})
    return out


def legal_actions(view, kb, player: str | None = None) -> dict:
    player = player or view.viewer
    me = view.players[player]
    turn = view.turn
    if turn.phase != "action":
        return {"player": player, "phase": turn.phase, "note": "Actions are taken in the action phase."}
    pass_check = validate_action(view, {"type": "pass", "player": player}, kb=kb)
    result = {
        "player": player,
        "is_active": turn.active_player == player,
        "responses": response_options(view, kb, player),
        "pass": {"allowed": pass_check.legal is True, "why_not": [v.message for v in pass_check.violations]},
    }
    if turn.active_player != player or me.passed:
        result["note"] = "Not this player's turn: only responses to an open window apply."
        return result
    result["tactical"] = tactical_options(view, kb, player)
    result["strategic"] = [
        {"card": s.id, "initiative": initiative(s.id, kb)}
        for s in me.strategy_cards
        if not s.primary_used and not s.exhausted
    ]
    result["component"] = component_options(view, kb, player)
    result["next_step"] = "Draft a concrete action from one option and run it through validate_action."
    return result
