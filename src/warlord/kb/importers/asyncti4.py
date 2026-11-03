"""Import AsyncTI4 bot component data into ComponentCard records.

Reads the JSON files under ``src/main/resources/data`` of a local checkout of
the AsyncTI4 ``TI4_map_generator_bot`` repository (action_cards, agendas,
technologies, units, ...). Nothing is downloaded here: point the importer at a
checkout, and keep the output under ``data/private/`` because the card text is
Fantasy Flight Games' copyright.

Only sources listed in ``include`` are kept; everything else (homebrew,
Discordant Stars, Codex until it is in scope) is skipped and counted.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from warlord.kb.records import ComponentCard, Timing

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

DEFAULT_INCLUDE = frozenset({"base", "pok"})


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
    lower = w.lower()
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
    skipped_sources: Counter = field(default_factory=Counter)
    merged_copies: int = 0
    files: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        by_type = Counter(r.type for r in self.records)
        return {
            "records": len(self.records),
            "by_type": dict(by_type),
            "merged_copies": self.merged_copies,
            "skipped_sources": dict(self.skipped_sources),
            "files": self.files,
        }


class _Builder:
    def __init__(self, include: frozenset[str]):
        self.include = include
        self.report = ImportReport()
        self._by_id: dict[str, ComponentCard] = {}

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

    def finish(self) -> ImportReport:
        self.report.records = list(self._by_id.values())
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
    )


CONVERTERS: dict[str, Callable[[dict, str], ComponentCard]] = {
    "action_cards": _action_card,
    "agendas": _agenda,
    "technologies": _technology,
    "units": _unit,
}


def import_rows(
    kind: str, rows: Iterable[dict], include: Iterable[str] = DEFAULT_INCLUDE
) -> ImportReport:
    b = _Builder(frozenset(include))
    _feed(b, kind, rows)
    return b.finish()


def _feed(b: _Builder, kind: str, rows: Iterable[dict]) -> None:
    conv = CONVERTERS[kind]
    for row in rows:
        tag = b.tag(row.get("source"))
        if tag is None:
            continue
        if kind == "units" and not row.get("name"):
            continue  # placeholders such as "nowarsun"
        b.add(conv(row, tag))


def import_checkout(
    data_dir: str | Path, include: Iterable[str] = DEFAULT_INCLUDE
) -> ImportReport:
    """Import every supported component folder under an AsyncTI4 ``data`` directory."""
    root = Path(data_dir)
    b = _Builder(frozenset(include))
    for kind in CONVERTERS:
        folder = root / kind
        if not folder.is_dir():
            continue
        for f in sorted(folder.glob("*.json")):
            rows = json.loads(f.read_text(encoding="utf-8"))
            if not isinstance(rows, list):
                continue
            b.report.files.append(str(f.relative_to(root)))
            _feed(b, kind, rows)
    return b.finish()


def write_jsonl(records: Iterable[ComponentCard], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(r.model_dump_json(exclude_none=True) + "\n")
    return path
