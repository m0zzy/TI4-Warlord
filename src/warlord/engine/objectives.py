"""Public objective progress: what a player could score now, and how far off they are.

Covers the 40 PoK public objectives (stage I and II) from the state alone:
* control: planets, traits, specialties, attachments, home systems;
* presence: structures, ships, units on the board's edge or in special systems;
* technology: colors and unit upgrades;
* spending: resources, influence and trade goods, where each planet pays its
  resources or its influence (not both), and trade goods fill either.

Each result says ``can_score``, ``scored``, or ``not_yet`` with the progress.
Scoring happens in the status phase (one public and one secret per player),
so "can score" means "would qualify if scored now". Faction abilities that
change these counts are not modelled.
"""

from __future__ import annotations

from collections import Counter

from warlord.engine.geometry import hex_neighbors, parse_position, system_adjacency
from warlord.engine.movement import SHIPS, _tile_info
from warlord.engine.units import canonical

STRUCTURES = {"pds", "space_dock"}
MECATOL = {"mr", "mrte", "mecatol_rex", "mecatolrex"}
COLORS = {"propulsion", "biotic", "warfare", "cybernetic"}


class _Board:
    """Facts about one player's position, computed once."""

    def __init__(self, view, kb, player: str):
        self.view, self.kb, self.player = view, kb, player
        self.me = view.players[player]
        self.home_positions = {pid: self._home(pid) for pid in view.players}
        self.home = self.home_positions[player]
        self.planets = [(s, name, p) for s in view.map for name, p in s.planets.items() if p.controller == player]
        self.adjacency = system_adjacency(view.map, _tile_info(kb))

    def stat(self, rid: str, key: str, default=None):
        rec = self.kb.get(rid) if self.kb is not None else None
        return (getattr(rec, "stats", {}) or {}).get(key, default)

    def _home(self, pid: str) -> str | None:
        """The system holding the faction's home planets; else its listed home tile.

        Home planets come first: AsyncTI4 lists the Creuss Gate as the Ghosts of
        Creuss' home tile, but their home planet sits in the off-board system.
        """
        p = self.view.players[pid]
        homes = set(self.stat(f"faction.{p.faction}", "home_planets") or [])
        for s in self.view.map:
            if homes & set(s.planets):
                return s.position
        tile = self.stat(f"faction.{p.faction}", "home_system")
        for s in self.view.map:
            if tile is not None and s.tile == str(tile):
                return s.position
        return None

    def has_units(self, s) -> bool:
        if any(n for n in s.space.get(self.player, {}).values()):
            return True
        return any(any(n for n in p.units.get(self.player, {}).values()) for p in s.planets.values())

    def on_edge(self, s) -> bool:
        """On the board's edge: a ring position with a side that touches no system."""
        if parse_position(s.position) is None:
            return False  # off-grid systems (The Fracture, Creuss' home) are not on the edge
        positions = {x.position for x in self.view.map}
        return any(n is None or n not in positions for n in hex_neighbors(s.position))

    def special(self, s) -> bool:
        if s.anomaly:
            return True
        return any(name.lower() in MECATOL or self.stat(f"planet.{name.lower()}", "legendary") for name in s.planets)

    def structures(self) -> list[tuple[str, str]]:
        out = []
        for s in self.view.map:
            for name, p in s.planets.items():
                for u, n in p.units.get(self.player, {}).items():
                    if canonical(u) in STRUCTURES:
                        out += [(s.position, name)] * n
            for u, n in s.space.get(self.player, {}).items():
                if canonical(u) in STRUCTURES:  # Saar's floating factory
                    out += [(s.position, "space")] * n
        return out

    def tech_colors(self) -> Counter:
        c: Counter = Counter()
        for t in self.me.techs:
            for kind in self.stat(t.id, "tech_types", []) or []:
                if kind.lower() in COLORS:
                    c[kind.lower()] += 1
        return c

    def unit_upgrades(self) -> int:
        return sum(1 for t in self.me.techs if "UNITUPGRADE" in (self.stat(t.id, "tech_types", []) or []))

    def neighbors(self) -> set[str]:
        mine = {s.position for s in self.view.map if self.has_units(s) or any(p.controller == self.player for p in s.planets.values())}
        near = mine | {n for pos in mine for n in self.adjacency.get(pos, ())}
        out = set()
        for s in self.view.map:
            if s.position not in near:
                continue
            for pid in self.view.players:
                if pid == self.player:
                    continue
                if any(s.space.get(pid, {}).values()) or any(p.controller == pid or p.units.get(pid) for p in s.planets.values()):
                    out.add(pid)
        return out

    def can_spend(self, resources: int = 0, influence: int = 0, trade_goods: int = 0) -> tuple[bool, str]:
        tg = self.me.trade_goods
        if tg < trade_goods:
            return False, f"has {tg} of {trade_goods} trade goods"
        spare = tg - trade_goods
        ready = [(int(self.stat(f"planet.{n.lower()}", "resources", 0) or 0), int(self.stat(f"planet.{n.lower()}", "influence", 0) or 0))
                 for _, n, p in self.planets if not p.exhausted]
        best = {(0, 0)}
        for r, i in ready:  # each ready planet pays resources, or influence, or nothing
            best = {(min(a + r, resources), b) for a, b in best} | {(a, min(b + i, influence)) for a, b in best} | best
        gap = min(max(0, resources - a) + max(0, influence - b) for a, b in best)
        if gap <= spare:
            return True, "can pay"
        return False, f"short by {gap - spare} (resources/influence, after trade goods)"


