"""Movement checks for a tactical action (LRR 58, 95, 16, 37 and the anomaly rules).

The engine checks, for each move:

* whether every ship can reach the active system within its move value
  (58.4f). The path:
  * follows adjacency, including wormholes and hyperlanes (6, 44, 101);
  * gains +1 move for each exit from a gravity rift (41.1);
  * cannot pass through other players' ships (58.4b), asteroid fields
    (11.1), supernovas (86.1) or nebulas (59.1);
  * starts at move 1 from a nebula (59.2).
* modelled technologies: Gravity Drive (+1 for one ship), Antimass Deflectors
  (asteroid fields), Light/Wave Deflector (through other players' ships) and
  Fighter II (fighters that move on their own);
* transport: fighters and ground forces moved with a group of ships fit
  that group's capacity (95, 16.1);
* afterwards, in the active system: capacity (16.3) and the fleet pool
  (37.1). Going over either is legal, but the excess is removed, so the
  engine warns instead of refusing.

Anything that could change these answers and is not modelled becomes an
unchecked aspect, never a violation, so a reported violation stays
definitive. That covers faction abilities, leaders, relics, laws such as
Wormhole Reconstruction, and The Fracture. They are found by reading the
card text in the knowledge base.
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field

from warlord.engine.geometry import system_adjacency
from warlord.engine.units import UnitStats, canonical, faction_unit_overrides, stats_for

MODELLED = {
    "tech.gravity_drive",
    "tech.antimass_deflectors",
    "tech.light_wave_deflector",
    "tech.fighter_ii",
    "unit.fighter2",
}
_MOVEMENT_TEXT = re.compile(
    r"move value|\bmoves?d? (?:through|into|out of)\b|\b(?:are|is|be|treated as|considered) adjacent\b"
    r"|wormholes?[^.]{0,40}(?:have no effect|cannot be used)|cannot move|can move|may move|\+1 move",
    re.IGNORECASE,
)
_RESTRICTIVE_TEXT = re.compile(
    r"cannot move|wormholes?[^.]{0,60}(?:have no effect|cannot be used)|(?:are|is) not adjacent", re.IGNORECASE
)
SHIPS = {"fighter", "destroyer", "cruiser", "carrier", "dreadnought", "war_sun", "flagship"}
GROUND = {"infantry", "mech"}
INF = 10**6


def _affects_movement(text: str, pattern=_MOVEMENT_TEXT) -> bool:
    m = pattern.search(text or "")
    if not m:
        return False
    # "...against ships in systems that are adjacent" (PDS II and kin) is about
    # space cannon range, not movement.
    if "adjacent" in m.group(0).lower() and "SPACE CANNON" in (text or "").upper():
        return bool(pattern.search((text or "").replace(m.group(0), "", 1)))
    return True


@dataclass
class MovementContext:
    """What the movement check needs to know about the mover and the board."""

    view: object
    player: str
    kb: object | None
    overrides: dict[str, dict] = field(default_factory=dict)
    gravity_drive: bool = False
    antimass: bool = False
    light_wave: bool = False
    # Cards whose effect on movement is not modelled, found from their text.
    enabling: list[str] = field(default_factory=list)  # the mover's: could allow a refused move
    restrictive: list[str] = field(default_factory=list)  # laws and opponents': could forbid an allowed move

    @property
    def unmodelled(self) -> list[str]:
        return sorted(set(self.enabling) | set(self.restrictive))

    @classmethod
    def build(cls, view, player: str, kb) -> "MovementContext":
        me = view.players[player]
        techs = {t.id for t in me.techs}
        used = set(view.turn.used_this_turn)
        ctx = cls(
            view=view,
            player=player,
            kb=kb,
            overrides=faction_unit_overrides(kb, me.faction, me.unit_upgrades),
            gravity_drive="tech.gravity_drive" in techs and f"{player}:tech.gravity_drive" not in used,
            antimass="tech.antimass_deflectors" in techs,
            light_wave="tech.light_wave_deflector" in techs,
        )
        ctx.enabling, ctx.restrictive = ctx._scan()
        return ctx

    def stats(self, unit: str) -> UnitStats:
        me = self.view.players[self.player]
        return stats_for(unit, me.unit_upgrades, self.overrides)

    def _text(self, rid: str) -> str:
        return getattr(self.kb.get(rid), "text", "") or ""

    def _faction_cards(self, player) -> set[str]:
        """A player's public cards: techs, relics, notes in play, faction abilities and units."""
        rec = self.kb.get(f"faction.{player.faction}")
        fstats = getattr(rec, "stats", {}) or {}
        return (
            {t.id for t in player.techs}
            | set(player.relics)
            | set(player.promissory_in_play)
            | {f"ability.{a}" for a in fstats.get("abilities") or []}
            | {f"unit.{u}" for u in fstats.get("units") or []}
        )

    def _scan(self) -> tuple[list[str], list[str]]:
        if self.kb is None:
            return [], []
        view, me = self.view, self.view.players[self.player]

        mine = self._faction_cards(me)
        frec = self.kb.get(f"faction.{me.faction}")
        for lid in (getattr(frec, "stats", {}) or {}).get("leaders") or []:
            kind = (getattr(self.kb.get(f"leader.{lid}"), "stats", {}) or {}).get("leader_type")
            if kind in ("agent", "commander", "hero") and getattr(me.leaders, kind) in ("ready", "unlocked"):
                mine.add(f"leader.{lid}")
        if view.viewer == self.player:
            mine |= set(view.hand.action_cards) | set(view.hand.promissory_notes)
        enabling = {rid for rid in mine - MODELLED if _affects_movement(self._text(rid))}

        restrictive = set()
        for law in view.laws:
            text = self._text(law.id)
            if _affects_movement(text, _RESTRICTIVE_TEXT):
                restrictive.add(law.id)
            elif _affects_movement(text):
                enabling.add(law.id)  # e.g. Wormhole Reconstruction adds adjacency
        for pid, p in view.players.items():
            if pid != self.player:
                restrictive |= {
                    rid for rid in self._faction_cards(p) if _affects_movement(self._text(rid), _RESTRICTIVE_TEXT)
                }
        return sorted(enabling), sorted(restrictive)


