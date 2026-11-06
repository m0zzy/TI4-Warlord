"""Import AsyncTI4 bot component data into ComponentCard records.

Reads the JSON files under ``src/main/resources/data`` of a local checkout of
the AsyncTI4 ``TI4_map_generator_bot`` repository: action cards, agendas,
technologies, units, leaders, promissory notes, relics, breakthroughs,
strategy cards, factions, faction abilities, objectives, secret objectives and
exploration cards, plus the deck and strategy card set definitions that say
which printing of each card a game mode uses. Tiles (with hyperlane lanes) and
planets come from the sibling ``systems`` and ``planets`` folders. Nothing is downloaded here: point the
importer at a checkout, and keep the output under ``data/private/`` because
the card text is Fantasy Flight Games' copyright.

Only sources listed in ``include`` are kept. Everything else (homebrew,
Discordant Stars, Twilight's Fall) is skipped and counted.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from warlord.kb.records import ComponentCard, Deck, Timing

# AsyncTI4 "source" value -> our ruleset tag.
SOURCE_MAP = {
    "base": "base",
    "pok": "pok",
    "codex1": "codex",
    "codex2": "codex",
    "codex3": "codex",
    "codex4": "codex",
    "thunders_edge": "thunders_edge",
    "te": "thunders_edge",
}

DEFAULT_INCLUDE = frozenset({"base", "pok", "codex", "thunders_edge"})

# AsyncTI4 deck "type" -> ComponentCard.type, for the card types we import.
DECK_TYPES = {
    "action_card": "action_card",
    "technology": "technology",
    "relic": "relic",
    "agenda": "agenda",
    "secret_objective": "secret_objective",
    "public_stage_1_objective": "objective",
    "public_stage_2_objective": "objective",
    "explore": "exploration",
}

# Replacement printings: "<id>-te" / "<id>_te" / "<id>_omega" replace "<id>".
_REPLACEMENT_SUFFIX = re.compile(r"^(?P<base>.+?)(?:[-_]te|[-_]?omega)$")


def slug(text: str) -> str:
    text = text.lower().replace("&", " and ").replace("'", "")
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


_TIMING_PREFIXES: list[tuple[str, str]] = [
    ("at the start of", "start"),
    ("at the end of", "end"),
    ("when", "when"),
    ("after", "after"),
    ("before", "before"),
]


def parse_window(window: str | None, phase: str | None = None) -> Timing | None:
    """Turn AsyncTI4's free-text ``window`` ("After you activate a system") into a Timing."""
    if not window:
        return None
    w = window.strip()
    lower = w.lower().rstrip(":").strip()
    phase_norm = phase.strip().lower() if phase else None
    if lower == "action":
        return Timing(window="action", trigger=None, phase=phase_norm or "action")
    for prefix, kind in _TIMING_PREFIXES:
        if lower.startswith(prefix):
            trigger = w[len(prefix):].strip(" ,:")
            return Timing(window=kind, trigger=trigger or None, phase=phase_norm)  # type: ignore[arg-type]
    return Timing(window="any", trigger=w, phase=phase_norm)


@dataclass
class ImportReport:
    records: list[ComponentCard] = field(default_factory=list)
    decks: list[Deck] = field(default_factory=list)
    skipped_sources: Counter = field(default_factory=Counter)
    merged_copies: int = 0
    files: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        by_type = Counter(r.type for r in self.records)
        return {
            "records": len(self.records),
            "by_type": dict(by_type),
            "decks": {d.id: len(d.cards) for d in self.decks},
            "unresolved_deck_cards": sum(len(d.unresolved) for d in self.decks),
            "merged_copies": self.merged_copies,
            "skipped_sources": dict(self.skipped_sources),
            "files": self.files,
        }


