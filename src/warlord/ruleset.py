"""Ruleset tags.

Every rule, card, state and example carries a ruleset tag so content from
different releases never mixes. A record tagged ``base`` is valid in a
base + PoK game; a record tagged ``thunders_edge`` is only valid in a game
with Thunder's Edge. Decks narrow this further to the printing a game uses.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Content sources a single record can belong to.
ContentTag = Literal["base", "pok", "codex", "thunders_edge"]

# Which Living Rules Reference edition governs a game.
LrrEdition = Literal["base", "pok", "thunders_edge"]


class Ruleset(BaseModel):
    """The content a game is played with."""

    model_config = ConfigDict(extra="forbid")

    base: bool = True
    pok: bool = True
    codex: bool = False
    thunders_edge: bool = False
    lrr: LrrEdition = "pok"
    # Card type -> deck id (e.g. "action_card" -> "deck.action_cards_te"). When
    # set and the deck is loaded, lookups of that type only return the deck's
    # cards. Faction cards are never filtered by decks.
    decks: dict[str, str | list[str]] = Field(default_factory=dict)

    def deck_ids(self, card_type: str) -> list[str]:
        ref = self.decks.get(card_type)
        if ref is None:
            return []
        return [ref] if isinstance(ref, str) else list(ref)

    def allows(self, tag: str) -> bool:
        """True when content tagged ``tag`` is legal in this ruleset."""
        return bool(getattr(self, tag, False))

    def tags(self) -> list[str]:
        return [t for t in ("base", "pok", "codex", "thunders_edge") if self.allows(t)]


# PoK as AsyncTI4 sets it up by default. Its default tech deck already folds in
# the Codex updates, so Codex content is allowed, but only through these decks.
POK_RULESET = Ruleset(
    codex=True,
    decks={
        "action_card": "deck.action_cards_pok",
        "technology": "deck.techs_pok_c4",
        "relic": "deck.relics_pok",
        "agenda": "deck.agendas_pok",
        "strategy_card": "deck.sc_set.pok",
        "objective": ["deck.public_stage_1_objectives_pok", "deck.public_stage_2_objectives_pok"],
        "secret_objective": "deck.secret_objectives_pok",
        "exploration": "deck.explores_pok",
    },
)

# Thunder's Edge + PoK as AsyncTI4 sets it up by default: the TE action card and
# relic decks, the PoK + Codex tech deck, PoK agendas, objectives and explores, and
# the TE strategy card set. Codex content enters only through these decks.
THUNDERS_EDGE_RULESET = Ruleset(
    base=True,
    pok=True,
    codex=True,
    thunders_edge=True,
    lrr="thunders_edge",
    decks={
        "action_card": "deck.action_cards_te",
        "technology": "deck.techs_pok_c4",
        "relic": "deck.relics_pok_te",
        "agenda": "deck.agendas_pok",
        "strategy_card": "deck.sc_set.te",
        "objective": ["deck.public_stage_1_objectives_pok", "deck.public_stage_2_objectives_pok"],
        "secret_objective": "deck.secret_objectives_pok",
        "exploration": "deck.explores_pok",
    },
)

V1_RULESETS = {"pok": POK_RULESET, "thunders_edge": THUNDERS_EDGE_RULESET}
