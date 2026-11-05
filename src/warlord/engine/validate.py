"""validate_action: partial rules engine with honest coverage reporting.

The engine checks what it can check with certainty and lists what it did not
check. The result contract:

* ``legal: False`` with ``coverage: "engine"`` - at least one violation was
  found. A found violation is definitive even if other parts went unchecked.
* ``legal: True`` with ``coverage: "engine"`` - every aspect of the action was
  checked and none failed.
* ``legal: None`` with ``coverage: "text_only"`` - nothing failed, but some
  aspects (listed in ``unchecked``) are outside engine coverage. The agent
  must answer those from the text and flag the answer as unverified.

Validation runs on a :class:`PerspectiveView`, never the omniscient state, so a
violation message can never reveal hidden information.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from pydantic import ValidationError

from warlord.actions import (
    ComponentAction,
    Pass,
    Respond,
    StrategicAction,
    TacticalAction,
    Transaction,
    parse_action,
)
from warlord.state.schema import PerspectiveView

GROUND_OR_FIGHTER = {"infantry", "mech", "fighter"}

# Rule topics the engine cites. A resolver maps each to knowledge-base ids
# (LRR numbers) once the LRR is loaded; see RefResolver.
TOPICS = {
    "action_phase": "Action phase: on their turn the active player performs one action",
    "passing": "Passing: a player cannot pass until they have performed the strategic action of each strategy card they have",
    "tactical.activation": "Tactical action activation: spend a command token from the tactic pool, place it in a system that does not contain one of your command tokens",
    "movement.origin": "Movement: ships cannot move out of a system that contains your command token",
    "movement.units_present": "Movement: only units you have in the system can move",
    "strategic_action": "Strategic action: resolve the primary ability of an unexhausted strategy card you have",
    "timing.windows": "Timing: when and after abilities can only be resolved during their timing window",
    "movement.range": "Movement: the number of systems a ship enters cannot exceed its move value; it cannot move through systems with other players' ships",
    "movement.gravity_rift": "Gravity rift: a ship moving out of a gravity rift applies +1 to its move value and rolls a die",
    "transport.capacity": "Transport: the number of units a ship transports cannot exceed its capacity value",
    "capacity.excess": "Capacity: excess fighters and ground forces in a system's space area are removed",
    "fleet_pool": "Fleet pool: maximum number of non-fighter ships a player can have in a system",
    "invasion": "Invasion: commit ground forces from the space area of the active system to land on its planets",
    "production": "Production: the number of units produced cannot exceed the combined production value; ships cannot be produced in a system with other players' ships",
    "production.cost": "Producing units: spend resources equal to or greater than the cost of the units produced",
    "production.reinforcements": "Producing units: a player is limited by the units in their reinforcements",
}


@dataclass
class Violation:
    topic: str
    message: str
    refs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"topic": self.topic, "message": self.message, "refs": self.refs}


@dataclass
class ValidationResult:
    legal: bool | None
    coverage: str  # "engine" | "text_only"
    violations: list[Violation] = field(default_factory=list)
    checked: list[str] = field(default_factory=list)
    unchecked: list[str] = field(default_factory=list)
    schema_errors: list[str] = field(default_factory=list)
    warnings: list[Violation] = field(default_factory=list)  # legal, but with a cost (units removed, dice to roll)

    def to_dict(self) -> dict:
        return {
            "legal": self.legal,
            "coverage": self.coverage,
            "violations": [v.to_dict() for v in self.violations],
            "warnings": [w.to_dict() for w in self.warnings],
            "checked": self.checked,
            "unchecked": self.unchecked,
            **({"schema_errors": self.schema_errors} if self.schema_errors else {}),
        }


RefResolver = Callable[[str], list[str]]


def _no_refs(topic: str) -> list[str]:
    return []


class _Checker:
    def __init__(self, view: PerspectiveView, resolve: RefResolver, kb=None):
        self.view = view
        self.resolve = resolve
        self.kb = kb
        self.violations: list[Violation] = []
        self.warnings: list[Violation] = []
        self.checked: list[str] = []
        self.unchecked: list[str] = []

    def check(self, name: str, ok: bool, topic: str, message: str) -> bool:
        self.checked.append(name)
        if not ok:
            self.violations.append(Violation(topic, message, self.resolve(topic)))
        return ok

    def skip(self, aspect: str) -> None:
        self.unchecked.append(aspect)

    def warn(self, topic: str, message: str) -> None:
        self.warnings.append(Violation(topic, message, self.resolve(topic)))

    def result(self) -> ValidationResult:
        if self.violations:
            r = ValidationResult(False, "engine", self.violations, self.checked, self.unchecked)
        elif self.unchecked:
            r = ValidationResult(None, "text_only", [], self.checked, self.unchecked)
        else:
            r = ValidationResult(True, "engine", [], self.checked, [])
        r.warnings = self.warnings
        return r

    # ------------------------------------------------------------- shared checks

    def own_turn(self, player: str) -> None:
        t = self.view.turn
        self.check(
            "phase_is_action",
            t.phase == "action",
            "action_phase",
            f"Actions are taken in the action phase; the game is in the {t.phase} phase.",
        )
        self.check(
            "is_active_player",
            t.active_player == player,
            "action_phase",
            f"It is {t.active_player}'s turn, not {player}'s.",
        )
        p = self.view.players.get(player)
        if p is not None:
            self.check(
                "has_not_passed",
                not p.passed,
                "action_phase",
                f"{player} has already passed this round.",
            )
        if t.open_window is not None:
            self.skip(
                f"an open {t.open_window.timing} window on {t.open_window.event} may need to close first"
            )

    def player_exists(self, player: str) -> bool:
        return self.check(
            "player_exists",
            player in self.view.players,
            "action_phase",
            f"No player {player!r} in this game.",
        )


# ------------------------------------------------------------------ per type


def _tactical(c: _Checker, a: TacticalAction) -> None:
    view = c.view
    c.own_turn(a.player)
    me = view.players[a.player]
    c.check(
        "tactic_token_available",
        me.command_tokens.tactic >= 1,
        "tactical.activation",
        f"{a.player} has no command tokens in their tactic pool.",
    )
    target = view.system(a.activate)
    if not c.check(
        "system_exists",
        target is not None,
        "tactical.activation",
        f"There is no system at position {a.activate}.",
    ):
        return
    assert target is not None
    c.check(
        "system_not_already_activated",
        a.player not in target.command_tokens,
        "tactical.activation",
        f"System {a.activate} already contains {a.player}'s command token, so {a.player} cannot activate it.",
    )

    for i, mv in enumerate(a.moves):
        origin = view.system(mv.from_)
        if not c.check(
            f"move[{i}].origin_exists",
            origin is not None,
            "movement.units_present",
            f"There is no system at position {mv.from_}.",
        ):
            continue
        assert origin is not None
        c.check(
            f"move[{i}].origin_not_activated",
            a.player not in origin.command_tokens and mv.from_ != a.activate,
            "movement.origin",
            f"Units cannot move out of {mv.from_}: it contains {a.player}'s command token"
            + (" (it is the system being activated)." if mv.from_ == a.activate else "."),
        )
        in_space = origin.space.get(a.player, {})
        for unit, n in mv.units.items():
            if n <= 0:
                c.check(
                    f"move[{i}].{unit}.positive",
                    False,
                    "movement.units_present",
                    f"Unit counts must be positive ({unit}: {n}).",
                )
                continue
            available = in_space.get(unit, 0)
            if unit in GROUND_OR_FIGHTER:
                # Ground forces (and fighters) on planets in the origin system can be picked up.
                available += sum(p.units.get(a.player, {}).get(unit, 0) for p in origin.planets.values())
            c.check(
                f"move[{i}].{unit}.present",
                available >= n,
                "movement.units_present",
                f"{a.player} has {available} {unit} in {mv.from_} but tries to move {n}.",
            )
    if a.moves and not c.violations:
        from warlord.engine.movement import check_moves

        for f in check_moves(view, a, c.kb):
            if f.kind == "violation":
                c.check(f"movement.{f.topic}", False, f.topic, f.message)
            elif f.kind == "unchecked":
                c.skip(f.message)
            else:
                c.warn(f.topic, f.message)
        c.checked += ["movement_range_and_path", "transport_capacity", "capacity_and_fleet_pool_after_movement"]
    elif a.moves:
        c.skip("movement range and transport (not checked because of the violations above)")
    if (a.invade or a.produce) and not c.violations:
        from warlord.engine.invasion_production import check_invasion, check_production
        from warlord.engine.movement import MovementContext

        mc = MovementContext.build(view, a.player, c.kb)
        for f in check_invasion(view, a, c.kb, mc) + check_production(view, a, c.kb, mc):
            if f.kind == "violation":
                c.check(f"{f.topic}.rule", False, f.topic, f.message)
            elif f.kind == "unchecked":
                c.skip(f.message)
            else:
                c.warn(f.topic, f.message)
        if a.invade:
            c.checked.append("invasion_commit")  # the results (space cannon defense, ground combat) are dice, not legality
        if a.produce:
            c.checked += ["production_limit", "production_cost", "production_placement"]
    elif a.invade or a.produce:
        c.skip("invasion and production (not checked because of the violations above)")


def _pass(c: _Checker, a: Pass) -> None:
    c.own_turn(a.player)
    me = c.view.players[a.player]
    unused = [s.id for s in me.strategy_cards if not s.primary_used]
    c.check(
        "strategic_actions_done",
        not unused,
        "passing",
        f"{a.player} cannot pass while they still have the strategic action of {', '.join(unused)} to perform.",
    )


def _strategic(c: _Checker, a: StrategicAction) -> None:
    c.own_turn(a.player)
    me = c.view.players[a.player]
    held = next((s for s in me.strategy_cards if s.id == a.card), None)
    if c.check(
        "card_held",
        held is not None,
        "strategic_action",
        f"{a.player} does not have the {a.card} strategy card.",
    ):
        assert held is not None
        c.check(
            "primary_unused",
            not held.primary_used and not held.exhausted,
            "strategic_action",
            f"{a.player} has already resolved the primary ability of {a.card} this round.",
        )
    c.skip(f"primary ability choices for {a.card}")


def _component(c: _Checker, a: ComponentAction) -> None:
    c.own_turn(a.player)
    view = c.view
    if a.source.startswith("ac.") and a.player == view.viewer:
        c.check(
            "action_card_in_hand",
            a.source in view.hand.action_cards,
            "action_phase",
            f"{a.source} is not in {a.player}'s hand.",
        )
    c.skip(f"effect and requirements of {a.source}")


def _transaction(c: _Checker, a: Transaction) -> None:
    c.check(
        "counterparty_exists",
        a.with_player in c.view.players and a.with_player != a.player,
        "action_phase",
        f"{a.with_player!r} is not another player in this game.",
    )
    c.skip("neighbor requirement and once-per-turn transaction limit")
    c.skip("whether the offered items are tradeable")


def _respond(c: _Checker, a: Respond) -> None:
    view = c.view
    w = view.turn.open_window
    if not c.check(
        "window_open",
        w is not None,
        "timing.windows",
        "No timing window is open, so there is nothing to respond to.",
    ):
        return
    assert w is not None
    c.check(
        "player_awaited",
        not w.awaiting or a.player in w.awaiting,
        "timing.windows",
        f"The open window is waiting on {', '.join(w.awaiting)}, not {a.player}.",
    )
    if a.play is None:
        return  # declining is always allowed once awaited
    if a.play.startswith("ac.") and a.player == view.viewer:
        c.check(
            "action_card_in_hand",
            a.play in view.hand.action_cards,
            "timing.windows",
            f"{a.play} is not in {a.player}'s hand.",
        )
    c.skip(f"whether {a.play}'s timing matches the open {w.timing} {w.event} window")


_HANDLERS = {
    TacticalAction: _tactical,
    Pass: _pass,
    StrategicAction: _strategic,
    ComponentAction: _component,
    Transaction: _transaction,
    Respond: _respond,
}


def validate_action(
    view: PerspectiveView, action: dict, resolve_refs: RefResolver = _no_refs, kb=None
) -> ValidationResult:
    try:
        parsed = parse_action(action)
    except ValidationError as e:
        errs = [f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()]
        return ValidationResult(None, "text_only", schema_errors=errs,
                                unchecked=["the action is malformed; fix the schema errors first"])
    c = _Checker(view, resolve_refs, kb)
    if c.player_exists(parsed.player):
        _HANDLERS[type(parsed)](c, parsed)  # type: ignore[operator]
    return c.result()


def kb_ref_resolver(kb, ruleset=None, overrides: dict[str, list[str]] | None = None) -> RefResolver:
    """Resolve engine topics to rule ids.

    Pinned ids (``overrides``, e.g. warlord/data/lrr_refs.json) win when the knowledge
    base holds them; otherwise the best-matching rule chunks are used.
    """
    overrides = overrides or {}
    cache: dict[str, list[str]] = {}

    def resolve(topic: str) -> list[str]:
        pinned = [r for r in overrides.get(topic, []) if r in kb]
        if pinned:
            return pinned
        if topic not in cache:
            hits = kb.rules_lookup(TOPICS.get(topic, topic), ruleset=ruleset, k=2)["results"]
            cache[topic] = [h["id"] for h in hits if h.get("kind") == "rule"][:2]
        return cache[topic]

    return resolve


def load_ref_overrides(path) -> dict[str, list[str]]:
    import json
    from pathlib import Path

    p = Path(path)
    if not p.exists():
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))
    return {k: list(v) for k, v in data.items() if not k.startswith("_")}