class _Builder:
    def __init__(self, include: frozenset[str]):
        self.include = include
        self.report = ImportReport()
        self._by_id: dict[str, ComponentCard] = {}
        self._raw_decks: list[tuple[dict, str, str]] = []  # (row, tag, our card type)
        self.hyperlanes: dict[str, list[list[int]]] = {}

    def tag(self, source: str | None) -> str | None:
        tag = SOURCE_MAP.get((source or "").lower())
        if tag is None or tag not in self.include:
            self.report.skipped_sources[source or "unknown"] += 1
            return None
        return tag

    def add(self, card: ComponentCard) -> None:
        existing = self._by_id.get(card.id)
        if existing is None:
            self._by_id[card.id] = card
            return
        if existing.text == card.text and existing.ruleset == card.ruleset:
            # Same card, another copy (e.g. AsyncTI4's sabo1..sabo4). Keep aliases.
            merged = sorted(set(existing.aliases) | set(card.aliases))
            self._by_id[card.id] = existing.model_copy(update={"aliases": merged})
            self.report.merged_copies += 1
            return
        # Same name, different text: keep both, disambiguated by ruleset.
        alt_id = f"{card.id}.{card.ruleset}"
        n = 2
        while alt_id in self._by_id:
            alt_id = f"{card.id}.{card.ruleset}{n}"
            n += 1
        self._by_id[alt_id] = card.model_copy(update={"id": alt_id})

    def _attach_hyperlanes(self) -> None:
        """Store each hyperlane tile's lane matrix per rotation ("0", "60", ...)."""
        if not self.hyperlanes:
            return
        for cid, card in list(self._by_id.items()):
            if card.type != "tile" or not card.stats.get("hyperlane"):
                continue
            tile_id = card.aliases[0]
            lanes = {
                key[len(tile_id):] or "0": m
                for key, m in self.hyperlanes.items()
                if key == tile_id or (key.startswith(tile_id) and key[len(tile_id):].isdigit())
            }
            if lanes:
                self._by_id[cid] = card.model_copy(update={"stats": {**card.stats, "lanes": lanes}})

    def _resolve_supersedes(self, by_alias: dict[tuple[str, str], str]) -> None:
        """Turn raw AsyncTI4 replacement aliases into our ids, and add suffix pairs."""
        for i, card in enumerate(self.report.records):
            alias = (card.source_ref or "").rsplit(":", 1)[-1]
            raw = list(card.supersedes)
            m = _REPLACEMENT_SUFFIX.match(alias)
            if m:
                raw.append(m.group("base"))
            ids = []
            for a in raw:
                cid = by_alias.get((card.type, a))
                if cid and cid != card.id and cid not in ids:
                    ids.append(cid)
            if ids != card.supersedes:
                self.report.records[i] = card.model_copy(update={"supersedes": ids})
                self._by_id[card.id] = self.report.records[i]

    def add_deck(self, row: dict, tag: str, card_type: str) -> None:
        self._raw_decks.append((row, tag, card_type))

    def finish(self) -> ImportReport:
        self._attach_hyperlanes()
        self.report.records = list(self._by_id.values())
        # Resolve deck entries (AsyncTI4 aliases) to our ids, per card type.
        by_alias: dict[tuple[str, str], str] = {}
        for card in self.report.records:
            ref = card.source_ref or ""
            alias = ref.rsplit(":", 1)[-1] if ref.startswith("asyncti4:") else None
            for a in {alias, *card.aliases} - {None}:
                by_alias.setdefault((card.type, a), card.id)
        self._resolve_supersedes(by_alias)
        for row, tag, card_type in self._raw_decks:
            ids, missing = [], []
            for a in row.get("cardIDs") or row.get("scIDs") or []:
                cid = by_alias.get((card_type, a))
                if cid is None:
                    missing.append(a)
                elif cid not in ids:
                    ids.append(cid)
            prefix = "deck.sc_set." if card_type == "strategy_card" else "deck."
            self.report.decks.append(
                Deck(
                    id=f"{prefix}{row['alias']}",
                    ruleset=tag,  # type: ignore[arg-type]
                    name=row.get("name", row["alias"]),
                    card_type=card_type,
                    cards=ids,
                    unresolved=missing,
                    source_ref=f"asyncti4:deck:{row['alias']}",
                )
            )
        return self.report


# ----------------------------------------------------------------- converters


def _action_card(row: dict, tag: str) -> ComponentCard:
    return ComponentCard(
        id=f"ac.{slug(row['name'])}",
        type="action_card",
        ruleset=tag,  # type: ignore[arg-type]
        name=row["name"],
        aliases=[row["alias"]] if row.get("alias") else [],
        timing=parse_window(row.get("window"), row.get("phase")),
        text=row.get("text", ""),
        source_ref=f"asyncti4:action_card:{row.get('alias')}",
    )


