"""Manual eval kit for the MCP referee (M5).

The rules questions (set A) ship in ``warlord/data/evals/rules_qa.jsonl``.
Each holds a question, a short gold answer in our own words, the rule ids
that support it, and the label a correct answer carries ("unverified" for
rules-text answers, "engine" for engine-checked moves). Eight are legality
questions on the sample game ``pbd00000``, which ``install_game`` copies
into the data folder.

The loop, with no API key:

1. ``warlord eval questions`` prints the questions (``--answers`` adds the
   gold answers, for review). Set ``review`` to "approved" or "rejected" in
   the JSONL as you go.
2. ``warlord eval template answers.jsonl`` writes one line per question.
   Paste each answer your agent gave into ``answer``, and set ``correct``
   to true or false after reading it.
3. ``warlord eval score answers.jsonl`` checks citations, labels and quotes
   automatically, combines them with your correctness marks, and compares
   the results with the design doc's pass bars.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from warlord.agent.quotes import long_quotes
from warlord.kb.store import KnowledgeBase
from warlord.paths import PACKAGE_DATA, games_dir
from warlord.tools.ruling_check import base_id

QUESTIONS = PACKAGE_DATA / "evals" / "rules_qa.jsonl"
EVAL_GAME = PACKAGE_DATA / "evals" / "pbd00000.json"

PASS_BARS = {
    "accuracy": 0.90,  # correct and cites a gold rule
    "citation_validity": 1.0,  # every cited id exists
    "label_honesty": 1.0,  # rules-text answers say unverified; engine answers say engine-verified
    "no_long_quotes": 1.0,
}

_ID = re.compile(
    r"\b((?:lrr|te|ac|tech|unit|agenda|pn|obj|so|relic|leader|ability|breakthrough|sc|faction|explore|planet|tile|"
    r"ruling)(?:\.[a-z0-9_]+)+)"
)


def load_questions(path: Path = QUESTIONS) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def install_game(data: Path) -> Path:
    dest = games_dir(data) / EVAL_GAME.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(EVAL_GAME, dest)
    return dest


def questions_markdown(questions: list[dict], with_answers: bool = False) -> str:
    lines = ["# TI4 Warlord rules eval", ""]
    for q in questions:
        tag = f" _(game {q['game']}, seat {q['seat']})_" if q.get("game") else ""
        lines.append(f"**{q['id']}** [{q['topic']}]{tag} {q['question']}")
        if with_answers:
            lines.append(f"  - Gold: {q['answer']}")
            lines.append(f"  - Rules: {', '.join(q['refs'])} · expected label: {q['expected_label']} · review: {q['review']}")
        lines.append("")
    return "\n".join(lines)


def write_template(questions: list[dict], path: Path) -> Path:
    with path.open("w", encoding="utf-8") as fh:
        for q in questions:
            fh.write(json.dumps({"id": q["id"], "question": q["question"], "answer": "", "correct": None}) + "\n")
    return path


def cited_ids(text: str) -> list[str]:
    seen: list[str] = []
    for m in _ID.finditer(text):
        cid = m.group(1).rstrip(".")
        if cid not in seen:
            seen.append(cid)
    return seen


def _related(a: str, b: str) -> bool:
    """Same rule, or one is the other's topic/sub-rule (lrr.89 ~ lrr.89.2)."""
    return a == b or a.startswith(b + ".") or b.startswith(a + ".")


def detect_label(text: str) -> str | None:
    lower = text.lower()
    if "unverified" in lower or "not verified" in lower:
        return "unverified"
    if "engine-verified" in lower or "engine verified" in lower:
        return "engine"
    return None


@dataclass
class Scored:
    id: str
    answered: bool
    correct: bool | None
    cited: list[str] = field(default_factory=list)
    invalid: list[str] = field(default_factory=list)
    cites_gold: bool = False
    label: str | None = None
    label_ok: bool = False
    long_quotes: int = 0

    @property
    def accurate(self) -> bool | None:
        return None if self.correct is None else (self.correct and self.cites_gold)


def score(answers: list[dict], questions: list[dict], kb: KnowledgeBase | None) -> dict:
    by_id = {q["id"]: q for q in questions}
    sources = [getattr(r, "text", "") for r in kb.records.values()] if kb else []
    rows: list[Scored] = []
    for a in answers:
        q = by_id.get(a.get("id"))
        text = (a.get("answer") or "").strip()
        if q is None:
            continue
        # Your own mark wins; otherwise the Claude judge's, if it ran.
        mark = a.get("correct") if a.get("correct") is not None else a.get("judge")
        s = Scored(id=q["id"], answered=bool(text), correct=mark)
        if text:
            s.cited = cited_ids(text)
            s.invalid = [c for c in s.cited if kb is not None and base_id(c) not in kb]
            s.cites_gold = any(_related(base_id(c), g) for c in s.cited for g in q["refs"])
            s.label = detect_label(text)
            s.label_ok = s.label == q["expected_label"]
            s.long_quotes = len(long_quotes(text, sources)) if sources else 0
        rows.append(s)

    answered = [r for r in rows if r.answered]
    graded = [r for r in answered if r.correct is not None]
    by_judge = sum(1 for a in answers if a.get("correct") is None and a.get("judge") is not None and (a.get("answer") or "").strip())

    def rate(xs, ok) -> float | None:
        return round(sum(1 for x in xs if ok(x)) / len(xs), 3) if xs else None

    metrics = {
        "accuracy": rate(graded, lambda r: r.accurate),
        "citation_validity": rate(answered, lambda r: r.cited and not r.invalid),
        "label_honesty": rate(answered, lambda r: r.label_ok),
        "no_long_quotes": rate(answered, lambda r: r.long_quotes == 0),
    }
    return {
        "answered": len(answered),
        "graded": len(graded),
        "graded_by_judge": by_judge,
        "questions": len(questions),
        "metrics": {
            k: {"value": v, "bar": PASS_BARS[k], "meets_bar": None if v is None else v >= PASS_BARS[k]}
            for k, v in metrics.items()
        },
        "failures": [
            {
                "id": r.id,
                **({"incorrect": True} if r.correct is False else {}),
                **({"no_gold_citation": True} if not r.cites_gold else {}),
                **({"invalid_ids": r.invalid} if r.invalid else {}),
                **({"label": r.label, "expected": by_id[r.id]["expected_label"]} if not r.label_ok else {}),
                **({"long_quotes": r.long_quotes} if r.long_quotes else {}),
            }
            for r in answered
            if r.correct is False or not r.cites_gold or r.invalid or not r.label_ok or r.long_quotes
        ],
    }


def score_markdown(report: dict) -> str:
    judge = f" ({report['graded_by_judge']} by the Claude judge only)" if report.get("graded_by_judge") else ""
    lines = [f"Answered {report['answered']} of {report['questions']}; graded {report['graded']}{judge}.", ""]
    lines.append("| Check | Result | Bar | Pass |")
    lines.append("| --- | --- | --- | --- |")
    for name, m in report["metrics"].items():
        value = "—" if m["value"] is None else f"{m['value']:.0%}"
        ok = "—" if m["meets_bar"] is None else ("yes" if m["meets_bar"] else "no")
        lines.append(f"| {name} | {value} | {m['bar']:.0%} | {ok} |")
    if report["failures"]:
        lines += ["", "Needs a look:"]
        lines += [f"- {f['id']}: " + ", ".join(f"{k}={v}" for k, v in f.items() if k != "id") for f in report["failures"]]
    return "\n".join(lines)
