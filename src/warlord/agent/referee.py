"""Referee agent: a manual tool-use loop over the Claude Messages API.

The model drafts the ruling; code enforces the contract afterwards:

* citations that never appeared in a tool result are removed and reported;
* an answer with no valid citation is unverified;
* an answer is verified only if validate_action ran with engine coverage and
  no step of the session fell back to text-only coverage;
* the answer paraphrases instead of reproducing source text: a verbatim quote
  longer than MAX_QUOTE_WORDS gets one rewrite request, and is cut out if it
  is still there.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from warlord.agent.prompts import REFEREE_OUTPUT_SCHEMA, REFEREE_SYSTEM
from warlord.agent.quotes import MAX_QUOTE_WORDS, long_quotes, trim_quotes
from warlord.tools.registry import REFEREE_TOOLS, ToolContext, run_tool, tool_definitions
from warlord.tools.ruling_check import partition_citations, source_texts, verification_gaps

DEFAULT_MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"

REWRITE_REQUEST = (
    "Your answer reproduces source text word for word at length: {spans}. "
    "This tool is shared, so rules and card text must be paraphrased, not quoted. "
    "Rewrite the answer in your own words, quoting at most a short phrase "
    "(under {limit} words), and keep the same citations."
)


@dataclass
class Citation:
    id: str
    supports: str


@dataclass
class RefereeAnswer:
    request_type: str
    ruling: str
    answer: str
    citations: list[Citation]
    verified: bool
    unverified_reason: str
    conflicts: list[str] = field(default_factory=list)
    dropped_citations: list[str] = field(default_factory=list)
    trimmed_quotes: list[str] = field(default_factory=list)
    untrimmed_quotes: list[str] = field(default_factory=list)  # found but could not be located to cut
    tool_calls: list[dict] = field(default_factory=list)
    coverage: list[dict] = field(default_factory=list)
    stop_reason: str | None = None
    model: str | None = None

    def to_dict(self) -> dict:
        return {
            "request_type": self.request_type,
            "ruling": self.ruling,
            "answer": self.answer,
            "citations": [c.__dict__ for c in self.citations],
            "verified": self.verified,
            "unverified_reason": self.unverified_reason,
            "conflicts": self.conflicts,
            "dropped_citations": self.dropped_citations,
            "trimmed_quotes": self.trimmed_quotes,
            "untrimmed_quotes": self.untrimmed_quotes,
            "tool_calls": self.tool_calls,
            "coverage": self.coverage,
            "stop_reason": self.stop_reason,
            "model": self.model,
        }

    def render(self) -> str:
        badge = "VERIFIED by engine" if self.verified else "UNVERIFIED (text only)"
        lines = [f"[{badge}] ruling: {self.ruling}", "", self.answer.strip(), ""]
        if self.citations:
            lines.append("Sources:")
            lines += [f"  - {c.id}: {c.supports}" for c in self.citations]
        if not self.verified and self.unverified_reason:
            lines += ["", f"Not verified: {self.unverified_reason}"]
        if self.conflicts:
            lines += ["", "Conflicts noted:"] + [f"  - {c}" for c in self.conflicts]
        if self.dropped_citations:
            lines += ["", f"(Removed citations not backed by a lookup: {', '.join(self.dropped_citations)})"]
        if self.trimmed_quotes:
            lines += ["", f"(Removed {len(self.trimmed_quotes)} long verbatim quote(s) of source text.)"]
        if self.untrimmed_quotes:
            lines += ["", f"(Warning: {len(self.untrimmed_quotes)} long quote(s) of source text could not be removed.)"]
        return "\n".join(lines)


class RefereeError(RuntimeError):
    pass


class Referee:
    def __init__(
        self,
        ctx: ToolContext,
        client: Any = None,
        *,
        model: str = DEFAULT_MODEL,
        effort: str = "high",
        max_tokens: int = 16000,
        max_turns: int = 12,
        fallbacks: bool = True,
        quote_rewrites: int = 1,
    ):
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        self.client = client
        self.ctx = ctx
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self.max_turns = max_turns
        self.fallbacks = fallbacks
        self.quote_rewrites = quote_rewrites
        self.tools = tool_definitions(REFEREE_TOOLS)

    def _request(self, messages: list) -> Any:
        kwargs: dict[str, Any] = dict(
            model=self.model,
            max_tokens=self.max_tokens,
            system=REFEREE_SYSTEM,
            tools=self.tools,
            messages=messages,
            thinking={"type": "adaptive"},
            output_config={
                "effort": self.effort,
                "format": {"type": "json_schema", "schema": REFEREE_OUTPUT_SCHEMA},
            },
            cache_control={"type": "ephemeral"},
        )
        if self.fallbacks:
            kwargs.update(betas=[FALLBACK_BETA], fallbacks="default")
        return self.client.beta.messages.create(**kwargs)

    def ask(self, question: str) -> RefereeAnswer:
        preface = ""
        if self.ctx.game_id:
            preface = f"Game: {self.ctx.game_id}. Asking player: {self.ctx.viewer}.\n\n"
        messages: list = [{"role": "user", "content": preface + question}]

        rewrites = 0
        while True:
            response = self._run(messages)
            if response.stop_reason == "refusal":
                return self._refused(response)
            draft = self._parse(response)
            spans = self._long_quotes(draft)
            if spans and rewrites < self.quote_rewrites:
                rewrites += 1
                messages.append({"role": "assistant", "content": response.content})
                shown = "; ".join(f'"{s[:80]}…"' for s in spans[:3])
                messages.append(
                    {"role": "user", "content": REWRITE_REQUEST.format(spans=shown, limit=MAX_QUOTE_WORDS)}
                )
                continue
            return self.enforce(
                draft, stop_reason=response.stop_reason, model=getattr(response, "model", None)
            )

    def _run(self, messages: list) -> Any:
        """Run the tool loop until the model gives a final (or refused) response."""
        for _ in range(self.max_turns):
            response = self._request(messages)
            stop = response.stop_reason
            if stop == "refusal":
                return response
            if stop == "max_tokens":
                raise RefereeError("The response hit max_tokens before finishing.")
            if stop == "pause_turn":
                messages.append({"role": "assistant", "content": response.content})
                continue
            if stop != "tool_use":
                return response
            messages.append({"role": "assistant", "content": response.content})
            results = []
            for block in response.content:
                if getattr(block, "type", None) != "tool_use":
                    continue
                content, is_error = run_tool(self.ctx, block.name, dict(block.input or {}))
                result = {"type": "tool_result", "tool_use_id": block.id, "content": content}
                if is_error:
                    result["is_error"] = True
                results.append(result)
            messages.append({"role": "user", "content": results})
        raise RefereeError(f"No final answer after {self.max_turns} model turns.")

    @staticmethod
    def _parse(response: Any) -> dict:
        text = "".join(b.text for b in response.content if getattr(b, "type", None) == "text")
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise RefereeError(f"Final answer was not valid JSON: {text[:200]!r}") from e

    def _long_quotes(self, draft: dict) -> list[str]:
        sources = source_texts(self.ctx)
        found = long_quotes(draft.get("answer", ""), sources)
        for c in draft.get("citations", []):
            found += long_quotes(c.get("supports", ""), sources)
        return found

    def enforce(self, draft: dict, *, stop_reason: str | None = None, model: str | None = None) -> RefereeAnswer:
        """Apply the citation and verification rules to the model's draft."""
        ctx = self.ctx
        spans = self._long_quotes(draft)
        removed: list[str] = []
        if spans:
            answer, removed = trim_quotes(draft.get("answer", ""), spans)
            citations = []
            for c in draft.get("citations", []):
                supports, more = trim_quotes(c.get("supports", ""), spans)
                removed += more
                citations.append({**c, "supports": supports})
            draft = {**draft, "answer": answer, "citations": citations}
        backed, _, _ = partition_citations(ctx, [c.get("id", "") for c in draft.get("citations", [])])
        kept = [c for c in draft.get("citations", []) if c.get("id", "") in backed]
        dropped = [c for c in draft.get("citations", []) if c.get("id", "") not in backed]

        reasons = verification_gaps(ctx, backed)
        model_verified = bool(draft.get("verified"))
        verified = model_verified and not reasons

        reason = (draft.get("unverified_reason") or "").strip()
        if not verified:
            if model_verified and reasons:
                reason = "; ".join(reasons)
            elif not reason:
                reason = "; ".join(reasons) or "the model marked this answer unverified"

        return RefereeAnswer(
            request_type=draft.get("request_type", "other"),
            ruling=draft.get("ruling", "unknown"),
            answer=draft.get("answer", ""),
            citations=[Citation(c["id"], c.get("supports", "")) for c in kept],
            verified=verified,
            unverified_reason="" if verified else reason,
            conflicts=list(draft.get("conflicts", [])),
            dropped_citations=[c.get("id", "") for c in dropped],
            trimmed_quotes=sorted(set(removed)),
            untrimmed_quotes=sorted(set(spans) - set(removed)),
            tool_calls=list(ctx.calls),
            coverage=list(ctx.coverage),
            stop_reason=stop_reason,
            model=model,
        )

    def _refused(self, response: Any) -> RefereeAnswer:
        details = getattr(response, "stop_details", None)
        explanation = getattr(details, "explanation", None) if details else None
        return RefereeAnswer(
            request_type="other",
            ruling="unknown",
            answer="The model declined to answer this request." + (f" ({explanation})" if explanation else ""),
            citations=[],
            verified=False,
            unverified_reason="refused",
            tool_calls=list(self.ctx.calls),
            stop_reason="refusal",
            model=getattr(response, "model", None),
        )
