"""Evaluation checks from the design doc, as plain functions over agent outputs.

Each check returns a :class:`CheckResult` with a pass rate and the failing
cases, so a suite can be rerun on every change to the LRR, card data or prompts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from warlord.kb.store import KnowledgeBase
from warlord.state.perspective import hidden_from
from warlord.state.schema import GameState

PASS_BARS = {
    "rules_qa_accuracy": 0.90,
    "legality_agreement": 1.00,  # where coverage is "engine"
    "text_only_honesty": 1.00,
    "hidden_information_leaks": 1.00,  # share with zero leaks
}


@dataclass
class CheckResult:
    name: str
    total: int
    passed: int
    failures: list[dict] = field(default_factory=list)

    @property
    def rate(self) -> float:
        return self.passed / self.total if self.total else 1.0

    @property
    def meets_bar(self) -> bool:
        return self.rate >= PASS_BARS.get(self.name, 0.0)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "total": self.total,
            "passed": self.passed,
            "rate": round(self.rate, 4),
            "bar": PASS_BARS.get(self.name),
            "meets_bar": self.meets_bar,
            "failures": self.failures[:20],
        }


def citation_validity(answers: Iterable[dict], kb: KnowledgeBase) -> CheckResult:
    """Every answer cites at least one record, and every cited id exists in the KB."""
    total = passed = 0
    failures = []
    for a in answers:
        total += 1
        ids = [c["id"] for c in a.get("citations", [])]
        missing = [i for i in ids if i not in kb]
        if ids and not missing:
            passed += 1
        else:
            failures.append({"answer": a.get("answer", "")[:120], "missing": missing, "cited": ids})
    return CheckResult("citation_validity", total, passed, failures)


def rules_qa_accuracy(pairs: Iterable[tuple[dict, dict]], grade) -> CheckResult:
    """``pairs`` are (gold record, agent answer). ``grade(gold, answer) -> bool`` judges
    correctness (an expert or an LLM judge); the cited ids must also overlap gold refs."""
    total = passed = 0
    failures = []
    for gold, ans in pairs:
        total += 1
        cited = {c["id"] for c in ans.get("citations", [])}
        cites_gold = bool(cited & set(gold.get("refs", [])))
        ok = bool(grade(gold, ans)) and cites_gold
        passed += ok
        if not ok:
            failures.append({"id": gold.get("id"), "cites_gold": cites_gold})
    return CheckResult("rules_qa_accuracy", total, passed, failures)


def legality_agreement(cases: Iterable[tuple[bool | None, dict]]) -> CheckResult:
    """``cases`` are (agent's legal call, engine validate_action result). Only cases
    where the engine had full coverage count."""
    total = passed = 0
    failures = []
    for agent_call, engine in cases:
        if engine.get("coverage") != "engine" or engine.get("legal") is None:
            continue
        total += 1
        if agent_call == engine["legal"]:
            passed += 1
        else:
            failures.append({"agent": agent_call, "engine": engine.get("legal"), "violations": engine.get("violations")})
    return CheckResult("legality_agreement", total, passed, failures)


def text_only_honesty(answers: Iterable[dict]) -> CheckResult:
    """Every answer not fully backed by the engine must be flagged unverified.

    An answer is engine-backed when its session ran validate_action with
    coverage "engine" and no step reported text-only coverage.
    """
    total = passed = 0
    failures = []
    for a in answers:
        cov = a.get("coverage", [])
        engine_backed = any(
            c.get("tool") == "validate_action" and c.get("coverage") == "engine" for c in cov
        ) and not any(c.get("coverage") == "text_only" for c in cov)
        if engine_backed:
            continue
        total += 1
        if not a.get("verified"):
            passed += 1
        else:
            failures.append({"answer": a.get("answer", "")[:120], "coverage": cov})
    return CheckResult("text_only_honesty", total, passed, failures)


def find_leaks(text: str, state: GameState, viewer: str, name_of: dict[str, Sequence[str]] | None = None) -> list[str]:
    """Hidden ids (or their card names) that appear in ``text``.

    ``name_of`` maps ids to display names and aliases (e.g. from the KB) so a
    leak that names "Sabotage" instead of "ac.sabotage" is caught.
    """
    hidden = hidden_from(state, viewer)
    lower = text.lower()
    leaks = []
    for hid in sorted(hidden):
        needles = [hid, *((name_of or {}).get(hid, ()))]
        for n in needles:
            if len(n) < 4:
                continue
            if re.search(rf"(?<![a-z0-9_.]){re.escape(n.lower())}(?![a-z0-9_])", lower):
                leaks.append(hid)
                break
    return leaks


def names_from_kb(kb: KnowledgeBase) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for rid, rec in kb.records.items():
        name = getattr(rec, "name", None)
        if name:
            out[rid] = [name, *getattr(rec, "aliases", [])]
    return out


def hidden_information_leaks(
    cases: Iterable[tuple[GameState, str, str]], name_of: dict[str, Sequence[str]] | None = None
) -> CheckResult:
    """``cases`` are (omniscient state, viewer, advice text). Pass = zero leaks."""
    total = passed = 0
    failures = []
    for state, viewer, text in cases:
        total += 1
        leaks = find_leaks(text, state, viewer, name_of)
        if not leaks:
            passed += 1
        else:
            failures.append({"game": state.meta.game_id, "viewer": viewer, "leaked": leaks})
    return CheckResult("hidden_information_leaks", total, passed, failures)
