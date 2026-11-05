"""apply_action: what-if. Apply an action to a copy of the game state.

Only deterministic parts are applied:
* activation (token placed, tactic token spent), movement, invasion
  commits and control changes, production with its payment;
* exhausting a played strategy card or technology, discarding a played
  action card, passing, and the turn moving on in initiative order.

Dice and choices are not invented. A space combat, ground combat, space
cannon fire, or a gravity rift roll stops the what-if at that point and is
listed under ``pending``, with a pointer to combat_sim. Card effects the
engine doesn't model are listed under ``not_applied``. The original state
is never changed; the caller gets a new one.
"""

from __future__ import annotations

from warlord.actions import (
    ComponentAction,
    Pass,
    Respond,
    StrategicAction,
    TacticalAction,
    Transaction,
    parse_action,
)
from warlord.engine.invasion_production import _enemy_ships
from warlord.engine.movement import GROUND
from warlord.engine.options import initiative
from warlord.engine.units import canonical
from warlord.engine.validate import validate_action
from warlord.state.perspective import project
from warlord.state.schema import GameState, LogEvent


class ApplyRefused(ValueError):
    def __init__(self, violations):
        super().__init__("; ".join(v.message for v in violations))
        self.violations = violations


def _take(units: dict, unit: str, n: int) -> int:
    have = units.get(unit, 0)
    took = min(have, n)
    if took:
        units[unit] = have - took
        if not units[unit]:
            del units[unit]
    return took


def _add(units: dict, unit: str, n: int) -> None:
    if n > 0:
        units[unit] = units.get(unit, 0) + n


def _log(state: GameState, actor: str, kind: str, data: dict) -> None:
    state.log.append(LogEvent(
        seq=(state.log[-1].seq + 1) if state.log else 1,
        round=state.turn.round, step=state.turn.step, actor=actor, kind=kind, data=data,
    ))


def _next_player(state: GameState, kb) -> str | None:
    order = sorted(
        (min((initiative(s.id, kb) or 99) for s in p.strategy_cards) if p.strategy_cards else 99, pid)
        for pid, p in state.players.items()
    )
    seats = [pid for _, pid in order]
    if not seats:
        return None
    current = state.turn.active_player
    start = seats.index(current) + 1 if current in seats else 0
    for i in range(len(seats)):
        pid = seats[(start + i) % len(seats)]
        if not state.players[pid].passed:
            return pid
    return None


def _end_turn(state: GameState, kb, events: list[str]) -> None:
    state.turn.used_this_turn = []
    state.turn.step = None
    nxt = _next_player(state, kb)
    if nxt is None:
        state.turn.phase = "status"
        state.turn.active_player = None
        events.append("Every player has passed: the action phase ends and the status phase begins.")
    else:
        state.turn.active_player = nxt
        events.append(f"Turn passes to {nxt}.")


def _tactical(state: GameState, a: TacticalAction, kb, events, pending, not_applied) -> bool:
    """Apply a tactical action. Returns True if the turn is finished, False if it stops pending."""
    me = state.players[a.player]
    target = state.system(a.activate)
    target.command_tokens.append(a.player)
    me.command_tokens.tactic -= 1
    events.append(f"{a.player} activates {a.activate}.")

    for mv in a.moves:
        origin = state.system(mv.from_)
        for unit, n in mv.units.items():
            left = n - _take(origin.space.setdefault(a.player, {}), unit, n)
            for planet in origin.planets.values():
                if left and canonical(unit) in GROUND | {"fighter"}:
                    left -= _take(planet.units.setdefault(a.player, {}), unit, left)
            _add(target.space.setdefault(a.player, {}), unit, n - left)
            hurt = origin.damaged.get(a.player, {}).get(unit, 0)
            still = origin.space.get(a.player, {}).get(unit, 0)
            if hurt > still:  # damaged copies moved with the group
                _take(origin.damaged[a.player], unit, hurt - still)
                _add(target.damaged.setdefault(a.player, {}), unit, hurt - still)
        events.append(f"{a.player} moves {mv.units} from {mv.from_} to {a.activate}.")
    _prune(state)

    if _enemy_ships(target, a.player):
        enemies = {o: u for o, u in target.space.items() if o != a.player}
        pending.append({
            "step": "space_combat",
            "detail": f"Space combat in {a.activate}: {a.player} {target.space[a.player]} against {enemies}.",
            "how": "Use combat_sim for the odds; apply the result before invasion and production.",
        })
        if a.invade or a.produce:
            not_applied.append("invasion and production wait for the space combat result")
        return False

    for inv in a.invade:
        planet = target.planets[inv.planet]
        landing = {}
        for unit in ("infantry", "mech"):
            n = getattr(inv, unit)
            took = _take(target.space.setdefault(a.player, {}), unit, n)
            if took:
                _add(planet.units.setdefault(a.player, {}), unit, took)
                landing[unit] = took
        events.append(f"{a.player} commits {landing} to {inv.planet}.")
        defenders = {o: u for o, u in planet.units.items() if o != a.player and any(canonical(x) in GROUND for x in u)}
        cannons = {o: u for o, u in planet.units.items() if o != a.player and any(canonical(x) == "pds" for x in u)}
        if cannons:
            pending.append({"step": "space_cannon_defense", "detail": f"PDS on {inv.planet} may fire at the landing ground forces."})
        if defenders:
            pending.append({
                "step": "ground_combat",
                "detail": f"Ground combat on {inv.planet}: {landing} against {defenders}.",
                "how": "Apply the result, then control goes to whoever still has ground forces there.",
            })
            continue
        if planet.controller != a.player:
            for o in [o for o in planet.units if o != a.player]:
                planet.units[o] = {u: n for u, n in planet.units[o].items() if canonical(u) not in ("pds", "space_dock")}
            planet.controller = a.player
            planet.exhausted = True  # 49.5b: the gained planet card comes in exhausted
            events.append(f"{a.player} gains control of {inv.planet} (its planet card is exhausted).")
    if any(p["step"] == "ground_combat" for p in pending):
        if a.produce:
            not_applied.append("production waits for the ground combat result")
        return False

    for entry in a.produce:
        dock_planet = next(
            (name for name, p in target.planets.items()
             if p.controller == a.player and any(canonical(u) == "space_dock" for u in p.units.get(a.player, {}))),
            None,
        )
        for unit, n in entry.units.items():
            if canonical(unit) in GROUND and dock_planet:
                _add(target.planets[dock_planet].units.setdefault(a.player, {}), unit, n)
            else:
                _add(target.space.setdefault(a.player, {}), unit, n)
        for item in entry.pay_with:
            if item.startswith("tg:"):
                me.trade_goods -= int(item.split(":", 1)[1] or 0)
            else:
                for s in state.map:
                    if item in s.planets:
                        s.planets[item].exhausted = True
        if not entry.pay_with:
            not_applied.append("payment for production: choose which planets to exhaust and trade goods to spend")
        events.append(f"{a.player} produces {entry.units} in {a.activate}.")
    return True


