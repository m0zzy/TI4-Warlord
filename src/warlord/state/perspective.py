"""Perspective projection: omniscient state -> what one seat can know.

Rules:

* The viewer gets their own action cards, promissory notes and unscored
  secret objectives by id, in ``hand``.
* Every other hidden thing stays a count: opponents' hands, the unrevealed
  public objective deck, the agenda deck.
* Scored secrets show by id only once revealed; otherwise as
  ``obj.secret_hidden``.
* Log events with ``visible_to`` set are dropped for seats not listed.
"""

from __future__ import annotations

from warlord.state.schema import (
    GameState,
    Hand,
    ObjectivesView,
    PerspectiveView,
    PlayerState,
    PlayerView,
)

HIDDEN_SECRET = "obj.secret_hidden"


class UnknownViewer(ValueError):
    pass


def _player_view(player: PlayerState, *, is_viewer: bool) -> PlayerView:
    common = player.model_dump(exclude={"scored", "private"})
    scored = [
        s.id if (s.revealed or not s.secret or is_viewer) else HIDDEN_SECRET
        for s in player.scored
    ]
    return PlayerView(**common, scored=scored, hidden=player.private.counts())


def project(state: GameState, viewer: str) -> PerspectiveView:
    if viewer not in state.players:
        raise UnknownViewer(f"{viewer!r} is not a seat in game {state.meta.game_id}")

    me = state.players[viewer]
    return PerspectiveView(
        viewer=viewer,
        hand=Hand(
            action_cards=list(me.private.action_cards),
            promissory_notes=list(me.private.promissory_notes),
            secret_objectives=list(me.private.secret_objectives),
        ),
        ruleset=state.ruleset.model_copy(),
        meta=state.meta.model_copy(),
        turn=state.turn.model_copy(deep=True),
        map=[s.model_copy(deep=True) for s in state.map],
        players={
            pid: _player_view(p, is_viewer=(pid == viewer)) for pid, p in state.players.items()
        },
        objectives=ObjectivesView(
            public_revealed=list(state.objectives.public_revealed),
            public_unrevealed=len(state.objectives.public_unrevealed),
        ),
        laws=[law.model_copy() for law in state.laws],
        discards=state.discards.model_copy(deep=True),
        log=[
            e.model_copy(deep=True)
            for e in state.log
            if e.visible_to is None or viewer in e.visible_to
        ],
    )


def hidden_from(state: GameState, viewer: str) -> set[str]:
    """Every component id that exists in ``state`` but that ``viewer`` cannot see.

    Used by the leak checks: advice for ``viewer`` must never mention these.
    Ids the viewer can also see through a public route (for example a card that
    is both in an opponent's hand and, as another copy, in the discard) are
    excluded, so the set only holds ids whose mention is a genuine leak.
    """
    if viewer not in state.players:
        raise UnknownViewer(viewer)

    hidden: set[str] = set()
    for pid, p in state.players.items():
        if pid == viewer:
            continue
        hidden.update(p.private.action_cards)
        hidden.update(p.private.promissory_notes)
        hidden.update(p.private.secret_objectives)
        hidden.update(s.id for s in p.scored if s.secret and not s.revealed)
    hidden.update(state.objectives.public_unrevealed)
    hidden.update(state.agenda_deck)

    visible = _strings(project(state, viewer).model_dump())
    return hidden - visible


def _strings(node: object) -> set[str]:
    """All string keys and values anywhere in a dumped model."""
    out: set[str] = set()
    stack = [node]
    while stack:
        cur = stack.pop()
        if isinstance(cur, str):
            out.add(cur)
        elif isinstance(cur, dict):
            out.update(k for k in cur if isinstance(k, str))
            stack.extend(cur.values())
        elif isinstance(cur, (list, tuple)):
            stack.extend(cur)
    return out
