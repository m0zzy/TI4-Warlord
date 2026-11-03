from warlord.datasets.schemas import AdvisorDecision, LegalityCase, game_of, parse_record, split_by_game
from warlord.evals.checks import (
    citation_validity,
    find_leaks,
    hidden_information_leaks,
    legality_agreement,
    names_from_kb,
    text_only_honesty,
)


def test_parse_set_d_record():
    r = parse_record({
        "set": "D", "id": "adv.000231", "state_ref": "asyncti4:pbd00000@r3.strategy", "viewer": "p3",
        "decision": "strategy_card_pick", "options": ["leadership", "warfare"], "chosen": "warfare",
        "alternatives": [{"option": "leadership", "why_not": "..."}], "rationale": "...",
        "outcome": {"vp_change_this_round": 1, "won_game": False},
    })
    assert isinstance(r, AdvisorDecision) and game_of(r) == "asyncti4:pbd00000"


def test_split_keeps_games_together():
    recs = [
        LegalityCase(id=f"b{i}", state_ref=f"asyncti4:g{i % 10}@r1", action={}, legal=bool(i % 2))
        for i in range(200)
    ]
    train, test = split_by_game(recs)
    assert {game_of(r) for r in train}.isdisjoint({game_of(r) for r in test})
    assert train and test
    assert split_by_game(recs) == (train, test)  # deterministic


def test_citation_validity(kb):
    res = citation_validity([
        {"answer": "a", "citations": [{"id": "lrr.sample.movement"}]},
        {"answer": "b", "citations": [{"id": "lrr.999"}]},
        {"answer": "c", "citations": []},
    ], kb)
    assert (res.total, res.passed) == (3, 1)


def test_legality_agreement_only_counts_engine_coverage():
    res = legality_agreement([
        (False, {"coverage": "engine", "legal": False}),
        (True, {"coverage": "engine", "legal": False}),
        (True, {"coverage": "text_only", "legal": None}),
    ])
    assert (res.total, res.passed) == (2, 1)


def test_text_only_honesty():
    res = text_only_honesty([
        {"verified": False, "coverage": []},
        {"verified": True, "coverage": [{"tool": "validate_action", "coverage": "text_only"}]},
        {"verified": True, "coverage": [{"tool": "validate_action", "coverage": "engine"}]},
    ])
    assert (res.total, res.passed) == (2, 1)
    assert not res.meets_bar


def test_leak_detection(state, kb):
    names = names_from_kb(kb)
    assert find_leaks("p2 is probably holding Direct Hit, so keep the dreadnought healthy", state, "p3", names) == ["ac.direct_hit"]
    assert find_leaks("You can Sabotage their next card", state, "p3", names) == []  # p3 holds Sabotage
    res = hidden_information_leaks([(state, "p3", "Attack 201 with the carrier group.")], names)
    assert res.passed == 1
