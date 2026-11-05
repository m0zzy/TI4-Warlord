"""The eight agent tools: definitions (Claude tool schemas) and handlers.

Handlers run against a :class:`ToolContext` that is bound to one game and one
seat. The model can never ask for another seat's view: ``get_state`` refuses a
different ``viewer``, and every engine call runs on the bound seat's
perspective view.

The context also records what the session has seen, so the referee can check
afterwards that every citation came from a tool result and that every
text-only step was flagged.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from warlord.engine.combat import combat_sim
from warlord.engine.validate import RefResolver, kb_ref_resolver, validate_action
from warlord.kb.store import KnowledgeBase
from warlord.ruleset import Ruleset
from warlord.state.schema import PerspectiveView
from warlord.state.store import GameNotFound, GameStore


class ToolError(Exception):
    """Raised by a handler; returned to the model as an is_error tool_result."""


@dataclass
class ToolContext:
    kb: KnowledgeBase
    store: GameStore | None = None
    game_id: str | None = None
    viewer: str | None = None
    default_ruleset: Ruleset | None = None  # used when no game is attached
    ref_overrides: dict[str, list[str]] = field(default_factory=dict)

    # Session record.
    seen_ids: set[str] = field(default_factory=set)
    coverage: list[dict] = field(default_factory=list)  # one entry per engine-backed step
    calls: list[dict] = field(default_factory=list)

    _view: PerspectiveView | None = field(default=None, init=False, repr=False)
    _resolver: RefResolver | None = field(default=None, init=False, repr=False)

    def view(self) -> PerspectiveView | None:
        if self._view is None and self.store and self.game_id and self.viewer:
            try:
                self._view = self.store.view(self.game_id, self.viewer)
            except GameNotFound as e:
                raise ToolError(f"Game {self.game_id!r} not found.") from e
        return self._view

    def ruleset(self) -> Ruleset | None:
        v = self.view()
        return v.ruleset if v else self.default_ruleset

    def resolver(self) -> RefResolver:
        if self._resolver is None:
            self._resolver = kb_ref_resolver(self.kb, self.ruleset(), self.ref_overrides)
        return self._resolver

    def note_ids(self, payload: Any) -> None:
        """Remember every record id in a tool result as citable."""
        stack = [payload]
        while stack:
            cur = stack.pop()
            if isinstance(cur, dict):
                rid = cur.get("id")
                if isinstance(rid, str) and rid in self.kb:
                    self.seen_ids.add(rid)
                for key in ("refs", "related"):
                    for r in cur.get(key, []) or []:
                        if isinstance(r, str) and r in self.kb:
                            self.seen_ids.add(r)
                stack.extend(cur.values())
            elif isinstance(cur, list):
                stack.extend(cur)


# ------------------------------------------------------------------- handlers


def _get_state(ctx: ToolContext, args: dict) -> dict:
    if ctx.store is None or ctx.game_id is None or ctx.viewer is None:
        raise ToolError("No game is attached to this conversation; ask the player to describe the position.")
    game_id = args.get("game_id") or ctx.game_id
    viewer = args.get("viewer") or ctx.viewer
    if game_id != ctx.game_id:
        raise ToolError(f"This conversation is about game {ctx.game_id!r} only.")
    if viewer != ctx.viewer:
        raise ToolError(
            f"You advise {ctx.viewer} and can only see {ctx.viewer}'s view. "
            "Other players' hidden information is not available."
        )
    view = ctx.view()
    assert view is not None
    return view.model_dump(mode="json", exclude_none=True)


def _rules_lookup(ctx: ToolContext, args: dict) -> dict:
    q = (args.get("question") or "").strip()
    if not q:
        raise ToolError("question is required")
    k = int(args.get("k") or 6)
    out = ctx.kb.rules_lookup(q, ruleset=ctx.ruleset(), k=max(1, min(k, 12)))
    if not out["results"]:
        out["note"] = "No matching rule text in the knowledge base. Any answer is unverified."
    return out


def _card_lookup(ctx: ToolContext, args: dict) -> dict:
    name = (args.get("name") or "").strip()
    if not name:
        raise ToolError("name is required")
    out = ctx.kb.card_lookup(name, ruleset=ctx.ruleset())
    match = out.get("match")
    if match is not None:
        out["coverage"] = "engine" if match.get("engine_ops") else "text_only"
    return out


def _validate_action(ctx: ToolContext, args: dict) -> dict:
    view = ctx.view()
    if view is None:
        raise ToolError("validate_action needs a game state; none is attached.")
    action = args.get("action")
    if not isinstance(action, dict):
        raise ToolError("action must be an object in the canonical action format")
    if "player" not in action:
        action = {**action, "player": view.turn.active_player or ctx.viewer}
    result = validate_action(view, action, ctx.resolver(), ctx.kb).to_dict()
    result["status_line"] = status_line(result)
    ctx.coverage.append({"tool": "validate_action", "coverage": result["coverage"], "legal": result["legal"]})
    return result


def status_line(result: dict) -> str:
    """One line the agent can repeat to the player as-is."""
    if result.get("schema_errors"):
        return "Not checked: the move is not in the engine's action format."
    if result["legal"] is False:
        v = result["violations"][0]
        refs = f" ({', '.join(v['refs'])})" if v.get("refs") else ""
        more = f" (+{len(result['violations']) - 1} more)" if len(result["violations"]) > 1 else ""
        return f"Illegal, engine-verified: {v['message']}{refs}{more}"
    warn = ""
    if result.get("warnings"):
        warn = " Note: " + " ".join(w["message"] for w in result["warnings"])
    if result["legal"] is True:
        return "Legal, engine-verified." + warn
    return "Not verified by the engine. Unchecked: " + "; ".join(result.get("unchecked") or ["unknown"]) + "." + warn


def _combat_sim(ctx: ToolContext, args: dict) -> dict:
    try:
        result = combat_sim(
            args["attacker"],
            args["defender"],
            trials=int(args.get("trials") or 5000),
            space_cannon=bool(args.get("space_cannon", True)),
            afb=bool(args.get("afb", True)),
            seed=args.get("seed"),
        )
    except (KeyError, TypeError, ValueError) as e:
        raise ToolError(f"bad combat_sim input: {e}") from e
    ctx.coverage.append({"tool": "combat_sim", "coverage": "engine"})
    return result


def _seat(ctx: ToolContext, args: dict) -> str:
    view = ctx.view()
    if view is None:
        raise ToolError("No game is open.")
    player = args.get("player") or ctx.viewer
    if player not in view.players:
        raise ToolError(f"No seat {player!r}. Seats: {', '.join(sorted(view.players))}.")
    return player


def _legal_actions(ctx: ToolContext, args: dict) -> dict:
    from warlord.engine.options import legal_actions

    player = _seat(ctx, args)
    result = legal_actions(ctx.view(), ctx.kb, player)
    ctx.coverage.append({"tool": "legal_actions", "coverage": "engine"})
    return result


def _diff(before: dict, after: dict) -> dict:
    """What changed between two dumped perspective views."""
    out: dict = {}
    old_map = {s["position"]: s for s in before.get("map", [])}
    changed = [s for s in after.get("map", []) if old_map.get(s["position"]) != s]
    if changed:
        out["systems"] = changed
    players = {}
    for pid, p in after.get("players", {}).items():
        fields = {k: v for k, v in p.items() if before["players"].get(pid, {}).get(k) != v}
        if fields:
            players[pid] = fields
    if players:
        out["players"] = players
    turn = {k: v for k, v in after.get("turn", {}).items() if before.get("turn", {}).get(k) != v}
    if turn:
        out["turn"] = turn
    if after.get("hand") != before.get("hand"):
        out["hand"] = after.get("hand")
    return out


def _apply_action(ctx: ToolContext, args: dict) -> dict:
    from warlord.engine.apply import ApplyRefused, HiddenInformation, apply_action
    from warlord.state.perspective import project

    if ctx.store is None or ctx.game_id is None:
        raise ToolError("No game is open.")
    action = args.get("action")
    if not isinstance(action, dict):
        raise ToolError("action must be an object in the canonical action format")
    state = ctx.store.load(ctx.game_id)
    try:
        result = apply_action(state, action, ctx.kb, viewer=ctx.viewer)
    except ApplyRefused as e:
        return {"applied": False, "illegal": [v.to_dict() for v in e.violations]}
    except HiddenInformation as e:
        raise ToolError(str(e)) from e
    except (KeyError, ValueError) as e:
        raise ToolError(f"could not apply the action: {e}") from e
    before = project(state, ctx.viewer).model_dump(mode="json", exclude={"log"})
    after = project(result["state"], ctx.viewer).model_dump(mode="json", exclude={"log"})
    ctx.coverage.append({"tool": "apply_action", "coverage": "engine" if result["verified"] else "text_only"})
    return {
        "applied": True,
        "what_if": "Nothing was saved; this shows the state after the action.",
        "verified": result["verified"],
        "unchecked": result["unchecked"],
        "warnings": result["warnings"],
        "events": result["events"],
        "pending": result["pending"],
        "not_applied": result["not_applied"],
        "changes": _diff(before, after),
    }


def _evaluate_position(ctx: ToolContext, args: dict) -> dict:
    from warlord.engine.evaluate import evaluate_position

    player = _seat(ctx, args)
    return evaluate_position(ctx.view(), ctx.kb, player)


def _not_yet(name: str) -> Callable[[ToolContext, dict], dict]:
    def handler(ctx: ToolContext, args: dict) -> dict:
        ctx.coverage.append({"tool": name, "coverage": "text_only"})
        return {
            "coverage": "text_only",
            "status": "not_implemented",
            "note": f"{name} is not implemented yet; reason from the rules text and flag the answer as unverified.",
        }

    return handler


# ---------------------------------------------------------------- definitions

_UNITS = {"type": "object", "additionalProperties": {"type": "integer", "minimum": 0}}
_SIDE = {
    "type": "object",
    "properties": {
        "units": {**_UNITS, "description": 'Unit type -> count, e.g. {"dreadnought": 2, "fighter": 3, "pds": 1}. PDS on the defender fire space cannon offense.'},
        "upgrades": {"type": "array", "items": {"type": "string"}, "description": 'Unit upgrades owned, e.g. ["carrier_2", "destroyer_2"].'},
        "damaged": {**_UNITS, "description": "Units that have already used Sustain Damage."},
        "combat_modifier": {"type": "integer", "description": "Added to every combat die (e.g. -1 from an ability)."},
        "afb_modifier": {"type": "integer"},
        "space_cannon_modifier": {"type": "integer", "description": "Added to this side's space cannon dice (e.g. -1 when the target owns Antimass Deflectors)."},
        "space_cannon_units": {**_UNITS, "description": "Extra space cannon shooters outside the system, e.g. adjacent PDS II."},
        "overrides": {"type": "object", "description": 'Per-unit stat overrides for faction units or flagships, e.g. {"flagship": {"combat": 5, "combat_dice": 2}}.'},
    },
    "required": ["units"],
}

TOOL_DEFS: dict[str, dict] = {
    "get_state": {
        "description": "Get the current game state as the asking player sees it: the map, every player's public information, the open timing window and per-turn usage flags, plus the asking player's own hand. Opponents' hidden cards appear only as counts. Call this first for any question about a specific game.",
        "input_schema": {
            "type": "object",
            "properties": {
                "game_id": {"type": "string", "description": "Omit to use the game attached to this conversation."},
                "viewer": {"type": "string", "description": "Omit; it is always the asking player."},
            },
        },
    },
    "rules_lookup": {
        "description": "Search the Living Rules Reference and rulings (official FAQ, developer rulings, adjudicated cases). Returns ranked records with ids to cite and an authority level. Use specific game terms (unit names, ability keywords, step names) for best results; call it more than once with different phrasing if the first results miss.",
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "k": {"type": "integer", "minimum": 1, "maximum": 12},
            },
            "required": ["question"],
        },
    },
    "card_lookup": {
        "description": "Look up a component (action card, agenda, technology, unit, promissory note, leader, relic, objective) by name or id, with errata applied. Tolerates misspelled names and returns close alternatives. coverage is 'engine' when the engine implements the card, else 'text_only'.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Card name or id, e.g. 'Sabotage' or 'ac.sabotage'."}},
            "required": ["name"],
        },
    },
    "validate_action": {
        "description": "Check whether a proposed action is legal in the current state. Returns legal (true, false, or null when part of the action is outside engine coverage), violations with rule refs, what was checked, and what was not. A violation is definitive. When legal is null, answer the unchecked parts from the rules text and flag them unverified.",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "object",
                    "description": 'Canonical action. Tactical: {"type":"tactical","player":"p3","activate":"201","moves":[{"from":"102","units":{"carrier":1,"infantry":2}}],"invade":[{"planet":"quann","infantry":2}],"produce":[]}. Strategic: {"type":"strategic","player","card","primary":{...}} where primary is, by card: Leadership {"influence_from":[planet],"trade_goods":n,"tokens":{"tactic":a,"fleet":b,"strategy":c}}; Diplomacy {"system":pos,"ready":[planet]}; Politics {"speaker":player}; Construction {"place":[{"structure":"pds"|"space_dock","planet":p}, ...]}; Trade {"chosen_players":[player]}; Warfare (PoK) {"remove_token_from":pos,"to_pool":pool} or (Thunder's Edge) {"tactical":{...tactical action...}}; Technology {"research":[tech id],"specialties":[planet],"pay_with":[planet|"tg:N"]}; Imperial {"score":objective id}. Others, {"type":"component","player","source"}, {"type":"transaction","player","with_player","give","receive"}, {"type":"pass","player"}, {"type":"respond","player","play"} (play null declines).',
                }
            },
            "required": ["action"],
        },
    },
    "combat_sim": {
        "description": "Monte Carlo space combat odds: win/draw/loss, expected survivors, hits needed and expected hits per round. Models space cannon offense, anti-fighter barrage, Sustain Damage and combat rounds; not retreats or card effects beyond modifiers. Always use this instead of estimating odds.",
        "input_schema": {
            "type": "object",
            "properties": {
                "attacker": _SIDE,
                "defender": _SIDE,
                "trials": {"type": "integer", "minimum": 100, "maximum": 50000},
                "space_cannon": {"type": "boolean"},
                "afb": {"type": "boolean"},
            },
            "required": ["attacker", "defender"],
        },
    },
    "legal_actions": {
        "description": "Option families available to a player now: systems they can activate, with the ship groups that can reach each one; unused strategy cards; component actions; responses to the open window; and whether passing is allowed (and why not). Draft a concrete action from an option, then run validate_action on it.",
        "input_schema": {"type": "object", "properties": {"player": {"type": "string"}}},
    },
    "apply_action": {
        "description": "What-if: apply an action to a copy of the game (nothing is saved). Returns what changed, the events, and what is pending, such as a space combat or ground combat to resolve with combat_sim. Card effects the engine doesn't model are listed under not_applied. Illegal actions are refused with the violations.",
        "input_schema": {"type": "object", "properties": {"action": {"type": "object"}}, "required": ["action"]},
    },
    "evaluate_position": {
        "description": "Heuristic summary of a player's position, from the asking player's view: the VP race and scored public objectives, spending power (ready resources and influence, trade goods, command tokens), and threats (other players' ships that could reach the player's systems next turn, with expected hits). Not a verdict: weigh it.",
        "input_schema": {"type": "object", "properties": {"player": {"type": "string"}}},
    },
}

HANDLERS: dict[str, Callable[[ToolContext, dict], dict]] = {
    "get_state": _get_state,
    "rules_lookup": _rules_lookup,
    "card_lookup": _card_lookup,
    "validate_action": _validate_action,
    "combat_sim": _combat_sim,
    "legal_actions": _legal_actions,
    "apply_action": _apply_action,
    "evaluate_position": _evaluate_position,
}

IMPLEMENTED = ("get_state", "rules_lookup", "card_lookup", "validate_action", "combat_sim")
REFEREE_TOOLS = IMPLEMENTED
ADVISOR_TOOLS = (*IMPLEMENTED, "legal_actions", "apply_action", "evaluate_position")


def tool_definitions(names=REFEREE_TOOLS) -> list[dict]:
    """Claude API tool definitions, in a fixed order so the prompt cache stays warm."""
    return [{"name": n, **TOOL_DEFS[n]} for n in names]


def run_tool(ctx: ToolContext, name: str, args: dict) -> tuple[str, bool]:
    """Execute a tool. Returns (JSON content, is_error)."""
    ctx.calls.append({"tool": name, "input": args})
    handler = HANDLERS.get(name)
    if handler is None:
        return json.dumps({"error": f"unknown tool {name!r}"}), True
    try:
        result = handler(ctx, args or {})
    except ToolError as e:
        return json.dumps({"error": str(e)}), True
    ctx.note_ids(result)
    return json.dumps(result, ensure_ascii=False, separators=(",", ":")), False
