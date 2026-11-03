import pytest

from warlord.kb.records import Authority, ComponentCard, ErrataPatch
from warlord.kb.store import DuplicateRecord, KnowledgeBase, apply_errata
from warlord.ruleset import Ruleset


def test_errata_applied_on_load(kb):
    card = kb.get("tech.gravity_drive")
    assert "1 ship gets" in card.text
    assert card.errata == ["errata.sample.0001"]


def test_errata_replacement_must_match():
    card = ComponentCard(id="ac.x", type="action_card", ruleset="base", name="X", text="abc")
    with pytest.raises(ValueError):
        apply_errata(card, ErrataPatch(id="e1", target="ac.x", date="2020-01-01", old="zzz", new="y"))


def test_card_lookup_tolerates_misspelling(kb):
    out = kb.card_lookup("sabotge")
    assert out["match"]["id"] == "ac.sabotage"


def test_card_lookup_by_alias(kb):
    assert kb.card_lookup("grav drive")["match"]["id"] == "tech.gravity_drive"


def test_card_lookup_no_match(kb):
    assert kb.card_lookup("zzzzzz qqqq")["match"] is None


def test_rules_lookup_ranks_relevant_rule(kb):
    ids = [r["id"] for r in kb.rules_lookup("can my ships move out of a system with my command token")["results"]]
    assert "lrr.sample.movement" in ids[:3]


def test_rules_lookup_pulls_parent(kb):
    ids = [r["id"] for r in kb.rules_lookup("sustain damage damaged unit hit", k=2)["results"]]
    assert "lrr.sample.sustain_damage" in ids


def test_ruleset_filter_excludes_other_releases():
    kb = KnowledgeBase()
    kb.add({"kind": "rule", "id": "lrr.te.1", "ruleset": "thunders_edge", "title": "Breakthrough", "text": "breakthrough rules"})
    kb.add({"kind": "rule", "id": "lrr.b.1", "ruleset": "base", "title": "Breakthrough-ish", "text": "breakthrough rules"})
    kb.finalize()
    ids = [r["id"] for r in kb.rules_lookup("breakthrough", ruleset=Ruleset())["results"]]
    assert ids == ["lrr.b.1"]


def test_superseded_ruling_dropped():
    kb = KnowledgeBase()
    kb.add({"kind": "ruling", "id": "r.old", "question": "warp fighters", "answer": "yes warp", "authority": "developer"})
    kb.add({"kind": "ruling", "id": "r.new", "question": "warp fighters", "answer": "no warp", "authority": "official_faq", "supersedes": ["r.old"]})
    kb.finalize()
    out = kb.rules_lookup("warp fighters")
    assert [r["id"] for r in out["results"]] == ["r.new"]
    assert out["superseded_dropped"] == ["r.old"]


def test_duplicate_ids_rejected():
    kb = KnowledgeBase()
    kb.add({"kind": "rule", "id": "lrr.1", "ruleset": "base", "title": "A", "text": "a"})
    with pytest.raises(DuplicateRecord):
        kb.add({"kind": "rule", "id": "lrr.1", "ruleset": "base", "title": "A", "text": "a"})


def test_authority_order():
    assert Authority.card_text > Authority.lrr > Authority.official_faq > Authority.developer > Authority.community