def _agenda(row: dict, tag: str) -> ComponentCard:
    text = "\n".join(t for t in (row.get("text1"), row.get("text2")) if t)
    return ComponentCard(
        id=f"agenda.{slug(row['name'])}",
        type="agenda",
        ruleset=tag,  # type: ignore[arg-type]
        name=row["name"],
        aliases=[row["alias"]] if row.get("alias") else [],
        timing=Timing(window="passive", phase="agenda"),
        text=text,
        stats={
            k: v
            for k, v in {"agenda_type": row.get("type"), "target": row.get("target")}.items()
            if v
        },
        source_ref=f"asyncti4:agenda:{row.get('alias')}",
    )


def _technology(row: dict, tag: str) -> ComponentCard:
    stats = {
        k: v
        for k, v in {
            "tech_types": row.get("types"),
            "requirements": row.get("requirements"),
            "base_upgrade": row.get("baseUpgrade"),
        }.items()
        if v
    }
    return ComponentCard(
        id=f"tech.{slug(row['name'])}",
        type="technology",
        ruleset=tag,  # type: ignore[arg-type]
        name=row["name"],
        aliases=[a for a in (row.get("alias"), row.get("initials"), row.get("shortName")) if a],
        text=row.get("text", ""),
        faction=row.get("faction"),
        stats=stats,
        source_ref=f"asyncti4:technology:{row.get('alias')}",
    )


_UNIT_FIELDS = {
    "baseType": "base_type",
    "moveValue": "move",
    "capacityValue": "capacity",
    "capacityUsed": "capacity_used",
    "cost": "cost",
    "combatHitsOn": "combat",
    "combatDieCount": "combat_dice",
    "afbHitsOn": "afb",
    "afbDieCount": "afb_dice",
    "bombardHitsOn": "bombard",
    "bombardDieCount": "bombard_dice",
    "spaceCannonHitsOn": "space_cannon",
    "spaceCannonDieCount": "space_cannon_dice",
    "deepSpaceCannon": "deep_space_cannon",
    "sustainDamage": "sustain_damage",
    "canBeDirectHit": "can_be_direct_hit",
    "planetaryShield": "planetary_shield",
    "disablesPlanetaryShield": "disables_planetary_shield",
    "isShip": "is_ship",
    "isGroundForce": "is_ground_force",
    "isStructure": "is_structure",
    "productionValue": "production",
    "requiredTechId": "required_tech",
    "upgradesToUnitId": "upgrades_to",
    "upgradesFromUnitId": "upgrades_from",
    "faction": "faction",
}


def _unit(row: dict, tag: str) -> ComponentCard:
    stats = {ours: row[theirs] for theirs, ours in _UNIT_FIELDS.items() if theirs in row}
    name = row.get("name", row["id"])
    if row.get("subtitle"):
        name = f"{name} ({row['subtitle']})"
    return ComponentCard(
        id=f"unit.{slug(row['id'])}",
        type="unit",
        ruleset=tag,  # type: ignore[arg-type]
        name=name,
        aliases=[a for a in (row.get("asyncId"), row.get("name")) if a],
        text=row.get("ability", ""),
        faction=row.get("faction"),
        stats=stats,
        source_ref=f"asyncti4:unit:{row['id']}",
        supersedes=[row["homebrewReplacesID"]] if row.get("homebrewReplacesID") else [],
    )


def _leader(row: dict, tag: str) -> ComponentCard:
    return ComponentCard(
        id=f"leader.{slug(row['id'])}",
        type="leader",
        ruleset=tag,  # type: ignore[arg-type]
        name=row["name"],
        aliases=[a for a in (row.get("id"), row.get("title")) if a],
        timing=parse_window(row.get("abilityWindow")),
        text=row.get("abilityText", ""),
        faction=row.get("faction"),
        stats={
            k: v
            for k, v in {"leader_type": row.get("type"), "unlock": row.get("unlockCondition")}.items()
            if v
        },
        source_ref=f"asyncti4:leader:{row['id']}",
        supersedes=[row["homebrewReplacesID"]] if row.get("homebrewReplacesID") else [],
    )


