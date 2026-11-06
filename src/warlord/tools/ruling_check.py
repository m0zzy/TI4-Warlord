"""Checks on a drafted ruling, shared by both front ends.

* API-key mode: ``Referee.enforce`` applies them after the model drafts, so
  they are guaranteed.
* MCP mode: the ``check_ruling`` tool returns them to the player's agent,
  which is asked to fix the answer before showing it. They are advice there.

They are judged against the session record in the ToolContext: the ids seen
in tool results, and the engine coverage of each step.

Deliberately absent: a hidden-information check. Telling an agent "your
answer names a card this player cannot see" would confirm a correct guess
about an opponent's hand, and anyone could probe hands that way. Hidden
information is enforced at the source instead: the agent is never given it.
"""

from __future__ import annotations

from typing import Iterable

from warlord.agent.quotes import MAX_QUOTE_WORDS, long_quotes
from warlord.tools.registry import ToolContext


def source_texts(ctx: ToolContext) -> list[str]:
    """Text of every record the session has seen, for the quote check."""
    texts = []
    for rid in sorted(ctx.seen_ids):
        rec = ctx.kb.get(rid)
        for attr in ("text", "answer"):
            value = getattr(rec, attr, None)
            if isinstance(value, str):
                texts.append(value)
    return texts


def partition_citations(ctx: ToolContext, ids: Iterable[str]) -> tuple[list[str], list[str], list[str]]:
    """(backed by a lookup this session, exists but not looked up, unknown id)."""
    backed, not_looked_up, unknown = [], [], []
    for cid in ids:
        if cid in ctx.seen_ids:
            backed.append(cid)
        elif cid in ctx.kb:
            not_looked_up.append(cid)
        else:
            unknown.append(cid)
    return backed, not_looked_up, unknown


def verification_gaps(ctx: ToolContext, backed: list[str]) -> list[str]:
    """Reasons an answer cannot be labelled engine-verified (empty if it can)."""
    reasons = []
    engine_checked = any(e["tool"] == "validate_action" and e["coverage"] == "engine" for e in ctx.coverage)
    text_only = sorted({e["tool"] for e in ctx.coverage if e["coverage"] == "text_only"})
    if not backed:
        reasons.append("no citation backed by a rules or card lookup")
    if not engine_checked:
        reasons.append("the engine did not check this; the answer comes from the rules text")
    if text_only:
        reasons.append(f"text-only coverage in: {', '.join(text_only)}")
    return reasons


def check_ruling(
    ctx: ToolContext,
    answer: str,
    citations: Iterable[str],
    claims_verified: bool,
    final: bool = True,
) -> dict:
    """Judge a draft. With ``final`` and no problems, the session record resets
    so the next question starts clean."""
    ids = [c for c in citations if c]
    backed, not_looked_up, unknown = partition_citations(ctx, ids)
    gaps = verification_gaps(ctx, backed)
    supported = not gaps
    quotes = long_quotes(answer, source_texts(ctx))

    problems = []
    if not_looked_up:
        problems.append(
            f"Cited without looking them up in this session: {', '.join(not_looked_up)}. "
            "Look them up, or drop them."
        )
    if unknown:
        problems.append(f"Not a known rule or card id: {', '.join(unknown)}. Remove these citations.")
    if not ids:
        problems.append("No citations. Cite the rule and card ids that support the ruling.")
    if claims_verified and not supported:
        problems.append("The answer claims to be verified, but " + "; ".join(gaps) + ". Label it unverified.")
    if quotes:
        problems.append(
            f"{len(quotes)} passage(s) copy more than {MAX_QUOTE_WORDS} words of rules or card text. "
            "Paraphrase them in your own words."
        )

    label = "Engine-verified." if supported else "Unverified: " + "; ".join(gaps) + "."
    result = {
        "ok": not problems,
        "problems": problems,
        "label": label,
        "citations": {"backed": backed, "not_looked_up": not_looked_up, "unknown": unknown},
        "long_quotes": [q[:80] + ("…" if len(q) > 80 else "") for q in quotes],
    }
    if final and not problems:
        ctx.seen_ids.clear()
        ctx.coverage.clear()
        ctx.calls.clear()
        result["session_record"] = "reset for the next question"
    return result
