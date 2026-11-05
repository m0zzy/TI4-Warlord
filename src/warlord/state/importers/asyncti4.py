"""Import AsyncTI4 games into GameState.

Two public sources (see docs/design.md, Decisions):

* ``from_web_data``: a live game's ``/api/public/game/{name}/web-data`` JSON.
  It holds public information only: hands are counts. The result marks
  every hand as unknown (``PrivateHoldings.known = False``), and a player can
  add their own cards with ``set_hand``.
* ``from_save``: a completed game's save file from the public export
  (``/api/public/games/events``), optionally with its event log. A save holds
  everything, hands included, so the result is a full omniscient state for
  the example datasets.

Privacy: seats are keyed by faction (``"ghost"``), never by Discord user.
Discord ids, usernames, notepads, channel ids and user names inside events
are not imported.

Card ids are mapped to knowledge-base ids through the AsyncTI4 aliases that
the component importer recorded. Anything unmapped is kept as
``asyncti4:<alias>`` and counted in the import report.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from warlord.kb.records import ComponentCard
from warlord.kb.store import KnowledgeBase
from warlord.ruleset import POK_RULESET, THUNDERS_EDGE_RULESET, Ruleset
from warlord.state.schema import (
    CommandTokens,
    Commodities,
    Discards,
    GameState,
    HeldStrategyCard,
    HiddenCounts,
    LawInPlay,
    Leaders,
    LogEvent,
    Meta,
    ObjectivesState,
    OwnedTech,
    PlanetState,
    PlayerState,
    PrivateHoldings,
    ScoredObjective,
    SystemState,
    TurnState,
)

# AsyncTI4 unit codes -> our unit names (engine.units). Others pass through.
UNIT_CODES = {
    "ca": "cruiser",
    "cv": "carrier",
    "dd": "destroyer",
    "dn": "dreadnought",
    "ff": "fighter",
    "fs": "flagship",
    "ws": "war_sun",
    "gf": "infantry",
    "mf": "mech",
    "pd": "pds",
    "sd": "space_dock",
}

PHASES = {
    "strategy": "strategy",
    "action": "action",
    "status": "status",
    "statusscoring": "status",
    "agenda": "agenda",
    "agendawaiting": "agenda",
    "agendavoting": "agenda",
    "finished": "game_over",
    "gameend": "game_over",
}

NEUTRAL_COLORS = {"abr"}  # Thunder's Edge neutral units ("aberration")
NEUTRAL = "neutral"

_COLORS_FILE = Path(__file__).resolve().parents[1].parent / "data" / "asyncti4_colors.json"


def load_colors(path: str | Path = _COLORS_FILE) -> dict[str, str]:
    """AsyncTI4 color alias -> color name (shipped with the package)."""
    p = Path(path)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8")).get("colors", {})


# --------------------------------------------------------------- id mapping


class AliasIndex:
    """(component type, AsyncTI4 alias) -> knowledge-base id."""

    def __init__(self, kb: KnowledgeBase | None, colors: Iterable[str] = ()):
        self.kb = kb
        self.colors = {c.lower() for c in colors}
        self._by: dict[tuple[str, str], str] = {}
        self.unmapped: Counter = Counter()
        if kb is None:
            return
        for rec in kb.records.values():
            if not isinstance(rec, ComponentCard):
                continue
            ref = rec.source_ref or ""
            aliases = set(rec.aliases)
            if ref.startswith("asyncti4:"):
                aliases.add(ref.rsplit(":", 1)[-1])
            for a in aliases:
                self._by.setdefault((rec.type, a.lower()), rec.id)

    def get(self, card_type: str, alias: str | None) -> str | None:
        if not alias:
            return None
        found = self._by.get((card_type, alias.lower()))
        if found is None and card_type == "promissory_note":
            # Generic notes are stored per color: "teal_sftt" is "<color>_sftt".
            color, _, code = alias.lower().partition("_")
            if code and (not self.colors or color in self.colors):
                found = self._by.get((card_type, f"<color>_{code}"))
        if found is None:
            self.unmapped[card_type] += 1
            return f"asyncti4:{alias}"
        return found

    def many(self, card_type: str, aliases: Iterable[str]) -> list[str]:
        return [x for x in (self.get(card_type, a) for a in aliases) if x]

    def stats(self, card_id: str) -> dict:
        rec = self.kb.get(card_id) if self.kb else None
        return getattr(rec, "stats", {}) or {}


@dataclass
class ImportResult:
    state: GameState
    unmapped: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def _tile_stats(index: AliasIndex, tile_id: str) -> dict:
    if index.kb is None:
        return {}
    rec = index.kb.get(f"tile.{tile_id.lower()}")
    if rec is None:
        base = re.sub(r"\d+$", "", tile_id) if re.search(r"[a-z]\d+$", tile_id) else tile_id
        rec = index.kb.get(f"tile.{base.lower()}")
    return getattr(rec, "stats", {}) or {}


def _system(position: str, tile: str, index: AliasIndex) -> SystemState:
    st = _tile_stats(index, tile)
    anomalies = st.get("anomalies") or []
    return SystemState(
        position=position,
        tile=tile,
        wormholes=list(st.get("wormholes") or []),
        anomaly=anomalies[0] if anomalies else None,
        hyperlane=bool(st.get("hyperlane")),
    )


_WORMHOLE_TYPES = ("alpha", "beta", "gamma", "delta", "epsilon", "eta", "zeta", "iota", "kappa", "theta")


def wormhole_from_token(token_id: str) -> str | None:
    """Wormhole tokens placed during play: "creussalpha" -> "alpha", "gamma" -> "gamma"."""
    t = token_id.lower().removeprefix("token_").removesuffix(".png")
    for w in _WORMHOLE_TYPES:
        if t == w or t.endswith(w) or t.startswith(f"{w}_") or t == f"wormhole{w}":
            return w
    return None


def _add_wormhole(system: SystemState, token_id: str) -> None:
    w = wormhole_from_token(token_id)
    if w and w not in system.wormholes:
        system.wormholes.append(w)


def _upgrades(units_owned: Iterable[str]) -> list[str]:
    out = []
    for u in units_owned:
        m = re.fullmatch(r"([a-z_]+?)2", u)
        if m:
            out.append(f"{m.group(1)}_2")
    return out


def _leaders(entries: Iterable[dict]) -> Leaders:
    status: dict[str, str] = {"agent": "purged", "commander": "locked", "hero": "purged"}
    seen = set()
    for e in entries:
        kind = e.get("type")
        if kind not in status:
            continue
        seen.add(kind)
        if e.get("locked"):
            status[kind] = "locked"
        elif e.get("exhausted"):
            status[kind] = "exhausted"
        else:
            status[kind] = "ready" if kind == "agent" else "unlocked"
    if "agent" not in seen:
        status["agent"] = "purged"
    return Leaders(**status)  # type: ignore[arg-type]


def _ruleset_from_decks(decks: dict[str, str], thunders_edge: bool) -> Ruleset:
    base = THUNDERS_EDGE_RULESET if thunders_edge else POK_RULESET
    mapping = dict(base.decks)
    for our_type, key in (
        ("action_card", "ac_deck_id"),
        ("technology", "tech_deck_id"),
        ("relic", "relic_deck_id"),
        ("agenda", "agenda_deck_id"),
        ("secret_objective", "so_deck_id"),
        ("exploration", "exploration_deck_id"),
    ):
        if decks.get(key):
            mapping[our_type] = f"deck.{decks[key]}"
    if decks.get("stage_1_public_deck_id") or decks.get("stage_2_public_deck_id"):
        mapping["objective"] = [
            f"deck.{decks[k]}" for k in ("stage_1_public_deck_id", "stage_2_public_deck_id") if decks.get(k)
        ]
    if decks.get("strategy_card_set"):
        mapping["strategy_card"] = f"deck.sc_set.{decks['strategy_card_set']}"
    return base.model_copy(update={"decks": mapping})


def _sc_by_initiative(index: AliasIndex, ruleset: Ruleset) -> dict[int, str]:
    """initiative -> strategy card id, from the game's strategy card set."""
    out: dict[int, str] = {}
    kb = index.kb
    if kb is None:
        return out
    for deck_id in ruleset.deck_ids("strategy_card"):
        deck = kb.decks.get(deck_id)
        for cid in deck.cards if deck else []:
            init = index.stats(cid).get("initiative")
            if isinstance(init, int):
                out.setdefault(init, cid)
    return out


