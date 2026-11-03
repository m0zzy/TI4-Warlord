"""Ruleset tags.

Every rule, card, state and example carries a ruleset tag so content from
different releases never mixes. A record tagged ``base`` is valid in a
base + PoK game; a record tagged ``thunders_edge`` is not.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

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

    def allows(self, tag: str) -> bool:
        """True when content tagged ``tag`` is legal in this ruleset."""
        return bool(getattr(self, tag, False))

    def tags(self) -> list[str]:
        return [t for t in ("base", "pok", "codex", "thunders_edge") if self.allows(t)]


V1_RULESET = Ruleset()