def _prune(state: GameState) -> None:
    """Drop the empty unit maps that moving units out leaves behind."""
    for s in state.map:
        holders = [s.space, s.damaged] + [p.units for p in s.planets.values()]
        for holder in holders:
            for owner in [o for o, u in holder.items() if not u]:
                del holder[owner]


class HiddenInformation(ValueError):
    pass


def apply_action(state: GameState, action: dict, kb=None, viewer: str | None = None) -> dict:
    """Apply ``action`` to a deep copy of ``state``. Raises ApplyRefused if the engine finds it illegal.

    Validation runs on ``viewer``'s perspective view, so checking an opponent's
    move can never reveal their hidden cards. For the same reason, an
    opponent's card plays (component actions, responses) are refused: whether
    they hold the card is hidden.
    """
    parsed = parse_action(action)
    viewer = viewer or parsed.player
    if parsed.player != viewer and isinstance(parsed, (ComponentAction, Respond, Transaction)):
        raise HiddenInformation(
            f"What-ifs for {parsed.player}'s card plays and transactions would depend on cards hidden from {viewer}."
        )
    view = project(state, viewer)
    check = validate_action(view, action, kb=kb)
    if check.legal is False:
        raise ApplyRefused(check.violations)

    new = state.model_copy(deep=True)
    events: list[str] = []
    pending: list[dict] = []
    not_applied: list[str] = []
    turn_over = True
    me = new.players[parsed.player]

    if isinstance(parsed, TacticalAction):
        turn_over = _tactical(new, parsed, kb, events, pending, not_applied)
        if any("gravity rift" in w.message for w in check.warnings):
            pending.append({"step": "gravity_rift", "detail": next(w.message for w in check.warnings if "gravity rift" in w.message)})
    elif isinstance(parsed, StrategicAction):
        card = next(s for s in me.strategy_cards if s.id == parsed.card)
        card.primary_used = card.exhausted = True
        events.append(f"{parsed.player} resolves the primary ability of {parsed.card}; other players may follow.")
        not_applied.append(f"the effects of {parsed.card}'s primary and secondary abilities")
    elif isinstance(parsed, ComponentAction):
        tech = next((t for t in me.techs if t.id == parsed.source), None)
        if tech is not None:
            tech.exhausted = True
        if parsed.source in me.private.action_cards:
            me.private.action_cards.remove(parsed.source)
            new.discards.action_cards.append(parsed.source)
        events.append(f"{parsed.player} uses {parsed.source}.")
        not_applied.append(f"the effect of {parsed.source}")
    elif isinstance(parsed, Pass):
        me.passed = True
        events.append(f"{parsed.player} passes.")
    elif isinstance(parsed, Transaction):
        turn_over = False
        not_applied.append("the transaction itself: agree the items, then record them")
    elif isinstance(parsed, Respond):
        turn_over = False
        not_applied.append(f"the effect of {parsed.play or 'declining'}")

    if turn_over and not pending and isinstance(parsed, (TacticalAction, StrategicAction, ComponentAction, Pass)):
        _end_turn(new, kb, events)
    elif pending:
        new.turn.step = pending[0]["step"]
    _prune(new)
    _log(new, parsed.player, f"whatif_{parsed.type}", {"action": action})

    return {
        "state": new,
        "verified": check.legal is True,
        "unchecked": check.unchecked,
        "warnings": [w.message for w in check.warnings],
        "events": events,
        "pending": pending,
        "not_applied": not_applied,
    }