def _promissory_note(row: dict, tag: str) -> ComponentCard:
    return ComponentCard(
        id=f"pn.{slug(row['alias'])}",
        type="promissory_note",
        ruleset=tag,  # type: ignore[arg-type]
        name=row["name"],
        aliases=[a for a in (row.get("alias"), row.get("shortName")) if a],
        text=row.get("text", ""),
        faction=row.get("faction"),
        stats={k: row[k] for k in ("playArea", "attachment", "playImmediately") if k in row},
        source_ref=f"asyncti4:promissory_note:{row['alias']}",
    )


def _relic(row: dict, tag: str) -> ComponentCard:
    return ComponentCard(
        id=f"relic.{slug(row['name'])}",
        type="relic",
        ruleset=tag,  # type: ignore[arg-type]
        name=row["name"],
        aliases=[a for a in (row.get("alias"), row.get("shortName")) if a],
        text=row.get("text", ""),
        source_ref=f"asyncti4:relic:{row['alias']}",
    )


def _breakthrough(row: dict, tag: str) -> ComponentCard:
    return ComponentCard(
        id=f"breakthrough.{slug(row['name'])}",
        type="breakthrough",
        ruleset=tag,  # type: ignore[arg-type]
        name=row["name"],
        aliases=[a for a in (row.get("alias"),) if a],
        text=row.get("text", ""),
        faction=row.get("faction"),
        stats={"synergy": row["synergy"]} if row.get("synergy") else {},
        source_ref=f"asyncti4:breakthrough:{row['alias']}",
    )


def _strategy_card(row: dict, tag: str) -> ComponentCard:
    primary = "\n".join(row.get("primaryTexts") or [])
    secondary = "\n".join(row.get("secondaryTexts") or [])
    return ComponentCard(
        id=f"sc.{slug(row['id'])}",
        type="strategy_card",
        ruleset=tag,  # type: ignore[arg-type]
        name=row["name"],
        aliases=[row["id"]],
        text=f"Primary:\n{primary}\nSecondary:\n{secondary}".strip(),
        stats={"initiative": row["initiative"]} if "initiative" in row else {},
        source_ref=f"asyncti4:strategy_card:{row['id']}",
    )


def _faction(row: dict, tag: str) -> ComponentCard:
    stats = {
        "home_system": row.get("homeSystem"),
        "home_planets": row.get("homePlanets"),
        "commodities": row.get("commodities"),
        "starting_fleet": row.get("startingFleet"),
        "starting_tech": row.get("startingTech"),
        "starting_tech_options": row.get("startingTechOptions"),
        "faction_tech": row.get("factionTech"),
        "abilities": row.get("abilities"),
        "leaders": row.get("leaders"),
        "promissory_notes": row.get("promissoryNotes"),
        "units": row.get("units"),
        "complexity": row.get("complexity"),
    }
    stats = {k: v for k, v in stats.items() if v not in (None, [], "")}
    text = (
        f"Home system {row.get('homeSystem')}. Commodities {row.get('commodities')}. "
        f"Abilities: {', '.join(row.get('abilities') or [])}."
    )
    return ComponentCard(
        id=f"faction.{slug(row['alias'])}",
        type="faction",
        ruleset=tag,  # type: ignore[arg-type]
        name=row["factionName"],
        aliases=[a for a in (row.get("alias"), row.get("shortName"), row.get("shortTag")) if a],
        text=text,
        faction=row["alias"],
        stats=stats,
        source_ref=f"asyncti4:faction:{row['alias']}",
        supersedes=[row["homebrewReplacesID"]] if row.get("homebrewReplacesID") else [],
    )


def _ability(row: dict, tag: str) -> ComponentCard:
    parts = [p for p in (row.get("permanentEffect"), row.get("windowEffect")) if p]
    window = row.get("window")
    return ComponentCard(
        id=f"ability.{slug(row['id'])}",
        type="ability",
        ruleset=tag,  # type: ignore[arg-type]
        name=row["name"],
        aliases=[row["id"]],
        timing=parse_window(window) if window else Timing(window="passive"),
        text="\n".join(([window] if window else []) + parts) if window else "\n".join(parts),
        faction=row.get("faction"),
        source_ref=f"asyncti4:ability:{row['id']}",
        supersedes=[row["homebrewReplacesID"]] if row.get("homebrewReplacesID") else [],
    )


