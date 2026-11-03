from types import SimpleNamespace as Sys

from warlord.engine.geometry import (
    distances_from,
    from_axial,
    hex_distance,
    hex_neighbors,
    parse_position,
    system_adjacency,
    to_axial,
)

# Rows from AsyncTI4's positions/tileAdjacencies.properties (side order N, NE, SE, S, SW, NW).
ASYNC_ROWS = {
    "000": "101,102,103,104,105,106",
    "101": "201,202,102,000,106,212",
    "201": "301,302,202,101,212,318",
    "301": "401,402,302,201,318,424",
    "401": "501,502,402,301,424,530",
}


def test_matches_asyncti4_table():
    for pos, row in ASYNC_ROWS.items():
        assert hex_neighbors(pos) == row.split(",")


def test_round_trip_all_rings():
    for ring in range(0, 17):
        for index in range(1, 6 * ring + 1) if ring else [0]:
            pos = "000" if ring == 0 else f"{ring}{index:02d}"
            assert from_axial(*to_axial(pos)) == pos


def test_parse_and_distance():
    assert parse_position("1001") == (10, 1)
    assert parse_position("107") is None  # ring 1 has 6 positions
    assert parse_position("frac1") is None
    assert hex_distance("000", "318") == 3
    assert hex_distance("101", "104") == 2


def sys(pos, tile="1", wormholes=(), hyperlane=False):
    return Sys(position=pos, tile=tile, wormholes=list(wormholes), hyperlane=hyperlane)


def test_wormholes_connect_systems():
    adj = system_adjacency([sys("000"), sys("101"), sys("304", wormholes=["alpha"]), sys("310", wormholes=["alpha"]), sys("313", wormholes=["beta"])])
    assert "310" in adj["304"] and "304" in adj["310"]
    assert "313" not in adj["304"]
    assert "101" in adj["000"]


def test_hyperlanes_join_their_sides_and_are_not_nodes():
    # A hyperlane at 101 whose lane joins its N side (201) to its S side (000).
    m = [[0] * 6 for _ in range(6)]
    m[0][3] = m[3][0] = 1
    tiles = {"83a": {"hyperlane": True, "lanes": {"0": m}}}
    systems = [sys("000"), sys("101", tile="83a"), sys("201"), sys("202")]
    adj = system_adjacency(systems, tiles.get)
    assert "101" not in adj
    assert "201" in adj["000"]
    assert "202" not in adj["000"]  # 202 touches the hyperlane on a side with no lane
    assert distances_from("000", adj) == {"000": 0, "201": 1, "202": 2}


def test_hyperlane_rotation_suffix():
    m = [[0] * 6 for _ in range(6)]
    m[1][4] = m[4][1] = 1  # NE <-> SW
    tiles = {"83a": {"hyperlane": True, "lanes": {"0": [[0] * 6 for _ in range(6)], "60": m}}}
    systems = [sys("101", tile="83a60", hyperlane=True), sys("202"), sys("106")]
    adj = system_adjacency(systems, tiles.get)
    assert "106" in adj["202"]