# -------------------------------------------------------------- web-data


def from_web_data(data: dict, kb: KnowledgeBase | None = None, ruleset: Ruleset | None = None) -> ImportResult:
    index = AliasIndex(kb, colors=[p.get("color", "") for p in data.get("playerData", [])])
    notes: list[str] = []
    gs = data.get("gameState") or {}
    sc_rows = data.get("strategyCards") or []
    thunders_edge = bool(data.get("expeditions")) or any(str(c.get("id", "")).startswith("te") for c in sc_rows)
    rs = ruleset or (THUNDERS_EDGE_RULESET if thunders_edge else POK_RULESET)

    color_to_faction = {p.get("color"): p.get("faction") for p in data.get("playerData", [])}
    sc_card = {c.get("initiative"): c for c in sc_rows}

    players: dict[str, PlayerState] = {}
    speaker = active = None
    for p in data.get("playerData", []):
        faction = p.get("faction")
        if not faction or faction == NEUTRAL or (p.get("eliminated") and not p.get("planets")):
            continue  # neutral units stay on the map but are not a seat
        if p.get("isSpeaker"):
            speaker = faction
        if p.get("active"):
            active = faction
        exhausted_techs = set(p.get("exhaustedTechs") or [])
        scored = [
            ScoredObjective(id=sid, secret=True, revealed=True)
            for sid in index.many("secret_objective", (p.get("secretsScored") or {}).keys())
        ]
        held = []
        for init in p.get("scs") or []:
            row = sc_card.get(init) or {}
            cid = index.get("strategy_card", row.get("id")) if row.get("id") else f"sc.initiative_{init}"
            held.append(
                HeldStrategyCard(
                    id=cid or f"sc.initiative_{init}",
                    primary_used=bool(row.get("played")),
                    exhausted=init in (p.get("exhaustedSCs") or []),
                )
            )
        players[faction] = PlayerState(
            faction=faction,
            vp=int(p.get("totalVps") or 0),
            command_tokens=CommandTokens(
                tactic=int(p.get("tacticalCC") or 0),
                fleet=int(p.get("fleetCC") or 0),
                strategy=int(p.get("strategicCC") or 0),
            ),
            trade_goods=int(p.get("tg") or 0),
            commodities=Commodities(current=int(p.get("commodities") or 0), max=int(p.get("commoditiesTotal") or 0)),
            techs=[
                OwnedTech(id=tid, exhausted=alias in exhausted_techs)
                for alias in p.get("techs") or []
                if (tid := index.get("technology", alias))
            ],
            unit_upgrades=_upgrades(p.get("unitsOwned") or []),
            strategy_cards=held,
            passed=bool(p.get("passed")),
            leaders=_leaders(p.get("leaders") or []),
            relics=index.many("relic", p.get("relics") or []),
            promissory_in_play=index.many("promissory_note", p.get("promissoryNotesInPlayArea") or []),
            scored=scored,
            private=PrivateHoldings(
                known=False,
                public_counts=HiddenCounts(
                    action_cards=int(p.get("acCount") or 0),
                    promissory_notes=int(p.get("pnCount") or 0),
                    secret_objectives=int(p.get("soCount") or 0),
                ),
            ),
        )

    # Public objectives: revealed ones, and who scored them.
    revealed = []
    for o in (data.get("objectives") or {}).get("allObjectives", []):
        if not o.get("revealed"):
            continue
        oid = index.get("objective", o.get("key"))
        if oid is None or oid.startswith("asyncti4:"):
            # Not a public objective card: custom VP sources such as the
            # custodians token or Shard of the Throne show up here too.
            notes.append(f"VP source not imported as an objective: {o.get('name') or o.get('key')}")
            continue
        revealed.append(oid)
        for f in o.get("scoredFactions") or []:
            if f in players and not any(s.id == oid for s in players[f].scored):
                players[f].scored.append(ScoredObjective(id=oid))

    systems: list[SystemState] = []
    units = data.get("tileUnitData") or {}
    for entry in data.get("tilePositions") or []:
        pos, _, tile = entry.partition(":")
        sysm = _system(pos, tile, index)
        tud = units.get(pos) or {}
        sysm.command_tokens = [f for f in tud.get("ccs") or [] if f]
        for owner, ents in (tud.get("space") or {}).items():
            _add_entities(sysm.space, sysm.damaged, owner, ents)
            for e in ents:
                if e.get("entityType") == "token":
                    _add_wormhole(sysm, str(e.get("entityId") or ""))
        for pname, pdata in (tud.get("planets") or {}).items():
            planet = PlanetState(
                controller=pdata.get("controlledBy") or None,
                exhausted=bool(pdata.get("exhausted")),
            )
            for owner, ents in (pdata.get("entities") or {}).items():
                for e in ents:
                    if e.get("entityType") in ("token", "attachment"):
                        planet.attachments.append(e.get("entityId"))
                dmg: dict = {}
                _add_entities(planet.units, dmg, owner, ents)
            sysm.planets[pname] = planet
        systems.append(sysm)

    laws = [
        LawInPlay(id=index.get("agenda", law.get("id")) or law.get("id"), elected=law.get("electedInfo"))
        for law in data.get("lawsInPlay") or []
    ]
    phase = PHASES.get(str(gs.get("phase") or "").lower(), "action")
    state = GameState(
        ruleset=rs,
        meta=Meta(
            game_id=data.get("gameName") or "asyncti4",
            source="asyncti4",
            players=len(players),
            vp_target=int(data.get("vpsToWin") or 10),
        ),
        turn=TurnState(
            round=int(data.get("gameRound") or 1),
            phase=phase,  # type: ignore[arg-type]
            active_player=active or color_to_faction.get(gs.get("activePlayer")) or gs.get("activePlayer"),
            speaker=speaker,
        ),
        map=systems,
        players=players,
        objectives=ObjectivesState(public_revealed=revealed, unrevealed_known=False),
        laws=laws,
    )
    notes.append("Public data: hands and unrevealed objectives are unknown; use set_hand for your own cards.")
    return ImportResult(state, dict(index.unmapped), notes)


