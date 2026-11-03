"""Referee agent: a manual tool-use loop over the Claude Messages API.

The model drafts the ruling; code enforces the contract afterwards:

* citations that never appeared in a tool result are removed and reported;
* an answer with no valid citation is unverified;
* an answer is verified only if validate_action ran with engine coverage and
  no step of the session fell back to text-only coverage.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from warlord.agent.prompts import REFEREE_OUTPUT_SCHEMA, REFEREE_SYSTEM
from warlord.tools.registry import REFEREE_TOOLS, ToolContext, run_tool, tool_definitions

DEFAULT_MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"


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
            preface = (
                f"Game: {self.ctx.game_id}. Asking player: {self.ctx.viewer}.\n\n"
            )
        messages: list = [{"role": "user", "content": preface + question}]

        response = None
        for _ in range(self.max_turns):
            response = self._request(messages)
            stop = response.stop_reason
            if stop == "refusal":
                return self._refused(response)
            if stop == "max_tokens":
                raise RefereeError("The response hit max_tokens before finishing.")
            if stop == "pause_turn":
                messages.append({"role": "assistant", "content": response.content})
                continue
            if stop != "tool_use":
                break
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
        else:
            raise RefereeError(f"No final answer after {self.max_turns} model turns.")

        assert response is not None
        text = "".join(b.text for b in response.content if getattr(b, "type", None) == "text")
        try:
            draft = json.loads(text)
        except json.JSONDecodeError as e:
            raise RefereeError(f"Final answer was not valid JSON: {text[:200]!r}") from e
        return self.enforce(draft, stop_reason=response.stop_reason, model=getattr(response, "model", None))

    def enforce(self, draft: dict, *, stop_reason: str | None = None, model: str | None = None) -> RefereeAnswer:
        """Apply the citation and verification rules to the model's draft."""
        ctx = self.ctx
        kept, dropped = [], []
        for c in draft.get("citations", []):
            cid = c.get("id", "")
            (kept if cid in ctx.seen_ids else dropped).append(c)

        reasons = []
        model_verified = bool(draft.get("verified"))
        engine_checked = any(
            e["tool"] == "validate_action" and e["coverage"] == "engine" for e in ctx.coverage
        )
        text_only_steps = [e["tool"] for e in ctx.coverage if e["coverage"] == "text_only"]
        if not kept:
            reasons.append("no citation backed by a rules or card lookup")
        if not engine_checked:
            reasons.append("the engine did not check this; the answer comes from the rules text")
        if text_only_steps:
            reasons.append(f"text-only coverage in: {', '.join(sorted(set(text_only_steps)))}")
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
