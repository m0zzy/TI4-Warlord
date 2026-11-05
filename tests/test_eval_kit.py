import json

from warlord.engine.validate import validate_action
from warlord.evals import kit
from warlord.state.perspective import project
from warlord.state.schema import GameState


def test_question_set_is_well_formed():
    qs = kit.load_questions()
    assert len(qs) == 50 and len({q["id"] for q in qs}) == 50
    assert sum(q["kind"] == "legality" for q in qs) == 8
    for q in qs:
        assert q["question"] and q["answer"] and q["refs"] and q["review"] in ("draft", "approved", "rejected")
        assert q["expected_label"] in ("engine", "unverified")


def test_legality_gold_answers_match_the_engine(kb):
    state = GameState.model_validate_json(kit.EVAL_GAME.read_text())
    view = project(state, "p3")
    for q in kit.load_questions():
        if q["kind"] == "legality":
            assert validate_action(view, q["action"], kb=kb).legal == q["expected_legal"], q["id"]


def test_install_and_template(tmp_path):
    assert kit.install_game(tmp_path).exists()
    path = kit.write_template(kit.load_questions()[:3], tmp_path / "answers.jsonl")
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert [r["id"] for r in rows] == ["a.001", "a.002", "a.003"] and rows[0]["correct"] is None


def test_cited_ids_and_labels():
    text = "No (lrr.89.2, see also ac.sabotage). Engine-verified."
    assert kit.cited_ids(text) == ["lrr.89.2", "ac.sabotage"]
    assert kit.detect_label(text) == "engine"
    assert kit.detect_label("Unverified: from the rules text.") == "unverified"
    assert kit.detect_label("Yes.") is None


def test_score(kb):
    qs = [
        {"id": "x1", "question": "?", "answer": "", "refs": ["lrr.sample.movement"], "expected_label": "unverified", "kind": "rules"},
        {"id": "x2", "question": "?", "answer": "", "refs": ["lrr.sample.passing"], "expected_label": "engine", "kind": "legality"},
        {"id": "x3", "question": "?", "answer": "", "refs": ["lrr.sample.timing"], "expected_label": "unverified", "kind": "rules"},
    ]
    answers = [
        {"id": "x1", "answer": "No, ships can't leave (lrr.sample.movement). Unverified: rules text.", "correct": True},
        {"id": "x2", "answer": "No, you must use your card first (lrr.sample.passing, lrr.999). Unverified.", "correct": True},
        {"id": "x3", "answer": "", "correct": None},
    ]
    report = kit.score(answers, qs, kb)
    assert report["answered"] == 2 and report["graded"] == 2
    m = report["metrics"]
    assert m["accuracy"]["value"] == 1.0
    assert m["citation_validity"]["value"] == 0.5  # lrr.999 does not exist
    assert m["label_honesty"]["value"] == 0.5  # x2 should be engine-verified
    assert [f["id"] for f in report["failures"]] == ["x2"]
    assert "label_honesty" in kit.score_markdown(report)