def _tile_info(kb):
    if kb is None:
        return None

    def lookup(tile_id: str):
        rec = kb.get(f"tile.{tile_id.lower()}")
        return getattr(rec, "stats", None)

    return lookup


def path_cost(mc: MovementContext, origin: str, dest: str, adjacency) -> tuple[int, int] | None:
    """Fewest systems entered (net of gravity rift bonuses) from origin to dest,
    and the number of gravity rift exits on that path. None if unreachable."""
    systems = {s.position: s for s in mc.view.map}

    def enterable(pos: str, final: bool) -> bool:
        s = systems.get(pos)
        if s is None:
            return False
        if s.anomaly == "supernova":
            return False
        if s.anomaly == "asteroid_field" and not mc.antimass:
            return False
        if final:
            return True
        if s.anomaly == "nebula":
            return False
        enemies = any(owner != mc.player and units for owner, units in s.space.items() if _has_ships(units))
        return mc.light_wave or not enemies

    best: dict[str, tuple[int, int]] = {origin: (0, 0)}
    queue: deque[str] = deque([origin])
    while queue:
        cur = queue.popleft()
        cost, rifts = best[cur]
        if cur == dest and cur != origin:
            continue
        out_of_rift = systems.get(cur) is not None and systems[cur].anomaly == "gravity_rift"
        step = 0 if out_of_rift else 1
        for nxt in adjacency.get(cur, ()):
            if not enterable(nxt, final=(nxt == dest)):
                continue
            cand = (cost + step, rifts + (1 if out_of_rift else 0))
            if nxt not in best or cand[0] < best[nxt][0]:
                best[nxt] = cand
                (queue.appendleft if step == 0 else queue.append)(nxt)
    return best.get(dest) if dest != origin else None


def _has_ships(units: dict) -> bool:
    return any(n > 0 and canonical(u) in SHIPS for u, n in units.items())


@dataclass
class MoveFinding:
    kind: str  # "violation" | "unchecked" | "warning"
    topic: str
    message: str


