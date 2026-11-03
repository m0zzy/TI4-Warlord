"""Knowledge base record types. Every record has an id the agent can cite."""

from __future__ import annotations

from enum import IntEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from warlord.ruleset import ContentTag


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Authority(IntEnum):
    """Precedence, highest first. When two records conflict the agent cites the
    higher one and mentions the conflict.

    Card text beats the LRR (both apply when both can); the LRR beats the Learn
    to Play booklet; the LRR FAQ beats older developer rulings.
    """

    card_text = 6
    lrr = 5
    official_faq = 4
    developer = 3
    learn_to_play = 2
    community = 1

    @classmethod
    def parse(cls, value: str | int | Authority) -> Authority:
        if isinstance(value, Authority):
            return value
        if isinstance(value, int):
            return cls(value)
        return cls[value]


AuthorityName = Literal[
    "card_text", "lrr", "official_faq", "developer", "learn_to_play", "community"
]


class RuleChunk(_Model):
    """One numbered LRR rule (or sub-rule)."""

    kind: Literal["rule"] = "rule"
    id: str  # "lrr.<number>" or "lrr.<number>.<sub>"
    ruleset: ContentTag
    title: str
    text: str
    parent: str | None = None
    related: list[str] = Field(default_factory=list)
    authority: AuthorityName = "lrr"


class Timing(_Model):
    window: Literal["action", "when", "after", "before", "start", "end", "any", "passive"]
    trigger: str | None = None
    phase: str | None = None  # "action", "agenda", "status", "strategy", "any"


class ErrataPatch(_Model):
    """A dated correction applied over a component's text."""

    kind: Literal["errata"] = "errata"
    id: str
    target: str  # component id
    date: str  # ISO date
    field: Literal["text", "name", "timing"] = "text"
    old: str | None = None  # if set, replace this substring; else replace the field
    new: str | dict
    source: str | None = None


class ComponentCard(_Model):
    kind: Literal["component"] = "component"
    id: str  # "<type prefix>.<slug>", e.g. "ac.sabotage"
    type: Literal[
        "action_card",
        "agenda",
        "objective",
        "secret_objective",
        "promissory_note",
        "technology",
        "unit",
        "faction",
        "leader",
        "relic",
        "exploration",
        "strategy_card",
        "tile",
        "planet",
        "ability",
        "breakthrough",
    ]
    ruleset: ContentTag
    name: str
    aliases: list[str] = Field(default_factory=list)
    timing: Timing | None = None
    text: str
    faction: str | None = None
    stats: dict = Field(default_factory=dict)  # units/planets: numeric fields
    # Structured effect if the engine implements the card. None = text-only
    # coverage, which tells the agent to flag answers as unverified.
    engine_ops: dict | None = None
    errata: list[str] = Field(default_factory=list)  # ids of applied patches
    source_ref: str | None = None  # where the record came from (e.g. asyncti4 alias)


class Deck(_Model):
    """Which cards make up one deck (or strategy card set) in a game mode.

    Games in AsyncTI4 draw from named decks, e.g. ``action_cards_te`` for
    Thunder's Edge + PoK. A ruleset that names a deck for a card type limits
    lookups of that type to the deck's cards, so the right printing of a card
    (base, Codex or Thunder's Edge) is the one returned.
    """

    kind: Literal["deck"] = "deck"
    id: str  # "deck.<alias>"
    ruleset: ContentTag
    name: str
    card_type: str  # ComponentCard.type it holds
    cards: list[str] = Field(default_factory=list)  # component ids
    unresolved: list[str] = Field(default_factory=list)  # source aliases with no imported card
    source_ref: str | None = None


class Ruling(_Model):
    kind: Literal["ruling"] = "ruling"
    id: str
    ruleset: ContentTag = "pok"
    question: str
    answer: str
    refs: list[str] = Field(default_factory=list)
    authority: AuthorityName
    date: str | None = None
    supersedes: list[str] = Field(default_factory=list)
    source: str | None = None


class StrategyNote(_Model):
    kind: Literal["strategy_note"] = "strategy_note"
    id: str
    ruleset: ContentTag = "pok"
    title: str
    text: str
    factions: list[str] = Field(default_factory=list)
    stage: Literal["opening", "early", "mid", "late", "any"] = "any"
    author: str | None = None


Record = RuleChunk | ComponentCard | Ruling | StrategyNote


def authority_of(record: Record) -> Authority:
    if isinstance(record, ComponentCard):
        return Authority.card_text
    if isinstance(record, StrategyNote):
        return Authority.community
    return Authority.parse(record.authority)


def citation_label(record: Record) -> str:
    if isinstance(record, RuleChunk):
        return f"{record.id} {record.title}"
    if isinstance(record, ComponentCard):
        return f"{record.id} ({record.name})"
    if isinstance(record, Ruling):
        return f"{record.id} [{record.authority}]"
    return f"{record.id} {record.title}"