def _add_entities(units: dict, damaged: dict, owner: str, entities: Iterable[dict]) -> None:
    for e in entities:
        if e.get("entityType") != "unit":
            continue
        name = UNIT_CODES.get(e.get("entityId"), e.get("entityId"))
        count = int(e.get("count") or 0)
        if count <= 0:
            continue
        units.setdefault(owner, {})
        units[owner][name] = units[owner].get(name, 0) + count
        states = e.get("unitStates") or []
        hurt = int(states[1]) if len(states) > 1 else int(e.get("sustained") or 0)
        if hurt:
            damaged.setdefault(owner, {})
            damaged[owner][name] = damaged[owner].get(name, 0) + hurt


def set_hand(
    state: GameState,
    player: str,
    action_cards: Iterable[str] = (),
    promissory_notes: Iterable[str] = (),
    secret_objectives: Iterable[str] = (),
) -> GameState:
    """Record a player's own hand (entered by them) in a public-data game."""
    p = state.players[player]
    counts = p.private.counts()
    p.private = PrivateHoldings(
        action_cards=list(action_cards),
        promissory_notes=list(promissory_notes),
        secret_objectives=list(secret_objectives),
        known=True,
    )
    # Everyone else still sees the public counts.
    if (len(p.private.action_cards), len(p.private.promissory_notes), len(p.private.secret_objectives)) != (
        counts.action_cards,
        counts.promissory_notes,
        counts.secret_objectives,
    ):
        p.private.public_counts = counts
    return state