def check_moves(view, action, kb) -> list[MoveFinding]:
    """Check every move in a tactical action. ``action`` is a TacticalAction."""
    findings: list[MoveFinding] = []
    player, dest = action.player, action.activate
    mc = MovementContext.build(view, player, kb)

    def refuse(topic: str, msg: str) -> None:
        """A violation, unless one of the mover's unmodelled cards could allow it: then only unchecked."""
        if mc.enabling:
            findings.append(MoveFinding("unchecked", topic, f"{msg} (may be allowed by {', '.join(mc.enabling)})"))
        else:
            findings.append(MoveFinding("violation", topic, msg))

    # Off-grid systems (a Creuss home system, Mallice) connect only through
    # wormholes, which the adjacency covers. The Fracture's ingress and egress
    # links are not modelled.
    if any(p.startswith("frac") for p in [dest, *(m.from_ for m in action.moves)]):
        findings.append(MoveFinding("unchecked", "movement.range", "movement to or from The Fracture"))
        return findings
    adjacency = system_adjacency(view.map, _tile_info(kb))
    systems = {s.position: s for s in view.map}

    short_by_one: list[str] = []  # single ships that Gravity Drive could carry the last step
    rift_rolls = 0
    for mv in action.moves:
        origin = systems.get(mv.from_)
        if origin is None:
            continue
        ships = {u: n for u, n in mv.units.items() if canonical(u) in SHIPS and n > 0}
        cargo = {u: n for u, n in mv.units.items() if canonical(u) in GROUND and n > 0}
        independent = {u: n for u, n in ships.items() if canonical(u) == "fighter" and mc.stats(u).independent}
        carried = cargo | {u: n for u, n in ships.items() if canonical(u) == "fighter" and u not in independent}
        movers = {u: n for u, n in ships.items() if canonical(u) != "fighter"} | independent

        for u, n in mv.units.items():
            if n > 0 and mc.stats(u).is_structure:
                refuse("movement.range", f"{u} is a structure and cannot move.")
            elif n > 0 and canonical(u) not in SHIPS | GROUND:
                findings.append(MoveFinding("unchecked", "movement.range", f"movement of {u} (unit type not modelled)"))

        found = path_cost(mc, mv.from_, dest, adjacency)
        if found is None:
            target = systems.get(dest)
            if target is not None and target.anomaly == "supernova":
                refuse("movement.range", f"{dest} is a supernova: ships cannot move into it.")
            elif target is not None and target.anomaly == "asteroid_field" and not mc.antimass:
                refuse("movement.range", f"{dest} is an asteroid field: ships cannot move into it without Antimass Deflectors.")
            else:
                refuse(
                    "movement.range",
                    f"No legal path from {mv.from_} to {dest}: every route passes through other players' ships, "
                    "an asteroid field, a supernova or a nebula, or the systems are not connected.",
                )
            continue
        cost, rifts = found
        for u, n in movers.items():
            move = 1 if origin.anomaly == "nebula" else mc.stats(u).move
            if move >= cost:
                continue
            if move + 1 == cost and n == 1:
                short_by_one.append(f"1 {u} in {mv.from_} cannot reach {dest}: it needs {cost} move and has {move}.")
                continue
            refuse("movement.range", f"{n} {u} in {mv.from_} cannot reach {dest}: it needs {cost} move and has {move}.")
        rift_rolls += rifts * sum(movers.values())

        need = sum(carried.values())
        have = sum(mc.stats(u).capacity * n for u, n in movers.items())
        if need and not movers:
            refuse("transport.capacity", f"{', '.join(carried)} from {mv.from_} must be transported, and no ship moves with them.")
        elif need > have:
            refuse("transport.capacity", f"Ships moving from {mv.from_} can transport {have} fighters and ground forces, not {need}.")

    if short_by_one:
        covered = 1 if mc.gravity_drive else 0
        for msg in short_by_one[covered:]:
            note = " Gravity Drive covers only one ship." if mc.gravity_drive else ""
            refuse("movement.range", msg + note)
    if rift_rolls:
        findings.append(
            MoveFinding(
                "warning",
                "movement.gravity_rift",
                f"{rift_rolls} ship(s) leave a gravity rift: roll a die for each; on 1-3 that ship is removed (41.2).",
            )
        )
    if not any(f.kind == "violation" for f in findings):
        findings += _after_movement(mc, action, systems)
    if mc.restrictive and all(f.kind == "warning" for f in findings):
        findings.append(
            MoveFinding("unchecked", "movement.range", f"whether {', '.join(mc.restrictive)} forbid this move")
        )
    return findings


def _after_movement(mc: MovementContext, action, systems) -> list[MoveFinding]:
    """Capacity and fleet pool in the active system once everything has arrived."""
    out: list[MoveFinding] = []
    dest = systems.get(action.activate)
    if dest is None:
        return out
    arrived: dict[str, int] = dict(dest.space.get(mc.player, {}))
    for mv in action.moves:
        for u, n in mv.units.items():
            arrived[u] = arrived.get(u, 0) + n
    ships = {u: n for u, n in arrived.items() if canonical(u) in SHIPS and n > 0}
    capacity = sum(mc.stats(u).capacity * n for u, n in ships.items() if canonical(u) != "fighter")
    fighters = sum(n for u, n in ships.items() if canonical(u) == "fighter")
    ground = sum(n for u, n in arrived.items() if canonical(u) in GROUND)
    independent = any(mc.stats(u).independent for u in ships if canonical(u) == "fighter")

    excess = fighters + ground - capacity
    over_cap_fighters = 0
    if excess > 0:
        over_cap_fighters = min(excess, fighters) if independent else 0
        removed = excess - over_cap_fighters
        if removed:
            out.append(MoveFinding("warning", "capacity.excess",
                                   f"{removed} fighter(s)/ground force(s) in {action.activate} exceed capacity {capacity} and will be removed (16.3)."))
    non_fighters = sum(n for u, n in ships.items() if canonical(u) != "fighter") + over_cap_fighters
    pool = mc.view.players[mc.player].command_tokens.fleet
    if non_fighters > pool:
        out.append(MoveFinding("warning", "fleet_pool",
                               f"{non_fighters} ships count against fleet supply in {action.activate} but the fleet pool has {pool}; "
                               f"{non_fighters - pool} will be removed (37.3)."))
    return out
