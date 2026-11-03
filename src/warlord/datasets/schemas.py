"""Record types for the six example sets (A-F) and the by-game split.

Each record carries ``ruleset`` and, where it comes from a game, ``game_id`` so
the train/test split never puts one game on both sides.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from warlord.ruleset import LrrEdition

DecisionType = Literal[
    "tactical_action",
    "strategy_card_pick",
    "production",
    "agenda_vote",
    "window_response",
    "transaction_offer",
    "status_scoring",
]


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    ruleset: LrrEdition = "pok"
    game_id: str | None = None  # source game, for the split


class RulesQA(_Record):
    """Set A."""

    set: Literal["A"] = "A"
    question: str
    state_ref: str | None = None
    answer: str
    refs: list[str]
    authority: str


class Violation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    topic: str
    refs: list[str] = Field(default_factory=list)
    note: str | None = None


class LegalityCase(_Record):
    """Set B. About half should be illegal."""

    set: Literal["B"] = "B"
    state_ref: str
    action: dict
    legal: bool
    violations: list[Violation] = Field(default_factory=list)
    origin: Literal["engine", "hand_written"] = "hand_written"


class TimingCase(_Record):
    """Set C."""

    set: Literal["C"] = "C"
    state_ref: str
    window: dict  # OpenWindow as a dict
    player: str
    available: list[str]  # card / ability ids playable now
    unavailable: dict[str, str] = Field(default_factory=dict)  # id -> why not
    refs: list[str] = Field(default_factory=list)


class Alternative(BaseModel):
    model_config = ConfigDict(extra="forbid")
    option: str
    why_not: str


class Outcome(BaseModel):
    """Weak labels only: a move by the eventual winner is not automatically good."""

    model_config = ConfigDict(extra="forbid")
    vp_change_this_round: int | None = None
    won_game: bool | None = None


class AdvisorDecision(_Record):
    """Set D."""

    set: Literal["D"] = "D"
    state_ref: str
    viewer: str
    decision: DecisionType
    options: list[str]
    chosen: str
    alternatives: list[Alternative] = Field(default_factory=list)
    rationale: str
    refs: list[str] = Field(default_factory=list)
    annotator: str | None = None
    outcome: Outcome | None = None


class ContrastivePair(_Record):
    """Set E."""

    set: Literal["E"] = "E"
    state_ref: str
    viewer: str
    move_a: dict | str
    move_b: dict | str
    better: Literal["a", "b", "tie"]
    why: str
    annotator: str | None = None


class TeachingExplanation(_Record):
    """Set F."""

    set: Literal["F"] = "F"
    state_ref: str | None = None
    question: str
    answer: str
    refs: list[str] = Field(default_factory=list)


ANY_RECORD = RulesQA | LegalityCase | TimingCase | AdvisorDecision | ContrastivePair | TeachingExplanation
_BY_SET = {"A": RulesQA, "B": LegalityCase, "C": TimingCase, "D": AdvisorDecision, "E": ContrastivePair, "F": TeachingExplanation}

T = TypeVar("T", bound=_Record)


def parse_record(data: dict) -> _Record:
    return _BY_SET[data["set"]].model_validate(data)


def load_jsonl(path: str | Path) -> list[_Record]:
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(parse_record(json.loads(line)))
    return out


def game_of(record: _Record) -> str:
    """The grouping key for the split: the source game, else the state ref's game, else the record."""
    if record.game_id:
        return record.game_id
    ref = getattr(record, "state_ref", None)
    if ref:
        # "asyncti4:pbd00000@r3.strategy" -> "asyncti4:pbd00000"
        return ref.split("@", 1)[0]
    return f"record:{record.id}"


def split_by_game(records: Iterable[T], test_fraction: float = 0.2, salt: str = "warlord") -> tuple[list[T], list[T]]:
    """Deterministic split by game: hash each game id into [0, 1)."""
    train, test = [], []
    for r in records:
        h = hashlib.sha256(f"{salt}:{game_of(r)}".encode()).digest()
        bucket = int.from_bytes(h[:8], "big") / 2**64
        (test if bucket < test_fraction else train).append(r)
    return train, test
