import pytest

from warlord.state import HIDDEN_SECRET, hidden_from, project
from warlord.state.perspective import UnknownViewer


def test_viewer_sees_own_hand(view):
    assert view.viewer == "p3"
    assert view.hand.action_cards == ["ac.sabotage"]
    assert view.hand.secret_objectives == ["so.control_the_region"]


def test_opponents_hidden_cards_are_counts(view):
    p2 = view.players["p2"]
    assert p2.hidden.action_cards == 2
    assert p2.hidden.promissory_notes == 1
    dumped = view.model_dump_json()
    for secret in ("ac.direct_hit", "pn.jolnar.research_agreement", "so.monument_to_the_ages"):
        assert secret not in dumped


def test_unrevealed_scored_secret_is_masked(view, state):
    assert view.players["p2"].scored == ["obj.expand_borders", HIDDEN_SECRET]
    # The owner still sees it.
    own = project(state, "p2")
    assert "so.destroy_their_greatest_ship" in own.players["p2"].scored


def test_decks_are_counts(view):
    assert view.objectives.public_unrevealed == 2
    dumped = view.model_dump_json()
    assert "obj.develop_weaponry" not in dumped
    assert "agenda.mutiny" not in dumped


def test_private_log_events_filtered(state):
    assert all(e.visible_to is None for e in project(state, "p3").log)
    assert any(e.kind == "draw_action_card" for e in project(state, "p2").log)


def test_hidden_from_excludes_cards_viewer_holds(state):
    hidden = hidden_from(state, "p3")
    assert "ac.direct_hit" in hidden
    assert "ac.sabotage" not in hidden  # p3 holds a copy, so naming it is no leak
    assert "so.destroy_their_greatest_ship" in hidden


def test_unknown_viewer(state):
    with pytest.raises(UnknownViewer):
        project(state, "p9")