def _public_objective(row: dict, tag: str) -> ComponentCard:
    points = row.get("points")
    return ComponentCard(
        id=f"obj.{slug(row['name'])}",
        type="objective",
        ruleset=tag,  # type: ignore[arg-type]
        name=row["name"],
        aliases=[row["alias"]],
        timing=Timing(window="passive", phase=(row.get("phase") or "").lower() or None),
        text=row.get("text", ""),
        stats={k: v for k, v in {"points": points, "stage": 2 if points == 2 else 1}.items() if v is not None},
        source_ref=f"asyncti4:objective:{row['alias']}",
    )


def _secret_objective(row: dict, tag: str) -> ComponentCard:
    return ComponentCard(
        id=f"so.{slug(row['name'])}",
        type="secret_objective",
        ruleset=tag,  # type: ignore[arg-type]
        name=row["name"],
        aliases=[row["alias"]],
        timing=Timing(window="passive", phase=(row.get("phase") or "").lower() or None),
        text=row.get("text", ""),
        stats={"points": row.get("points", 1)},
        source_ref=f"asyncti4:secret_objective:{row['alias']}",
    )


def _explore(row: dict, tag: str) -> ComponentCard:
    return ComponentCard(
        id=f"explore.{slug(row['name'])}",
        type="exploration",
        ruleset=tag,  # type: ignore[arg-type]
        name=row["name"],
        aliases=[row["id"]],
        text=row.get("text", ""),
        stats={
            k: v
            for k, v in {
                "explore_type": (row.get("type") or "").lower() or None,
                "resolution": row.get("resolution"),
                "attachment": row.get("attachmentId"),
            }.items()
            if v
        },
        source_ref=f"asyncti4:explore:{row['id']}",
    )


_ANOMALIES = {
    "isAsteroidField": "asteroid_field",
    "isAstroid": "asteroid_field",
    "isNebula": "nebula",
    "isNebulae": "nebula",
    "isSupernova": "supernova",
    "isGravityRift": "gravity_rift",
    "isRift": "gravity_rift",
}


def _tile(row: dict, tag: str) -> ComponentCard:
    anomalies = sorted({v for k, v in _ANOMALIES.items() if row.get(k)})
    stats = {
        "planets": [f"planet.{slug(p)}" for p in row.get("planets") or []],
        "wormholes": [w.lower() for w in row.get("wormholes") or []],
        "anomalies": anomalies,
        "hyperlane": bool(row.get("isHyperlane")),
        "tile_back": row.get("tileBack"),
    }
    if row.get("isFracture"):
        stats["fracture"] = True
    return ComponentCard(
        id=f"tile.{slug(row['id'])}",
        type="tile",
        ruleset=tag,  # type: ignore[arg-type]
        name=row["name"],
        aliases=[row["id"]],
        text="",
        stats={k: v for k, v in stats.items() if v not in (None, [], False)},
        source_ref=f"asyncti4:tile:{row['id']}",
    )


def _planet(row: dict, tag: str) -> ComponentCard:
    traits = row.get("planetTypes") or ([row["planetType"]] if row.get("planetType") else [])
    legendary = row.get("legendaryAbilityText")
    stats = {
        "resources": row.get("resources"),
        "influence": row.get("influence"),
        "traits": sorted({t.lower() for t in traits if t and t.upper() != "NONE"}),
        "tech_specialties": [t.lower() for t in row.get("techSpecialties") or []],
        "tile": row.get("tileId"),
        "home_of": row.get("factionHomeworld"),
        "legendary": row.get("legendaryAbilityName"),
        "space_cannon": row.get("spaceCannonHitsOn"),
        "space_cannon_dice": row.get("spaceCannonDieCount"),
    }
    return ComponentCard(
        id=f"planet.{slug(row['id'])}",
        type="planet",
        ruleset=tag,  # type: ignore[arg-type]
        name=row["name"],
        aliases=[a for a in (row.get("id"), row.get("shortName")) if a],
        text=legendary or "",
        stats={k: v for k, v in stats.items() if v not in (None, [], "")},
        source_ref=f"asyncti4:planet:{row['id']}",
    )


