"""combat_sim: Monte Carlo space combat.

Sequence modelled (one tactical action, one system):

1. Space cannon offense - the defender's space cannon units (``pds`` in the
   defender's units, or ``space_cannon_units`` for adjacent PDS II) fire at the
   attacker's ships. The attacker assigns those hits.
2. Anti-fighter barrage - first round only, each side's AFB hits only the
   other side's fighters; excess hits are lost.
3. Combat rounds until one side (or both) has no ships left.

Each player assigns hits to their own units: Sustain Damage on undamaged
sustain units first (configurable), then the cheapest units die first.
Retreats, faction abilities and action cards are not modelled; pass them in
as die modifiers or unit overrides where they reduce to that.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from warlord.engine.units import LOSS_ORDER, UnitStats, canonical, stats_for


@dataclass
class Side:
    units: dict[str, int]
    upgrades: list[str] = field(default_factory=list)
    damaged: dict[str, int] = field(default_factory=dict)
    combat_modifier: int = 0  # added to each combat die (e.g. +1 from an ability)
    afb_modifier: int = 0
    space_cannon_modifier: int = 0  # applied to this side's space cannon rolls
    overrides: dict[str, dict] = field(default_factory=dict)  # unit -> stat overrides
    space_cannon_units: dict[str, int] = field(default_factory=dict)  # extra shooters, e.g. adjacent PDS II
    sustain_first: bool = True

    @classmethod
    def from_dict(cls, d: dict) -> "Side":
        known = {f for f in cls.__dataclass_fields__}
        unknown = set(d) - known
        if unknown:
            raise ValueError(f"unknown side fields: {sorted(unknown)}")
        data = dict(d)
        data["units"] = {canonical(k): int(v) for k, v in d.get("units", {}).items() if int(v) > 0}
        data["damaged"] = {canonical(k): int(v) for k, v in d.get("damaged", {}).items()}
        data["space_cannon_units"] = {
            canonical(k): int(v) for k, v in d.get("space_cannon_units", {}).items()
        }
        return cls(**data)

    def stats(self, unit: str) -> UnitStats:
        return stats_for(unit, self.upgrades, self.overrides)


class _Fleet:
    """Mutable per-trial fleet: unit -> [healthy, damaged]."""

    __slots__ = ("side", "healthy", "hurt")

    def __init__(self, side: Side):
        self.side = side
        self.healthy: dict[str, int] = {}
        self.hurt: dict[str, int] = {}
        for u, n in side.units.items():
            if not side.stats(u).is_ship:
                continue
            d = min(side.damaged.get(u, 0), n)
            self.healthy[u] = n - d
            self.hurt[u] = d

    def ships(self) -> int:
        return sum(self.healthy.values()) + sum(self.hurt.values())

    def count(self, u: str) -> int:
        return self.healthy.get(u, 0) + self.hurt.get(u, 0)

    def roll(self, rng: random.Random, kind: str, modifier: int) -> int:
        hits = 0
        for u in set(self.healthy) | set(self.hurt):
            n = self.count(u)
            if not n:
                continue
            st = self.side.stats(u)
            value = getattr(st, kind)
            dice = st.combat_dice if kind == "combat" else getattr(st, f"{kind}_dice")
            if value is None or not dice:
                continue
            for _ in range(n * dice):
                if rng.randint(1, 10) + modifier >= value:
                    hits += 1
        return hits

    def assign(self, hits: int) -> None:
        while hits > 0 and self.ships():
            if self.side.sustain_first:
                target = next(
                    (u for u in reversed(LOSS_ORDER) if self.healthy.get(u) and self.side.stats(u).sustain),
                    None,
                )
                if target is None:
                    target = next(
                        (u for u in self.healthy if self.healthy[u] and self.side.stats(u).sustain), None
                    )
                if target is not None:
                    self.healthy[target] -= 1
                    self.hurt[target] = self.hurt.get(target, 0) + 1
                    hits -= 1
                    continue
            self._destroy_cheapest()
            hits -= 1

    def assign_fighters(self, hits: int) -> None:
        for pool in (self.healthy, self.hurt):
            k = min(hits, pool.get("fighter", 0))
            if k:
                pool["fighter"] -= k
                hits -= k

    def _destroy_cheapest(self) -> None:
        order = LOSS_ORDER + sorted(u for u in set(self.healthy) | set(self.hurt) if u not in LOSS_ORDER)
        for u in order:
            # Lose damaged copies of a type before healthy ones: same unit, no sustain left.
            for pool in (self.hurt, self.healthy):
                if pool.get(u):
                    pool[u] -= 1
                    return

    def survivors(self) -> dict[str, int]:
        return {u: self.count(u) for u in set(self.healthy) | set(self.hurt) if self.count(u)}


def _space_cannon_shooters(side: Side) -> _Fleet:
    f = _Fleet.__new__(_Fleet)
    f.side = side
    shooters = {u: n for u, n in side.units.items() if side.stats(u).space_cannon is not None}
    for u, n in side.space_cannon_units.items():
        shooters[u] = shooters.get(u, 0) + n
    f.healthy, f.hurt = shooters, {}
    return f


def hit_points(side: Side) -> int:
    hp = 0
    for u, n in side.units.items():
        st = side.stats(u)
        if not st.is_ship:
            continue
        d = min(side.damaged.get(u, 0), n)
        hp += n + ((n - d) if st.sustain else 0)
    return hp


def expected_hits_per_round(side: Side, modifier: int | None = None) -> float:
    mod = side.combat_modifier if modifier is None else modifier
    total = 0.0
    for u, n in side.units.items():
        st = side.stats(u)
        if not st.is_ship or st.combat is None:
            continue
        p = max(0, min(10, 11 - (st.combat - mod))) / 10
        total += n * st.combat_dice * p
    return total


def combat_sim(
    attacker: dict | Side,
    defender: dict | Side,
    *,
    trials: int = 5000,
    space_cannon: bool = True,
    afb: bool = True,
    max_rounds: int = 30,
    seed: int | None = None,
) -> dict:
    a = attacker if isinstance(attacker, Side) else Side.from_dict(attacker)
    d = defender if isinstance(defender, Side) else Side.from_dict(defender)
    trials = max(1, min(int(trials), 50_000))
    rng = random.Random(seed)

    wins = draws = losses = 0
    rounds_total = 0
    surv_a: dict[str, float] = {}
    surv_d: dict[str, float] = {}

    for _ in range(trials):
        fa, fd = _Fleet(a), _Fleet(d)
        if space_cannon:
            fa.assign(_space_cannon_shooters(d).roll(rng, "space_cannon", d.space_cannon_modifier))

        rounds = 0
        while fa.ships() and fd.ships() and rounds < max_rounds:
            if rounds == 0 and afb:
                ha = fa.roll(rng, "afb", a.afb_modifier)
                hd = fd.roll(rng, "afb", d.afb_modifier)
                fd.assign_fighters(ha)
                fa.assign_fighters(hd)
                if not (fa.ships() and fd.ships()):
                    break
            ha = fa.roll(rng, "combat", a.combat_modifier)
            hd = fd.roll(rng, "combat", d.combat_modifier)
            fd.assign(ha)
            fa.assign(hd)
            rounds += 1
        rounds_total += rounds

        a_alive, d_alive = fa.ships() > 0, fd.ships() > 0
        if a_alive and not d_alive:
            wins += 1
        elif d_alive and not a_alive:
            losses += 1
        else:
            draws += 1  # mutual destruction, or max_rounds reached
        for u, n in fa.survivors().items():
            surv_a[u] = surv_a.get(u, 0) + n
        for u, n in fd.survivors().items():
            surv_d[u] = surv_d.get(u, 0) + n

    def avg(m: dict[str, float]) -> dict[str, float]:
        return {u: round(v / trials, 3) for u, v in sorted(m.items())}

    return {
        "trials": trials,
        "attacker_win": round(wins / trials, 4),
        "draw": round(draws / trials, 4),
        "defender_win": round(losses / trials, 4),
        "expected_rounds": round(rounds_total / trials, 2),
        "expected_survivors": {"attacker": avg(surv_a), "defender": avg(surv_d)},
        "hits_needed": {"to_destroy_attacker": hit_points(a), "to_destroy_defender": hit_points(d)},
        "expected_hits_per_round": {
            "attacker": round(expected_hits_per_round(a), 3),
            "defender": round(expected_hits_per_round(d), 3),
        },
        "modelled": [
            m
            for m, on in (
                ("space cannon offense", space_cannon),
                ("anti-fighter barrage", afb),
                ("sustain damage", True),
                ("combat rounds to elimination", True),
            )
            if on
        ],
        "not_modelled": [
            "retreats",
            "faction abilities and flagship text beyond stat overrides",
            "action cards and promissory notes beyond die modifiers",
            "ground combat and invasion",
        ],
    }
