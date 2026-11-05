"""Generate example set B (legality cases) from stored games with the engine.

For each game and seat, the generator makes that seat the active player in
the action phase. It then samples candidate actions: passing, tactical
actions with random targets, ship groups, cargo, invasions and production,
and strategic actions with sampled primary choices. Each one is run through
validate_action.

Only cases the engine decides fully (coverage "engine", legal True or False)
are kept, balanced between legal and illegal. Each case's perspective view
is saved next to it, so the case can be replayed exactly.

The games are the player's own private data, so write the output under
data/private/ as well.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from warlord.datasets.schemas import LegalityCase, Violation
from warlord.engine.movement import GROUND, SHIPS
from warlord.engine.units import canonical
from warlord.engine.validate import kb_ref_resolver, load_ref_overrides, validate_action
from warlord.paths import LRR_REFS
from warlord.state.perspective import project
from warlord.state.schema import GameState


def _units(holder: dict, kinds: set[str]) -> dict[str, int]:
    return {u: n for u, n in holder.items() if canonical(u) in kinds and n > 0}


def _tactical(rng: random.Random, view, seat: str) -> dict:
    # The Fracture's links aren't modelled, so moves there never get a definite verdict.
    target = rng.choice([s for s in view.map if not s.position.startswith("frac")] or view.map).position
    groups = [s for s in view.map if _units(s.space.get(seat, {}), SHIPS) and s.position != target]
    moves = []
    for s in rng.sample(groups, k=min(len(groups), rng.choice([0, 1, 1, 2]))):
        ships = _units(s.space.get(seat, {}), SHIPS)
        pick = {u: rng.randint(1, n) for u, n in ships.items() if rng.random() < 0.7} or dict([next(iter(ships.items()))])
        ground = {}
        for p in s.planets.values():
            for u, n in _units(p.units.get(seat, {}), GROUND).items():
                if rng.random() < 0.5:
                    ground[u] = ground.get(u, 0) + rng.randint(1, n + (1 if rng.random() < 0.15 else 0))
        moves.append({"from": s.position, "units": {**pick, **ground}})
    action: dict = {"type": "tactical", "player": seat, "activate": target, "moves": moves}
    system = view.system(target)
    landed = sum(n for m in moves for u, n in m["units"].items() if canonical(u) in GROUND)
    if system.planets and landed and rng.random() < 0.6:
        planet = rng.choice(list(system.planets))
        action["invade"] = [{"planet": planet, "infantry": rng.randint(1, landed + 1)}]
    if rng.random() < 0.3:
        action["produce"] = [{"where": target, "units": rng.choice([{"infantry": 2}, {"fighter": 2}, {"cruiser": 1},
                                                                   {"carrier": 1, "fighter": 2}, {"dreadnought": 1}])}]
    return action


def _strategic(rng: random.Random, view, seat: str) -> dict | None:
    me = view.players[seat]
    if not me.strategy_cards:
        return None
    card = rng.choice(me.strategy_cards).id
    name = card.lower()
    planets = [n for s in view.map for n, p in s.planets.items() if p.controller == seat]
    others = [p for p in view.players if p != seat]
    systems = [s.position for s in view.map]
    if "leadership" in name:
        primary = {"influence_from": rng.sample(planets, k=min(len(planets), rng.randint(0, 2))),
                   "tokens": {"tactic": rng.randint(0, 3), "fleet": rng.randint(0, 2)}}
    elif "diplomacy" in name:
        primary = {"system": rng.choice(systems), "ready": rng.sample(planets, k=min(len(planets), rng.randint(0, 2)))}
    elif "politics" in name:
        primary = {"speaker": rng.choice(list(view.players))}
    elif "construction" in name:
        primary = {"place": [{"structure": rng.choice(["pds", "space_dock"]), "planet": rng.choice(planets or ["none"])}]}
    elif "trade" in name:
        primary = {"chosen_players": rng.sample(list(view.players), k=min(len(view.players), rng.randint(0, 2)))}
    elif "warfare" in name and not name.split(".")[-1].startswith("te"):
        primary = {"remove_token_from": rng.choice(systems), "to_pool": "tactic"}
    else:
        return None  # Technology, Imperial and TE Warfare need richer sampling
    return {"type": "strategic", "player": seat, "card": card, "primary": primary}


def _snapshots(state: GameState, seat: str):
    """Two positions per seat: the board as stored, and the same board at the
    start of that seat's action phase (its command tokens off the board, its
    strategy cards unused, 3 tactic tokens). Stored games are often snapshots
    from late in a round, when almost every system is already activated."""
    for variant in ("as_is", "start_of_phase"):
        game = state.model_copy(deep=True)
        game.turn.phase, game.turn.active_player, game.turn.open_window = "action", seat, None
        me = game.players[seat]
        me.passed = False
        if variant == "start_of_phase":
            for s in game.map:
                s.command_tokens = [t for t in s.command_tokens if t != seat]
            for card in me.strategy_cards:
                card.primary_used = card.exhausted = False
            me.command_tokens.tactic = max(me.command_tokens.tactic, 3)
        elif not me.command_tokens.tactic:
            me.command_tokens.tactic = 1
        yield variant, game


def generate_cases(state: GameState, kb, per_seat: int = 20, seed: int = 0, max_tries: int = 400):
    """Yield (LegalityCase, view dict) pairs for one game: ``per_seat`` per seat and snapshot."""
    rng = random.Random(f"{seed}:{state.meta.game_id}")
    resolve = kb_ref_resolver(kb, state.ruleset, load_ref_overrides(LRR_REFS)) if kb is not None else None
    for seat, variant, game in ((seat, v, g) for seat in sorted(state.players) for v, g in _snapshots(state, seat)):
        view = project(game, seat)
        ref = f"{state.meta.source}:{state.meta.game_id}@r{state.turn.round}:{seat}:{variant}"
        want = {True: per_seat // 2, False: per_seat - per_seat // 2}
        seen: set[str] = set()
        n = 0
        for _ in range(max_tries):
            if not any(want.values()):
                break
            roll = rng.random()
            action = (
                {"type": "pass", "player": seat} if roll < 0.08
                else _strategic(rng, view, seat) if roll < 0.3
                else _tactical(rng, view, seat)
            )
            if action is None:
                continue
            key = json.dumps(action, sort_keys=True)
            if key in seen:
                continue
            seen.add(key)
            result = validate_action(view, action, resolve, kb=kb) if resolve else validate_action(view, action, kb=kb)
            if result.coverage != "engine" or result.legal is None or not want[result.legal]:
                continue
            want[result.legal] -= 1
            n += 1
            yield LegalityCase(
                id=f"b.{state.meta.game_id}.{seat}.{variant}.{n:03d}",
                ruleset=state.ruleset.lrr,
                game_id=state.meta.game_id,
                state_ref=ref,
                action=action,
                legal=result.legal,
                violations=[Violation(topic=v.topic, refs=v.refs, note=v.message) for v in result.violations],
                origin="engine",
            ), view.model_dump(mode="json")


def write_set_b(states, kb, out: str | Path, per_seat: int = 20, seed: int = 0) -> dict:
    out = Path(out)
    (out / "states").mkdir(parents=True, exist_ok=True)
    counts = {"cases": 0, "legal": 0, "illegal": 0, "games": 0}
    with (out / "set_b.jsonl").open("w", encoding="utf-8") as fh:
        for state in states:
            counts["games"] += 1
            for case, view in generate_cases(state, kb, per_seat=per_seat, seed=seed):
                snap = out / "states" / (case.state_ref.replace(":", "_").replace("@", "_") + ".json")
                if not snap.exists():
                    snap.write_text(json.dumps(view), encoding="utf-8")
                fh.write(case.model_dump_json(exclude_none=True) + "\n")
                counts["cases"] += 1
                counts["legal" if case.legal else "illegal"] += 1
    return counts
