import json

from warlord.datasets.generate import generate_cases, write_set_b
from warlord.datasets.schemas import LegalityCase, split_by_game
from warlord.engine.validate import validate_action
from warlord.state.schema import PerspectiveView


def test_cases_are_engine_decided_balanced_and_replayable(state, kb, tmp_path):
    counts = write_set_b([state], kb, tmp_path, per_seat=6, seed=3)
    assert counts["cases"] > 0 and abs(counts["legal"] - counts["illegal"]) <= counts["cases"] // 2
    rows = [LegalityCase.model_validate_json(line) for line in (tmp_path / "set_b.jsonl").read_text().splitlines()]
    assert all(r.origin == "engine" for r in rows)
    for r in rows[:15]:
        snap = tmp_path / "states" / (r.state_ref.replace(":", "_").replace("@", "_") + ".json")
        view = PerspectiveView.model_validate(json.loads(snap.read_text()))
        assert validate_action(view, r.action, kb=kb).legal == r.legal  # replays to the same verdict
        if not r.legal:
            assert r.violations and r.violations[0].note


def test_generation_is_deterministic(state, kb):
    a = [c.action for c, _ in generate_cases(state, kb, per_seat=4, seed=1)]
    b = [c.action for c, _ in generate_cases(state, kb, per_seat=4, seed=1)]
    assert a == b and a


def test_split_keeps_each_game_on_one_side(state, kb):
    cases = [c for c, _ in generate_cases(state, kb, per_seat=4, seed=1)]
    train, test = split_by_game(cases)
    assert not ({c.game_id for c in train} & {c.game_id for c in test})
