"""Game state schema.

One omniscient :class:`GameState` is stored per game. The agent never sees it
directly: :func:`warlord.state.perspective.project` turns it into a
:class:`PerspectiveView` for one seat, in which everything that seat cannot
know is reduced to a count.

Field names follow the design doc (schema_version 0.1). Tile positions use the
AsyncTI4 ring numbering so imported snapshots need no remapping. Adjacency is
derived by the engine, never stored.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from warlord.ruleset import Ruleset

SCHEMA_VERSION = "0.1"

PlayerId = str
UnitCounts = dict[str, int]  # unit type -> count, e.g. {"carrier": 2}

Phase = Literal["setup", "strategy", "action", "status", "agenda", "game_over"]
LeaderStatus = Literal["locked", "unlocked", "ready", "exhausted", "purged"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- turn


class OpenWindow(_Model):
    """A "when"/"after" timing window that abilities can currently be played into."""

    timing: Literal["when", "after", "before", "start", "end"]
    event: str
    awaiting: list[PlayerId] = Field(default_factory=list)


class ResolvingEntry(_Model):
    """One ability on the resolution stack (last entry resolves first)."""

    source: str  # card or ability id
    player: PlayerId
    targets: list[str] = Field(default_factory=list)


class TurnState(_Model):
    round: int = 1
    phase: Phase = "strategy"
    # Sub-step, e.g. "tactical.movement", "tactical.space_cannon_offense",
    # "tactical.space_combat", "tactical.invasion", "tactical.production".
    step: str | None = None
    active_player: PlayerId | None = None
    speaker: PlayerId | None = None
    open_window: OpenWindow | None = None
    resolving: list[ResolvingEntry] = Field(default_factory=list)
    # Usage flags, "<player>:<ability id>".
    used_this_turn: list[str] = Field(default_factory=list)
    used_this_round: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------- map


class PlanetState(_Model):
    controller: PlayerId | None = None
    exhausted: bool = False
    attachments: list[str] = Field(default_factory=list)
    units: dict[PlayerId, UnitCounts] = Field(default_factory=dict)


class SystemState(_Model):
    position: str  # AsyncTI4 ring position, e.g. "201"
    tile: str  # tile id
    wormholes: list[str] = Field(default_factory=list)
    anomaly: Literal["asteroid_field", "nebula", "supernova", "gravity_rift"] | None = None
    hyperlane: bool = False
    command_tokens: list[PlayerId] = Field(default_factory=list)
    space: dict[PlayerId, UnitCounts] = Field(default_factory=dict)
    damaged: dict[PlayerId, UnitCounts] = Field(default_factory=dict)
    planets: dict[str, PlanetState] = Field(default_factory=dict)


# -------------------------------------------------------------------------- players


class CommandTokens(_Model):
    tactic: int = 3
    fleet: int = 3
    strategy: int = 2


class Commodities(_Model):
    current: int = 0
    max: int = 3


class OwnedTech(_Model):
    id: str
    exhausted: bool = False


class HeldStrategyCard(_Model):
    id: str
    primary_used: bool = False
    exhausted: bool = False


class Leaders(_Model):
    agent: LeaderStatus = "ready"
    commander: LeaderStatus = "locked"
    hero: LeaderStatus = "locked"


class HiddenCounts(_Model):
    action_cards: int = 0
    promissory_notes: int = 0
    secret_objectives: int = 0


class PrivateHoldings(_Model):
    """What only the owning player can see. Omniscient state only."""

    action_cards: list[str] = Field(default_factory=list)
    promissory_notes: list[str] = Field(default_factory=list)
    secret_objectives: list[str] = Field(default_factory=list)  # unscored

    def counts(self) -> HiddenCounts:
        return HiddenCounts(
            action_cards=len(self.action_cards),
            promissory_notes=len(self.promissory_notes),
            secret_objectives=len(self.secret_objectives),
        )


class ScoredObjective(_Model):
    id: str
    secret: bool = False
    # A scored secret is normally revealed; imports from partial logs may know
    # that a secret was scored without knowing which one.
    revealed: bool = True


class _PlayerCommon(_Model):
    faction: str
    vp: int = 0
    command_tokens: CommandTokens = Field(default_factory=CommandTokens)
    trade_goods: int = 0
    commodities: Commodities = Field(default_factory=Commodities)
    techs: list[OwnedTech] = Field(default_factory=list)
    unit_upgrades: list[str] = Field(default_factory=list)
    strategy_cards: list[HeldStrategyCard] = Field(default_factory=list)
    passed: bool = False
    leaders: Leaders = Field(default_factory=Leaders)
    relics: list[str] = Field(default_factory=list)
    promissory_in_play: list[str] = Field(default_factory=list)


class PlayerState(_PlayerCommon):
    """Omniscient player record."""

    scored: list[ScoredObjective] = Field(default_factory=list)
    private: PrivateHoldings = Field(default_factory=PrivateHoldings)


class PlayerView(_PlayerCommon):
    """Player record as another seat (or the same seat) sees it."""

    scored: list[str] = Field(default_factory=list)
    hidden: HiddenCounts = Field(default_factory=HiddenCounts)


# ----------------------------------------------------------------- objectives/laws


class ObjectivesState(_Model):
    public_revealed: list[str] = Field(default_factory=list)
    public_unrevealed: list[str] = Field(default_factory=list)  # hidden deck order


class ObjectivesView(_Model):
    public_revealed: list[str] = Field(default_factory=list)
    public_unrevealed: int = 0


class LawInPlay(_Model):
    id: str
    elected: str | None = None  # elected player/planet/outcome, if any


class Discards(_Model):
    action_cards: list[str] = Field(default_factory=list)
    agendas: list[str] = Field(default_factory=list)


class LogEvent(_Model):
    seq: int
    round: int
    step: str | None = None
    actor: PlayerId | None = None
    kind: str
    data: dict = Field(default_factory=dict)
    # None means public. Otherwise only these seats may see the event.
    visible_to: list[PlayerId] | None = None


class Meta(_Model):
    game_id: str
    source: Literal["asyncti4", "manual", "engine", "test"] = "manual"
    players: int
    vp_target: int = 10


# ---------------------------------------------------------------------- top level


class GameState(_Model):
    """Omniscient game record. Never handed to the agent."""

    schema_version: str = SCHEMA_VERSION
    ruleset: Ruleset = Field(default_factory=Ruleset)
    meta: Meta
    turn: TurnState = Field(default_factory=TurnState)
    map: list[SystemState] = Field(default_factory=list)
    players: dict[PlayerId, PlayerState]
    objectives: ObjectivesState = Field(default_factory=ObjectivesState)
    laws: list[LawInPlay] = Field(default_factory=list)
    discards: Discards = Field(default_factory=Discards)
    agenda_deck: list[str] = Field(default_factory=list)  # hidden
    log: list[LogEvent] = Field(default_factory=list)

    def system(self, position: str) -> SystemState | None:
        return next((s for s in self.map if s.position == position), None)


class Hand(_Model):
    """The viewing player's own hidden cards."""

    action_cards: list[str] = Field(default_factory=list)
    promissory_notes: list[str] = Field(default_factory=list)
    secret_objectives: list[str] = Field(default_factory=list)


class PerspectiveView(_Model):
    """What one seat can know. The only state shape the agent receives."""

    schema_version: str = SCHEMA_VERSION
    viewer: PlayerId
    hand: Hand
    ruleset: Ruleset
    meta: Meta
    turn: TurnState
    map: list[SystemState]
    players: dict[PlayerId, PlayerView]
    objectives: ObjectivesView
    laws: list[LawInPlay]
    discards: Discards
    log: list[LogEvent]

    def system(self, position: str) -> SystemState | None:
        return next((s for s in self.map if s.position == position), None)
