"""Checks on the choices made in a strategy card's primary ability.

Based on the card texts (PoK, and Thunder's Edge's revised Construction and
Warfare) and the LRR: Construction 24, Imperial 45, Structures 85,
Technology 90 and 91, Warfare 99.

``StrategicAction.primary`` carries the choices. Shapes, by card:

* Leadership: ``{"influence_from": [planet], "trade_goods": n, "tokens": {"tactic": a, "fleet": b, "strategy": c}}``.
  You gain 3 tokens, plus 1 per 3 influence spent; ``tokens`` is how they are placed.
* Diplomacy: ``{"system": pos, "ready": [planet, ...]}``.
* Politics: ``{"speaker": player}``.
* Construction:
  * PoK: ``{"place": [{"structure": "pds"|"space_dock", "planet": p}, {"structure": "pds", "planet": p}]}``.
  * Thunder's Edge: the first entry may instead be ``{"production": {...}}``.
* Trade: ``{"chosen_players": [player, ...]}``.
* Warfare:
  * PoK: ``{"remove_token_from": pos, "to_pool": "tactic"|"fleet"|"strategy"}``.
  * Thunder's Edge: ``{"tactical": {...tactical action...}}``, run without placing a token, in any system.
* Technology: ``{"research": [tech, ...], "specialties": [planet, ...], "pay_with": [planet | "tg:N", ...]}``.
  The first tech is free; a second costs 6 resources.
* Imperial: ``{"score": objective id or null}``.

What the engine cannot see stays unchecked: whether an objective's
requirements are met (Imperial), and the effects of drawing cards.
"""

from __future__ import annotations

import math
from collections import Counter

from warlord.engine.movement import MoveFinding

STRUCTURE_LIMITS = {"space_dock": 1, "pds": 2}  # per planet (85.4, 85.5)
COLORS = {"B": "propulsion", "G": "biotic", "R": "warfare", "Y": "cybernetic"}
MECATOL_PLANETS = {"mr", "mrte", "mecatol_rex", "mecatolrex"}


def card_kind(card_id: str) -> tuple[str | None, bool]:
    """(card name, is Thunder's Edge version) from an id like 'sc.te6warfare' or 'trade'."""
    name = card_id.split(".")[-1].lower()
    for kind in ("leadership", "diplomacy", "politics", "construction", "trade", "warfare", "technology", "imperial"):
        if kind in name:
            return kind, name.startswith("te")
    return None, False


def _stat(kb, rid: str, key: str):
    rec = kb.get(rid) if kb is not None else None
    return (getattr(rec, "stats", {}) or {}).get(key)


def _controlled(view, player: str) -> dict:
    return {name: p for s in view.map for name, p in s.planets.items() if p.controller == player}


def check_primary(view, kb, action) -> list[MoveFinding]:
    kind, te = card_kind(action.card)
    choices = action.primary or {}
    out: list[MoveFinding] = []
    if kind is None:
        return [MoveFinding("unchecked", "strategic_action", f"primary ability of {action.card} (card not modelled)")]
    if not choices:
        return [MoveFinding("unchecked", "strategic_action", f"primary ability choices for {action.card} (none given)")]
    fn = {
        "leadership": _leadership, "diplomacy": _diplomacy, "politics": _politics, "construction": _construction,
        "trade": _trade, "warfare": _warfare, "technology": _technology, "imperial": _imperial,
    }[kind]
    fn(view, kb, action.player, choices, te, out)
    return out


def _bad(out, msg):
    out.append(MoveFinding("violation", "strategic_action", msg))


def _leadership(view, kb, player, c, te, out):
    me = view.players[player]
    planets = _controlled(view, player)
    influence = 0
    for name in c.get("influence_from", []):
        p = planets.get(name)
        if p is None:
            _bad(out, f"{player} does not control {name}.")
            continue
        if p.exhausted:
            _bad(out, f"{name} is exhausted, so its influence can't be spent.")
            continue
        value = _stat(kb, f"planet.{name.lower()}", "influence")
        if value is None:
            out.append(MoveFinding("unchecked", "strategic_action", f"influence of {name}"))
            return
        influence += int(value)
    tg = int(c.get("trade_goods", 0))
    if tg > me.trade_goods:
        _bad(out, f"{player} has {me.trade_goods} trade goods, not {tg}.")
    gained = 3 + (influence + tg) // 3
    tokens = c.get("tokens")
    if tokens is not None:
        placed = sum(int(v) for v in tokens.values())
        if set(tokens) - {"tactic", "fleet", "strategy"}:
            _bad(out, "Command tokens go into the tactic, fleet or strategy pool.")
        if placed != gained:
            _bad(out, f"Leadership gives {gained} command tokens here (3, plus 1 per 3 influence spent), not {placed}.")
    on_sheet = sum(me.command_tokens.model_dump().values())
    on_board = sum(1 for s in view.map if player in s.command_tokens)
    if on_sheet + on_board + gained > 16:
        out.append(MoveFinding("warning", "strategic_action",
                               f"Only 16 command tokens exist per player; {player} can gain at most {16 - on_sheet - on_board}."))