# ------------------------------------------------------------------ saves


def _kv(lines: Iterable[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in lines:
        key, _, value = line.partition(" ")
        if key and not key.startswith("-"):
            out[key] = value.strip()
    return out


def _list(value: str | None) -> list[str]:
    """ "a,1;b,2;" -> ["a", "b"];  "a,b,c" -> ["a", "b", "c"]."""
    if not value:
        return []
    if ";" in value:
        return [part.split(",", 1)[0] for part in value.split(";") if part.strip()]
    return [v for v in value.split(",") if v]


def _pairs(value: str | None) -> dict[str, str]:
    """ "a,x;b,y;" -> {"a": "x", "b": "y"}."""
    out: dict[str, str] = {}
    for part in (value or "").split(";"):
        if "," in part:
            k, v = part.split(",", 1)
            out[k.strip()] = v.strip()
    return out


def _sections(text: str):
    lines = text.splitlines()
    game_info: list[str] = []
    players: list[list[str]] = []
    tiles: list[list[str]] = []
    mode, buf = None, []
    for line in lines:
        if line == "-gameinfo-":
            mode, buf = "game", game_info
            continue
        if line == "-endgameinfo-":
            mode = None
            continue
        if line == "-player-":
            mode, buf = "player", []
            players.append(buf)
            continue
        if line == "-endplayer-":
            mode = None
            continue
        if line == "-tile-":
            mode, buf = "tile", []
            tiles.append(buf)
            continue
        if line == "-endtile-":
            mode = None
            continue
        if mode is not None:
            buf.append(line)
    header = lines[:3]
    return header, game_info, players, tiles


_UNIT_LINE = re.compile(r"^([a-z]+)—([a-z0-9_]+) ([\d,]+)$")
_TOKEN = re.compile(r"^(command|control)_([a-z]+)\.png$")


def from_save(
    save_text: str,
    events: list[dict] | None = None,
    kb: KnowledgeBase | None = None,
    colors: dict[str, str] | None = None,
) -> ImportResult:
    colors = colors if colors is not None else load_colors()
    index = AliasIndex(kb, colors=colors.values())
    notes: list[str] = []
    header, game_lines, player_blocks, tile_blocks = _sections(save_text)
    game = _kv(game_lines)
    game_name = header[2].strip() if len(header) >= 3 else "asyncti4"

    ruleset = _ruleset_from_decks(game, thunders_edge=game.get("thunders_edge_mode") == "true")
    sc_cards = _sc_by_initiative(index, ruleset)
    sc_played = {int(k): v == "true" for k, v in _pairs(game.get("sc_played")).items() if k.isdigit()}

    # Players: user id -> faction only in memory, for speaker/active/scoring.
    user_to_faction: dict[str, str] = {}
    color_alias_to_faction: dict[str, str] = {}
    name_to_alias = {v: k for k, v in colors.items()}
    players: dict[str, PlayerState] = {}
    planet_info: dict[str, tuple[list[str], set[str]]] = {}  # faction -> (planets, exhausted)
    for block in player_blocks:
        user_id = block[0].strip() if block else ""
        kv = _kv(block[2:])
        faction = kv.get("faction")
        if not faction or faction == "null" or kv.get("dummy") == "true":
            continue
        user_to_faction[user_id] = faction
        alias = name_to_alias.get(kv.get("color", ""), kv.get("color", ""))
        color_alias_to_faction[alias] = faction

        exhausted = set(_list(kv.get("tech_exhausted")))
        held = []
        for s in _list(kv.get("strategy_card")):
            if s.isdigit():
                init = int(s)
                held.append(
                    HeldStrategyCard(
                        id=sc_cards.get(init, f"sc.initiative_{init}"),
                        primary_used=sc_played.get(init, False),
                        exhausted=sc_played.get(init, False),
                    )
                )
        leaders = []
        for entry in (kv.get("leaders") or "").split(";"):
            parts = entry.split(",")
            if len(parts) >= 5:
                leaders.append(
                    {"id": parts[0], "type": parts[1], "exhausted": parts[3] == "true", "locked": parts[4] == "true"}
                )
        players[faction] = PlayerState(
            faction=faction,
            command_tokens=CommandTokens(
                tactic=int(kv.get("tactical_cc") or 0),
                fleet=int(kv.get("fleet_cc") or 0),
                strategy=int(kv.get("strategy_cc") or 0),
            ),
            trade_goods=int(kv.get("trade_goods") or 0),
            commodities=Commodities(
                current=int(kv.get("commodities") or 0), max=int(kv.get("commodities_total") or 0)
            ),
            techs=[
                OwnedTech(id=tid, exhausted=a in exhausted)
                for a in _list(kv.get("tech"))
                if (tid := index.get("technology", a))
            ],
            unit_upgrades=_upgrades(_list(kv.get("units_owned"))),
            strategy_cards=held,
            passed=kv.get("passed") == "true",
            leaders=_leaders(leaders),
            relics=index.many("relic", _list(kv.get("relics"))),
            promissory_in_play=index.many("promissory_note", _list(kv.get("promissory_notes_play_area"))),
            scored=[
                ScoredObjective(id=sid, secret=True, revealed=True)
                for sid in index.many("secret_objective", _list(kv.get("secret_objectives_scored")))
            ],
            private=PrivateHoldings(
                action_cards=index.many("action_card", _list(kv.get("action_cards"))),
                promissory_notes=index.many("promissory_note", _list(kv.get("promissory_notes"))),
                secret_objectives=index.many("secret_objective", _list(kv.get("secret_objectives"))),
            ),
        )
        planet_info[faction] = (_list(kv.get("planets")), set(_list(kv.get("planets_exhausted"))))

    # Public objectives and VP.
    custom_vp = {k: int(v) for k, v in _pairs(game.get("customPublicVP")).items() if v.lstrip("-").isdigit()}
    revealed_aliases = list(_pairs(game.get("revealedPublicObjectives")).keys())
    revealed = [index.get("objective", a) for a in revealed_aliases if a not in custom_vp]
    for entry in (game.get("scoredPublicObjectives") or "").split(";"):
        if "," not in entry:
            continue
        alias, users = entry.split(",", 1)
        oid = None if alias in custom_vp else index.get("objective", alias)
        points = custom_vp.get(alias) or int(index.stats(oid or "").get("points") or 1)
        for uid in users.split("-"):
            faction = user_to_faction.get(uid.strip())
            if faction:
                if oid:
                    players[faction].scored.append(ScoredObjective(id=oid))
                players[faction].vp += points
    sftt = index.get("promissory_note", "<color>_sftt")
    for p in players.values():
        p.vp += len([s for s in p.scored if s.secret])
        p.vp += p.promissory_in_play.count(sftt)  # Support for the Throne: 1 VP while in play
    notes.append(
        "VP is derived from scored objectives, custom VP sources and Support for the Throne; "
        "other VP effects may be missed."
    )

    # Map.
    systems: list[SystemState] = []
    controllers: dict[str, str] = {}
    for faction, (planets, _) in planet_info.items():
        for planet in planets:
            controllers[planet] = faction
    for block in tile_blocks:
        if not block:
            continue
        tile, _, pos = block[0].partition(" ")
        sysm = _system(pos.strip(), tile.strip(), index)
        holder = None
        in_tokens = False
        for line in block[1:]:
            if line == "-units-":
                holder, in_tokens = "", False
                continue
            if line in ("-planettokens-", "-tokens-"):
                in_tokens = True
                continue
            if line in ("-planetendtokens-", "-endtokens-", "-endunits-", "-unitholder-", "-endunitholder-"):
                if line == "-endunits-":
                    in_tokens = False
                continue
            if holder == "" and not in_tokens:
                holder = line.strip()
                if holder != "space":
                    sysm.planets.setdefault(holder, PlanetState(controller=controllers.get(holder)))
                continue
            m = _UNIT_LINE.match(line)
            if m and holder is not None:
                code, unit, counts = m.groups()
                owner = NEUTRAL if code in NEUTRAL_COLORS else color_alias_to_faction.get(code, code)
                nums = [int(x) for x in counts.split(",") if x]
                total = sum(nums)
                hurt = nums[1] if len(nums) > 1 else 0
                name = UNIT_CODES.get(unit, unit)
                target = sysm.space if holder == "space" else sysm.planets[holder].units
                target.setdefault(owner, {})[name] = target.get(owner, {}).get(name, 0) + total
                if hurt and holder == "space":
                    sysm.damaged.setdefault(owner, {})[name] = sysm.damaged.get(owner, {}).get(name, 0) + hurt
                continue
            if line.startswith("token_"):
                _add_wormhole(sysm, line.strip())
            t = _TOKEN.match(line.strip())
            if t:
                kind, code = t.groups()
                faction = color_alias_to_faction.get(code)
                if kind == "command" and faction and faction not in sysm.command_tokens:
                    sysm.command_tokens.append(faction)
                continue
            if in_tokens and holder not in (None, "", "space") and line.startswith("attachment_"):
                sysm.planets[holder].attachments.append(line[len("attachment_"):].rsplit(".", 1)[0])
        for name, planet in sysm.planets.items():
            owner = planet.controller
            if owner in planet_info and name in planet_info[owner][1]:
                planet.exhausted = True
        systems.append(sysm)

    phase = PHASES.get(str(game.get("phase_of_game", "")).lower(), "action")
    if game.get("ended_date") or "game_ended" in {e.get("archetype", "").lower() for e in events or []}:
        phase = "game_over"
    law_elected = _pairs(game.get("law_info"))
    state = GameState(
        ruleset=ruleset,
        meta=Meta(
            game_id=game_name, source="asyncti4_export", players=len(players), vp_target=int(game.get("vp_count") or 10)
        ),
        turn=TurnState(
            round=int(game.get("round") or 1),
            phase=phase,  # type: ignore[arg-type]
            active_player=user_to_faction.get(game.get("active_player", "")),
            speaker=user_to_faction.get(game.get("speaker", "")),
        ),
        map=systems,
        players=players,
        objectives=ObjectivesState(
            public_revealed=[r for r in revealed if r],
            public_unrevealed=index.many("objective", _list(game.get("publicObjectives1")) + _list(game.get("publicObjectives2"))),
        ),
        laws=[
            LawInPlay(id=index.get("agenda", a) or a, elected=law_elected.get(a))
            for a in _list(game.get("law"))
        ],
        discards=Discards(
            action_cards=index.many("action_card", _list(game.get("action_cards_discarded"))),
            agendas=index.many("agenda", _list(game.get("discarded_agendas"))),
        ),
        agenda_deck=index.many("agenda", _list(game.get("agendas"))),
        log=_events(events or [], players),
    )
    return ImportResult(state, dict(index.unmapped), notes)


def _events(events: list[dict], players: dict[str, PlayerState]) -> list[LogEvent]:
    out = []
    for e in events:
        payload = dict(e.get("payload") or {})
        if e.get("archetype") == "MANUAL_COMMAND":
            payload.pop("user", None)  # a username
        actor = e.get("faction")
        out.append(
            LogEvent(
                seq=int(e.get("seq") or len(out) + 1),
                round=int(e.get("round") or 0),
                step=e.get("phase"),
                actor=actor if actor in players else None,
                kind=str(e.get("archetype") or "event").lower(),
                data=payload,
            )
        )
    return out
