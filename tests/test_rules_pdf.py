from warlord.engine.validate import kb_ref_resolver, load_ref_overrides
from warlord.kb.importers.rules_pdf import parse_lrr, parse_rulebook
from warlord.kb.store import KnowledgeBase

# Lines shaped like the LRR's extracted text (paraphrased, not the real rules).
LRR_LINES = [
    "LIVING RULES REFERENCE",
    "Front matter that comes before topic 1 is ignored.",
    "1 ABILITIES",
    "Abilities are things cards let you do.",
    "1.1 Card text wins over this document.",
    "1.13 TIMING",
    "1.14 Before and after abilities happen right before or",
    "after the event.",
    "a For example, after a ship is destroyed.",
    "RELATED TOPICS: Action Phase, Strategic",
    "Action",
    "3 ACTION PHASE",
    "Players take turns in initiative order.",
    "3.4 A player cannot pass until they have used their strategy",
    "card.",
    "2 ACTION CARDS",  # arrives out of order, as two-column extraction can
    "Action cards give one-off abilities.",
    "77. 2 SPACE CANNON OFFENSE",  # kerning gap in the number
    "82 STRATEGIC ACTION",
    "Resolve the primary ability of your strategy card.",
    "17",  # page number
    "INDEX",
    "1.1 index entries are not rules",
]


def test_parse_lrr_topics_and_subrules():
    chunks = {c.id: c for c in parse_lrr(LRR_LINES)}
    assert set(chunks) == {"lrr.1", "lrr.2", "lrr.3", "lrr.82", "lrr.1.1", "lrr.1.13", "lrr.1.14", "lrr.3.4", "lrr.77.2"}
    assert chunks["lrr.1"].related == ["lrr.3", "lrr.82"]
    assert chunks["lrr.1.14"].text == (
        "Before and after abilities happen right before or after the event.\na. For example, after a ship is destroyed."
    )
    assert chunks["lrr.1.14"].parent == "lrr.1"
    assert chunks["lrr.1.13"].title == "Abilities: Timing"
    assert chunks["lrr.3.4"].text.endswith("strategy card.")
    assert chunks["lrr.82"].title == "Strategic Action"
    assert "index entries" not in chunks["lrr.1.1"].text


def test_parse_rulebook_sections():
    lines = [
        "THUNDER’S EDGE",
        "EXPEDITION",
        "Players may venture to a special system to unlock powerful abilities and claim a bigger prize later on.",
        "ENTROPIC SCAR",
        "ANOMALY",
        "Unit abilities cannot be used by or against units inside this anomaly, and wormholes are discarded.",
        "TINY LABEL",
        "short caption",
    ]
    chunks = parse_rulebook(lines)
    assert [c.id for c in chunks] == ["te.thunders_edge_expedition", "te.entropic_scar_anomaly"]
    assert chunks[1].title == "Entropic Scar Anomaly"
    assert "TINY LABEL" in chunks[1].text  # short sections merge into the previous one


def test_pinned_refs_used_only_when_loaded():
    overrides = load_ref_overrides("data/lrr_refs.json")
    assert overrides["movement.origin"] == ["lrr.89.2"]
    kb = KnowledgeBase()
    kb.add({"kind": "rule", "id": "lrr.89.2", "ruleset": "base", "title": "Tactical Action: Step 2—Movement", "text": "move"})
    kb.finalize()
    assert kb_ref_resolver(kb, overrides=overrides)("movement.origin") == ["lrr.89.2"]
    empty = KnowledgeBase()
    empty.add({"kind": "rule", "id": "lrr.sample.movement", "ruleset": "base", "title": "Movement", "text": "ships cannot move out of a system that contains your command token"})
    empty.finalize()
    assert kb_ref_resolver(empty, overrides=overrides)("movement.origin") == ["lrr.sample.movement"]