def _count(have: int, need: int, what: str) -> tuple[bool, str]:
    return have >= need, f"{have} of {need} {what}"


def _checkers():
    def control_nonhome(b, need):
        return _count(sum(1 for s, _, _ in b.planets if s.position not in b.home_positions.values()), need, "planets in non-home systems")

    def same_trait(b, need):
        traits = Counter(t for _, n, _ in b.planets for t in b.stat(f"planet.{n.lower()}", "traits", []) or [])
        best = max(traits.values(), default=0)
        return _count(best, need, "planets sharing a trait")

    def specialties(b, need):
        return _count(sum(1 for _, n, _ in b.planets if b.stat(f"planet.{n.lower()}", "tech_specialties")), need,
                      "planets with technology specialties")

    def attachments(b, need):
        return _count(sum(1 for _, _, p in b.planets if p.attachments), need, "planets with attachments")

    def structures(b, need):
        return _count(len(b.structures()), need, "structures")

    def structure_planets(b, need):
        planets = {(pos, n) for pos, n in b.structures() if pos != b.home and n != "space"}
        return _count(len(planets), need, "planets with structures outside your home system")

    def empty_systems(b, need):
        return _count(sum(1 for s in b.view.map if not s.planets and not s.hyperlane and b.has_units(s)), need,
                      "systems without planets that hold your units")

    def edge(b, need):
        return _count(sum(1 for s in b.view.map if b.on_edge(s) and s.position != b.home and b.has_units(s)), need,
                      "edge systems (not your home) with your units")

    def special_systems(b, need):
        return _count(sum(1 for s in b.view.map if b.special(s) and b.has_units(s)), need,
                      "systems with legendary planets, Mecatol Rex or anomalies that hold your units")

    def fleet(b, need):
        best = max((sum(n for u, n in s.space.get(b.player, {}).items() if canonical(u) in SHIPS - {"fighter"})
                    for s in b.view.map), default=0)
        return _count(best, need, "non-fighter ships in one system")

    def colors(b, per, n_colors):
        c = b.tech_colors()
        ok = sum(1 for v in c.values() if v >= per)
        return _count(ok, n_colors, f"colors with {per}+ technologies")

    def upgrades(b, need):
        return _count(b.unit_upgrades(), need, "unit upgrade technologies")

    def flagship_or_sun(b, where_ok):
        for s in b.view.map:
            if any(canonical(u) in ("flagship", "war_sun") and n for u, n in s.space.get(b.player, {}).items()):
                if where_ok(b, s):
                    return True, f"in {s.position}"
        return False, "no flagship or war sun where needed"

    def near_mecatol(b, need):
        rex = next((s.position for s in b.view.map if any(n.lower() in MECATOL for n in s.planets)), None)
        if rex is None:
            return False, "Mecatol Rex is not on the map"
        near = b.adjacency.get(rex, set())
        return _count(sum(1 for pos in near if any(canonical(u) in SHIPS for u in b.view.system(pos).space.get(b.player, {}))),
                      need, "systems next to Mecatol Rex with your ships")

    def tokens(b, need):
        have = b.me.command_tokens.tactic + b.me.command_tokens.strategy
        return _count(have, need, "tokens in tactic and strategy pools")

    def push(b, need):
        mine = len(b.planets)
        fewer = sum(1 for pid in b.neighbors() if sum(1 for s in b.view.map for p in s.planets.values() if p.controller == pid) < mine)
        return _count(fewer, need, "neighbors with fewer planets than you")

    def conquer(b, need):
        others = {pos for pid, pos in b.home_positions.items() if pid != b.player and pos}
        return _count(sum(1 for s, _, _ in b.planets if s.position in others), need, "planets in other players' home systems")

    def distant(b, need):
        homes = {pid: pos for pid, pos in b.home_positions.items() if pid != b.player and pos}
        used = set()
        for s, name, _ in b.planets:
            for pid, pos in homes.items():
                if pid not in used and (s.position == pos or s.position in b.adjacency.get(pos, ())):
                    used.add(pid)
                    break
        return _count(len(used), need, "planets in or next to different players' home systems")

    def flag_supreme(b, s):
        others = {pos for pid, pos in b.home_positions.items() if pid != b.player}
        return s.position in others or any(n.lower() in MECATOL for n in s.planets)

    return {
        "amass_wealth": lambda b: b.can_spend(3, 3, 3),
        "build_defenses": lambda b: structures(b, 4),
        "corner": lambda b: same_trait(b, 4),
        "deep_space": lambda b: empty_systems(b, 3),
        "develop": lambda b: upgrades(b, 2),
        "diversify": lambda b: colors(b, 2, 2),
        "engineer_marvel": lambda b: flagship_or_sun(b, lambda b, s: True),
        "expand_borders": lambda b: control_nonhome(b, 6),
        "infrastructure": lambda b: structure_planets(b, 3),
        "intimidate": lambda b: near_mecatol(b, 2),
        "lead": lambda b: tokens(b, 3),
        "lost_outposts": lambda b: attachments(b, 2),
        "make_history": lambda b: special_systems(b, 2),
        "monument": lambda b: b.can_spend(resources=8),
        "outer_rim": lambda b: edge(b, 3),
        "push_boundaries": lambda b: push(b, 2),
        "raise_fleet": lambda b: fleet(b, 5),
        "research_outposts": lambda b: specialties(b, 3),
        "sway_council": lambda b: b.can_spend(influence=8),
        "trade_routes": lambda b: b.can_spend(trade_goods=5),
        "ancient_monuments": lambda b: attachments(b, 3),
        "become_legend": lambda b: special_systems(b, 4),
        "brain_trust": lambda b: specialties(b, 5),
        "centralize_trade": lambda b: b.can_spend(trade_goods=10),
        "command_armada": lambda b: fleet(b, 8),
        "conquer": lambda b: conquer(b, 1),
        "control_borderlands": lambda b: edge(b, 5),
        "distant_lands": lambda b: distant(b, 2),
        "galvanize": lambda b: tokens(b, 6),
        "golden_age": lambda b: b.can_spend(resources=16),
        "manipulate_law": lambda b: b.can_spend(influence=16),
        "massive_cities": lambda b: structures(b, 7),
        "master_science": lambda b: colors(b, 2, 4),
        "protect_border": lambda b: structure_planets(b, 5),
        "revolutionize": lambda b: upgrades(b, 3),
        "subdue": lambda b: control_nonhome(b, 11),
        "supremacy": lambda b: flagship_or_sun(b, flag_supreme),
        "unify_colonies": lambda b: same_trait(b, 6),
        "vast_reserves": lambda b: b.can_spend(6, 6, 6),
        "vast_territories": lambda b: empty_systems(b, 5),
    }


CHECKERS = _checkers()


def objective_progress(view, kb, player: str, objective_ids=None) -> list[dict]:
    board = _Board(view, kb, player)
    me = view.players[player]
    ids = list(objective_ids if objective_ids is not None else view.objectives.public_revealed)
    out = []
    for oid in ids:
        rec = kb.get(oid) if kb is not None else None
        aliases = list(getattr(rec, "aliases", []) or [])
        alias = next((a for a in aliases if a in CHECKERS), None)
        entry = {"id": oid, "name": getattr(rec, "name", oid), "points": (getattr(rec, "stats", {}) or {}).get("points")}
        if oid in me.scored:
            entry["status"] = "scored"
        elif alias is None:
            entry.update(status="unknown", detail="this objective is not modelled")
        else:
            ok, detail = CHECKERS[alias](board)
            entry.update(status="can_score" if ok else "not_yet", detail=detail)
        out.append(entry)
    if board.home is None:
        for e in out:
            e.setdefault("note", "home system not found; home-system counts may be off")
    return out
