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


def test_clause_citations_count_as_their_rule(kb):
    from warlord.tools.ruling_check import base_id

    assert base_id("lrr.58.4c") == "lrr.58.4" and base_id("lrr.58.4(c)") == "lrr.58.4" and base_id("ac.sabotage") == "ac.sabotage"
    qs = [{"id": "x", "question": "?", "answer": "", "refs": ["lrr.sample.movement"], "expected_label": "unverified", "kind": "rules"}]
    from warlord.kb.store import KnowledgeBase

    own = KnowledgeBase()
    own.add_many(kb.records.values())
    own.add({"kind": "rule", "id": "lrr.58.4", "ruleset": "base", "title": "Move Ships", "text": "t"})
    own.finalize()
    report = kit.score([{"id": "x", "answer": "No (lrr.58.4c, lrr.sample.movement). Unverified.", "correct": True}], qs, own)
    assert report["metrics"]["citation_validity"]["value"] == 1.0


def test_runner_parses_claude_stream(tmp_path):
    import json as _json
    from types import SimpleNamespace

    from warlord.evals import runner

    events = [
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "mcp__warlord__rules_lookup"}]}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "thinking aloud"}]}},
        {"type": "result", "result": "No. (lrr.89.2) Engine-verified.", "num_turns": 3, "total_cost_usd": 0.01, "is_error": False},
    ]
    fake = lambda cmd, **kw: SimpleNamespace(stdout="\n".join(_json.dumps(e) for e in events), stderr="", returncode=0)
    runner._claude = lambda: "claude"
    qs = [{"id": "a.001", "question": "Q?"}, {"id": "a.002", "question": "Q2?"}]
    path = tmp_path / "answers.jsonl"
    assert runner.run(qs, path, tmp_path, runner=fake, progress=lambda *_: None)["asked"] == 2
    rows = [_json.loads(line) for line in path.read_text().splitlines()]
    assert rows[0]["answer"] == "No. (lrr.89.2) Engine-verified." and rows[0]["tools_used"] == ["rules_lookup"]
    assert runner.run(qs, path, tmp_path, runner=fake, progress=lambda *_: None)["asked"] == 0  # already answered

    seq = iter(["y", "n"])
    gold = [{"id": "a.001", "question": "Q?", "answer": "No", "refs": ["lrr.89.2"], "topic": "t"},
            {"id": "a.002", "question": "Q2?", "answer": "Yes", "refs": ["lrr.1"], "topic": "t"}]
    assert runner.grade(gold, path, read=lambda _: next(seq), show=lambda *_: None)["graded"] == 2
    assert [_json.loads(line)["correct"] for line in path.read_text().splitlines()] == [True, False]
