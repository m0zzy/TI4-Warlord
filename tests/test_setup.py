import json

import pytest

from warlord.setup import SetupError, resolve_asyncti4, run_setup


def _asyncti4(tmp_path):
    data = tmp_path / "repo" / "src" / "main" / "resources" / "data"
    (data / "action_cards").mkdir(parents=True)
    (data / "action_cards" / "a.json").write_text(json.dumps([
        {"alias": "sabo1", "name": "Sabotage", "phase": "Any", "window": "When another player plays an action card", "text": "T", "source": "base"}]))
    return tmp_path / "repo"


def test_resolve_accepts_checkout_or_data_folder(tmp_path):
    repo = _asyncti4(tmp_path)
    assert resolve_asyncti4(repo).name == "data"
    assert resolve_asyncti4(repo / "src" / "main" / "resources" / "data").name == "data"
    with pytest.raises(SetupError):
        resolve_asyncti4(tmp_path / "nowhere")


def test_setup_writes_and_flags_what_is_missing(tmp_path):
    report = run_setup(tmp_path / "data", asyncti4=_asyncti4(tmp_path))
    assert report.written == {"components": 1, "decks": 0}
    assert (tmp_path / "data" / "kb" / "components.asyncti4.jsonl").exists()
    assert (tmp_path / "data" / "games").is_dir()
    checks = {c["check"]: c["ok"] for c in report.checks}
    assert checks["lrr_topics"] is False and checks["components"] is False and checks["engine_rule_refs"] is False
    assert not report.ok


def test_setup_with_nothing(tmp_path):
    report = run_setup(tmp_path / "data")
    assert [c["check"] for c in report.checks] == ["knowledge_base"] and not report.ok


def test_missing_pdf(tmp_path):
    pytest.importorskip("pdfminer")
    with pytest.raises(SetupError):
        run_setup(tmp_path / "data", lrr=tmp_path / "missing.pdf")
