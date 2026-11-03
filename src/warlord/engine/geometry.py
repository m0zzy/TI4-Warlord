"""Map geometry: AsyncTI4 ring positions, hex neighbors and system adjacency.

Positions use AsyncTI4's numbering: ``000`` is the center, and ``RNN`` is
ring ``R``, index ``NN`` (``RRNN`` from ring 10). Index 01 is straight up (north), and the indexes
run clockwise. Hexes are flat-topped, so every hex has a neighbor directly
north and south. Neighbor lists come back in side order:
N, NE, SE, S, SW, NW (sides 0-5). That is the order AsyncTI4's
``tileAdjacencies.properties`` uses, and the side order of its hyperlane
matrices.

Adjacency of two systems (per the rules) comes from three things, and is
derived, never stored:

* physical adjacency: neighboring hexes, except across a hyperlane tile;
* wormholes: systems with a matching wormhole type are adjacent;
* hyperlanes: a hyperlane tile links the systems on the sides its lanes join.
  The hyperlane itself is not a system units can stop in.

Effects that change adjacency (cards, abilities, TE fractures, Mallice being
inactive, and so on) are not modelled here. Callers must treat them as unchecked.
"""

from __future__ import annotations

from collections import deque
from typing import Callable, Iterable, Mapping

# Flat-top axial directions in side order N, NE, SE, S, SW, NW.
DIRECTIONS = [(0, -1), (1, -1), (1, 0), (0, 1), (-1, 1), (-1, 0)]
# A ring is walked from its north hex clockwise: SE, S, SW, NW, N, NE.
_RING_WALK = [2, 3, 4, 5, 0, 1]

# Two-digit indexes run out past ring 16 (6 * 17 > 99).
MAX_RING = 16

TileInfo = Callable[[str], Mapping | None]  # tile id -> stats (wormholes, hyperlane, lanes)


def parse_position(position: str) -> tuple[int, int] | None:
    """``"000"`` -> (0, 0); ``"212"`` -> (2, 12); ``"1001"`` -> (10, 1). Others -> None."""
    if position == "000":
        return 0, 0
    if len(position) < 3 or not position.isdigit() or position[0] == "0":
        return None
    ring, index = int(position[:-2]), int(position[-2:])
    if not 1 <= ring <= MAX_RING or not 1 <= index <= 6 * ring:
        return None
    return ring, index


def position_name(ring: int, index: int) -> str:
    return "000" if ring == 0 else f"{ring}{index:02d}"


def to_axial(position: str) -> tuple[int, int] | None:
    parsed = parse_position(position)
    if parsed is None:
        return None
    ring, index = parsed
    if ring == 0:
        return 0, 0
    q, r = 0, -ring
    steps = index - 1
    for side in _RING_WALK:
        dq, dr = DIRECTIONS[side]
        n = min(steps, ring)
        q, r = q + dq * n, r + dr * n
        steps -= n
        if steps == 0:
            break
    return q, r


def from_axial(q: int, r: int) -> str | None:
    ring = max(abs(q), abs(r), abs(q + r))
    if ring == 0:
        return "000"
    if ring > MAX_RING:
        return None
    # Walk the ring until we reach (q, r).
    cq, cr, index = 0, -ring, 1
    for side in _RING_WALK:
        dq, dr = DIRECTIONS[side]
        for _ in range(ring):
            if (cq, cr) == (q, r):
                return position_name(ring, index)
            cq, cr, index = cq + dq, cr + dr, index + 1
    return None


def hex_neighbors(position: str) -> list[str | None]:
    """The six neighboring positions in side order (None off the numbering)."""
    ax = to_axial(position)
    if ax is None:
        return [None] * 6
    q, r = ax
    return [from_axial(q + dq, r + dr) for dq, dr in DIRECTIONS]


def hex_distance(a: str, b: str) -> int | None:
    pa, pb = to_axial(a), to_axial(b)
    if pa is None or pb is None:
        return None
    dq, dr = pa[0] - pb[0], pa[1] - pb[1]
    return max(abs(dq), abs(dr), abs(dq + dr))


def _lane_matrix(tile_id: str, info: Mapping) -> list[list[int]] | None:
    """Pick the rotation's lane matrix: tile "83a60" -> lanes["60"]."""
    lanes = info.get("lanes") or {}
    rotation = tile_id[len(_base_tile(tile_id)):] or "0"
    return lanes.get(rotation)


def system_adjacency(
    systems: Iterable,  # objects with .position, .tile, .wormholes, .hyperlane
    tile_info: TileInfo | None = None,
) -> dict[str, set[str]]:
    """Adjacency between the systems on a map (hyperlane tiles excluded as nodes)."""
    systems = list(systems)
    by_pos = {s.position: s for s in systems}

    def info(s) -> Mapping:
        if tile_info is None:
            return {}
        return tile_info(s.tile) or tile_info(_base_tile(s.tile)) or {}

    def is_lane(s) -> bool:
        return bool(getattr(s, "hyperlane", False) or info(s).get("hyperlane"))

    nodes = {p for p, s in by_pos.items() if not is_lane(s)}
    adj: dict[str, set[str]] = {p: set() for p in nodes}

    for p in nodes:
        for n in hex_neighbors(p):
            if n in nodes:
                adj[p].add(n)

    # Hyperlanes: join the systems on connected sides.
    for p, s in by_pos.items():
        if not is_lane(s):
            continue
        matrix = _lane_matrix(s.tile, info(s))
        if matrix is None:
            continue
        sides = hex_neighbors(p)
        for i in range(6):
            for j in range(6):
                if i != j and matrix[i][j]:
                    a, b = sides[i], sides[j]
                    if a in nodes and b in nodes:
                        adj[a].add(b)
                        adj[b].add(a)

    # Wormholes: matching types are adjacent.
    holes: dict[str, list[str]] = {}
    for p in nodes:
        s = by_pos[p]
        kinds = set(w.lower() for w in (getattr(s, "wormholes", None) or info(s).get("wormholes") or []))
        for w in kinds:
            holes.setdefault(w, []).append(p)
    for members in holes.values():
        for a in members:
            for b in members:
                if a != b:
                    adj[a].add(b)

    return adj


def _base_tile(tile_id: str) -> str:
    """Strip a rotation suffix: "83a60" -> "83a"."""
    i = len(tile_id)
    while i > 0 and tile_id[i - 1].isdigit():
        i -= 1
    head = tile_id[:i]
    return head if head and head[-1].isalpha() and any(c.isdigit() for c in head) else tile_id


def distances_from(start: str, adjacency: Mapping[str, set[str]], max_steps: int | None = None) -> dict[str, int]:
    """Breadth-first move counts from ``start`` over the adjacency graph."""
    dist = {start: 0}
    queue = deque([start])
    while queue:
        cur = queue.popleft()
        if max_steps is not None and dist[cur] >= max_steps:
            continue
        for n in adjacency.get(cur, ()):
            if n not in dist:
                dist[n] = dist[cur] + 1
                queue.append(n)
    return dist