def parse_hyperlanes(text: str) -> dict[str, list[list[int]]]:
    """``83a60=0,0,..;..`` lines -> {"83a60": 6x6 side-connection matrix}."""
    out: dict[str, list[list[int]]] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        rows = [r for r in value.split(";") if r]
        try:
            matrix = [[int(x) for x in r.split(",")] for r in rows]
        except ValueError:
            continue
        if len(matrix) == 6 and all(len(r) == 6 for r in matrix):
            out[key.strip()] = matrix
    return out


CONVERTERS: dict[str, Callable[[dict, str], ComponentCard]] = {
    "action_cards": _action_card,
    "agendas": _agenda,
    "technologies": _technology,
    "units": _unit,
    "leaders": _leader,
    "promissory_notes": _promissory_note,
    "relics": _relic,
    "breakthroughs": _breakthrough,
    "strategy_cards": _strategy_card,
    "factions": _faction,
    "abilities": _ability,
    "public_objectives": _public_objective,
    "secret_objectives": _secret_objective,
    "explores": _explore,
}

# Folders that sit next to ``data`` rather than inside it.
SIBLING_CONVERTERS: dict[str, Callable[[dict, str], ComponentCard]] = {
    "systems": _tile,
    "planets": _planet,
}


def import_rows(
    kind: str, rows: Iterable[dict], include: Iterable[str] = DEFAULT_INCLUDE
) -> ImportReport:
    b = _Builder(frozenset(include))
    _feed(b, kind, rows)
    return b.finish()


# Homebrew rows that AsyncTI4 files under an official source tag.
_SKIP_ID_PREFIXES = ("absol_",)


def _feed(b: _Builder, kind: str, rows: Iterable[dict], conv=None) -> None:
    conv = conv or CONVERTERS[kind]
    for row in rows:
        rid = str(row.get("id") or row.get("alias") or "")
        if rid.startswith(_SKIP_ID_PREFIXES):
            b.report.skipped_sources["homebrew_prefix"] += 1
            continue
        tag = b.tag(row.get("source"))
        if tag is None:
            continue
        if kind == "units" and not row.get("name"):
            continue  # placeholders such as "nowarsun"
        b.add(conv(row, tag))


def import_checkout(
    data_dir: str | Path, include: Iterable[str] = DEFAULT_INCLUDE
) -> ImportReport:
    """Import every supported component folder, plus decks, under an AsyncTI4 ``data`` directory."""
    root = Path(data_dir)
    b = _Builder(frozenset(include))

    def rows_in(folder: str, base: Path = root):
        d = base / folder
        for f in sorted(d.glob("*.json")) if d.is_dir() else []:
            rows = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(rows, dict):
                rows = [rows]  # systems/ and planets/ hold one object per file
            if isinstance(rows, list):
                b.report.files.append(str(f.relative_to(base.parent)))
                yield from rows

    for kind in CONVERTERS:
        _feed(b, kind, rows_in(kind))
    for kind, conv in SIBLING_CONVERTERS.items():
        _feed(b, kind, rows_in(kind, root.parent), conv)

    lanes_file = root / "hyperlanes.properties"
    if lanes_file.exists():
        b.hyperlanes = parse_hyperlanes(lanes_file.read_text(encoding="utf-8"))

    for row in rows_in("decks"):
        card_type = DECK_TYPES.get(row.get("type", ""))
        tag = SOURCE_MAP.get((row.get("source") or "").lower())
        if card_type and tag in b.include:
            b.add_deck(row, tag, card_type)
    for row in rows_in("strategy_card_sets"):
        tag = SOURCE_MAP.get((row.get("source") or "").lower())
        if tag in b.include:
            b.add_deck(row, tag, "strategy_card")
    return b.finish()


def write_jsonl(records: Iterable[ComponentCard | Deck], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(r.model_dump_json(exclude_none=True) + "\n")
    return path