def _diplomacy(view, kb, player, c, te, out):
    pos = c.get("system")
    system = view.system(pos) if pos else None
    if system is None:
        _bad(out, f"There is no system at {pos!r}.")
        return
    if any(name.lower() in MECATOL_PLANETS for name in system.planets) or system.tile in ("18", "112"):
        _bad(out, "Diplomacy can't choose the Mecatol Rex system.")
    if not any(p.controller == player for p in system.planets.values()):
        _bad(out, f"{player} controls no planet in {pos}.")
    ready = c.get("ready", [])
    if len(ready) > 2:
        _bad(out, "Diplomacy readies at most 2 planets.")
    planets = _controlled(view, player)
    for name in ready:
        if name not in planets:
            _bad(out, f"{player} does not control {name}.")
        elif not planets[name].exhausted:
            _bad(out, f"{name} is not exhausted, so there is nothing to ready.")


def _politics(view, kb, player, c, te, out):
    speaker = c.get("speaker")
    if speaker not in view.players:
        _bad(out, f"No player {speaker!r} in this game.")
    elif speaker == view.turn.speaker:
        _bad(out, f"Politics must give the speaker token to a player other than the speaker ({speaker}).")


def _construction(view, kb, player, c, te, out):
    place = c.get("place", [])
    if not place or len(place) > 2:
        _bad(out, "Construction places one or two structures.")
        return
    planets = _controlled(view, player)
    added: Counter = Counter()
    for i, entry in enumerate(place):
        if "production" in entry:
            if te and i == 0:
                out.append(MoveFinding("unchecked", "strategic_action",
                                       "the space dock production (validate it as a production with validate_action)"))
            else:
                _bad(out, "Only the first Thunder's Edge Construction choice can be a space dock's production.")
            continue
        kind = str(entry.get("structure", "")).lower().replace(" ", "_")
        name = entry.get("planet")
        if kind not in STRUCTURE_LIMITS:
            out.append(MoveFinding("unchecked", "strategic_action", f"placing structure {kind!r}"))
            continue
        if not te and (i == 1 and kind != "pds"):
            _bad(out, "The second Construction placement is a PDS.")
        if name not in planets:
            _bad(out, f"{player} does not control {name}, so it can't hold their {kind}.")
            continue
        have = planets[name].units.get(player, {}).get(kind, 0) + added[(name, kind)]
        if have + 1 > STRUCTURE_LIMITS[kind]:
            _bad(out, f"{name} can hold at most {STRUCTURE_LIMITS[kind]} {kind}.")
        added[(name, kind)] += 1


def _trade(view, kb, player, c, te, out):
    for pid in c.get("chosen_players", []):
        if pid == player or pid not in view.players:
            _bad(out, f"Trade's free secondary goes to other players; {pid!r} is not one.")


def _warfare(view, kb, player, c, te, out):
    if te:
        tactical = c.get("tactical")
        if not isinstance(tactical, dict):
            _bad(out, "Thunder's Edge Warfare's primary is a tactical action: give it under 'tactical'.")
            return
        from warlord.engine.validate import validate_action

        # "Without placing a command token, even if the system already has your command token in it."
        free = view.model_copy(deep=True)
        target = free.system(tactical.get("activate", ""))
        if target is not None and player in target.command_tokens:
            target.command_tokens.remove(player)
        free.players[player].command_tokens.tactic += 1
        result = validate_action(free, {**tactical, "type": "tactical", "player": player}, kb=kb)
        for v in result.violations:
            out.append(MoveFinding("violation", v.topic, f"Warfare's tactical action: {v.message}"))
        for u in result.unchecked:
            out.append(MoveFinding("unchecked", "strategic_action", f"Warfare's tactical action: {u}"))
        for w in result.warnings:
            out.append(MoveFinding("warning", w.topic, w.message))
        return
    pos = c.get("remove_token_from")
    system = view.system(pos) if pos else None
    if system is None or player not in system.command_tokens:
        _bad(out, f"Warfare removes one of {player}'s command tokens from the board; {pos!r} has none.")
    if c.get("to_pool") not in (None, "tactic", "fleet", "strategy"):
        _bad(out, "The token goes into the tactic, fleet or strategy pool.")


