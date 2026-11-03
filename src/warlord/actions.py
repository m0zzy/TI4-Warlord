"""Canonical action format, shared by validate_action, apply_action and the example data."""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

UnitCounts = dict[str, int]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Move(_Model):
    from_: str = Field(alias="from")
    units: UnitCounts
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Invade(_Model):
    planet: str
    infantry: int = 0
    mech: int = 0


class Produce(_Model):
    where: str  # system position or planet name with the producing unit
    units: UnitCounts
    pay_with: list[str] = Field(default_factory=list)  # planet names / "tg:N"


class TacticalAction(_Model):
    type: Literal["tactical"] = "tactical"
    player: str
    activate: str
    moves: list[Move] = Field(default_factory=list)
    invade: list[Invade] = Field(default_factory=list)
    produce: list[Produce] = Field(default_factory=list)


class StrategicAction(_Model):
    type: Literal["strategic"] = "strategic"
    player: str
    card: str
    primary: dict = Field(default_factory=dict)  # card-specific choices


class ComponentAction(_Model):
    type: Literal["component"] = "component"
    player: str
    source: str  # action card, tech or ability id
    choices: dict = Field(default_factory=dict)


class Transaction(_Model):
    type: Literal["transaction"] = "transaction"
    player: str
    with_player: str
    give: dict = Field(default_factory=dict)
    receive: dict = Field(default_factory=dict)


class Pass(_Model):
    type: Literal["pass"] = "pass"
    player: str


class Respond(_Model):
    """Play into, or decline, the open timing window."""

    type: Literal["respond"] = "respond"
    player: str
    play: str | None = None  # card or ability id; None declines
    choices: dict = Field(default_factory=dict)


Action = Annotated[
    Union[TacticalAction, StrategicAction, ComponentAction, Transaction, Pass, Respond],
    Field(discriminator="type"),
]

ACTION_ADAPTER: TypeAdapter[Action] = TypeAdapter(Action)


def parse_action(data: dict) -> Action:
    return ACTION_ADAPTER.validate_python(data)


def action_json_schema() -> dict:
    return ACTION_ADAPTER.json_schema(by_alias=True)