def _technology(view, kb, player, c, te, out):
    me = view.players[player]
    research = c.get("research", [])
    if not research or len(research) > 2:
        _bad(out, "Technology researches one technology, or two if the second is paid for with 6 resources.")
        return
    owned = {t.id for t in me.techs}
    colors: Counter = Counter()
    for tid in owned:
        for t in _stat(kb, tid, "tech_types") or []:
            if t.lower() in COLORS.values():
                colors[t.lower()] += 1
    planets = _controlled(view, player)
    specialties: Counter = Counter()
    used = set(c.get("specialties", []))
    for name in used:
        p = planets.get(name)
        if p is None or p.exhausted:
            _bad(out, f"{name} must be a ready planet {player} controls to use its technology specialty.")
            continue
        for spec in _stat(kb, f"planet.{name.lower()}", "tech_specialties") or []:
            specialties[spec.lower()] += 1

    for i, tid in enumerate(research):
        rec = kb.get(tid) if kb is not None else None
        if rec is None:
            out.append(MoveFinding("unchecked", "strategic_action", f"prerequisites of {tid} (not in the knowledge base)"))
            continue
        if tid in owned:
            _bad(out, f"{player} already owns {rec.name}.")
            continue
        faction = getattr(rec, "faction", None)
        if faction and faction != me.faction:
            _bad(out, f"{rec.name} is a {faction} faction technology; {player} plays {me.faction} (90.11).")
            continue
        need = Counter(COLORS[ch] for ch in str(_stat(kb, tid, "requirements") or "") if ch in COLORS)
        missing = sum(max(0, n - colors[col] - specialties[col]) for col, n in need.items())
        if missing:
            have = ", ".join(f"{colors[col]} {col}" for col in need)
            _bad(out, f"{rec.name} needs {dict(need)} prerequisites; {player} has {have} (plus specialties).")
        for col, n in need.items():  # specialties spent on this tech can't count again
            over = max(0, n - colors[col])
            specialties[col] -= min(over, specialties[col])
        for t in _stat(kb, tid, "tech_types") or []:
            if t.lower() in COLORS.values():
                colors[t.lower()] += 1  # the first tech counts toward the second's prerequisites
        owned.add(tid)

    if len(research) == 2:
        paid = 0
        for item in c.get("pay_with", []):
            if str(item).startswith("tg:"):
                n = int(str(item).split(":", 1)[1] or 0)
                if n > me.trade_goods:
                    _bad(out, f"{player} has {me.trade_goods} trade goods, not {n}.")
                paid += n
                continue
            p = planets.get(item)
            if p is None or p.exhausted or item in used:
                _bad(out, f"{item} can't pay: it must be a ready planet {player} controls and not used for a specialty.")
                continue
            paid += int(_stat(kb, f"planet.{item.lower()}", "resources") or 0)
        if paid < 6:
            _bad(out, f"The second technology costs 6 resources; the payment named gives {paid}.")


def _imperial(view, kb, player, c, te, out):
    me = view.players[player]
    obj = c.get("score")
    if obj:
        if obj not in view.objectives.public_revealed:
            _bad(out, f"{obj} is not a revealed public objective.")
        elif obj in me.scored:
            _bad(out, f"{player} has already scored {obj} (61.8).")
        else:
            out.append(MoveFinding("unchecked", "strategic_action", f"whether {player} meets {obj}'s requirements"))
    holds_mecatol = any(
        p.controller == player for s in view.map for name, p in s.planets.items() if name.lower() in MECATOL_PLANETS
    )
    out.append(MoveFinding(
        "warning", "strategic_action",
        f"{player} {'controls Mecatol Rex: +1 VP' if holds_mecatol else 'does not control Mecatol Rex: draws 1 secret objective instead'}.",
    ))
