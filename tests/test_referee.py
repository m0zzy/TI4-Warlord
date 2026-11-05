import json
from types import SimpleNamespace as NS

import pytest

from warlord.agent.referee import FALLBACK_BETA, Referee, RefereeError
from warlord.tools.registry import ToolContext


class FakeClient:
    """Replays scripted responses and records each request."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.beta = NS(messages=NS(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        return self.responses.pop(0)


def tool_use(id_, name, input_):
    return NS(type="tool_use", id=id_, name=name, input=input_)


def final(**draft):
    base = {"request_type": "legality_check", "ruling": "illegal", "answer": "No.", "citations": [],
            "verified": True, "unverified_reason": "", "conflicts": []}
    base.update(draft)
    return NS(stop_reason="end_turn", model="claude-opus-5-5", content=[NS(type="text", text=json.dumps(base))])


def turn(*blocks):
    return NS(stop_reason="tool_use", model="claude-opus-5-5", content=list(blocks))


@pytest.fixture()
def ctx(kb, store):
    return ToolContext(kb=kb, store=store, game_id="pbd00000", viewer="p3")


ILLEGAL_MOVE = {"type": "tactical", "player": "p3", "activate": "201", "moves": [{"from": "101", "units": {"cruiser": 1}}]}


def test_engine_backed_ruling_is_verified(ctx):
    client = FakeClient([
        turn(tool_use("t1", "rules_lookup", {"question": "move out of a system with my command token"}),
             tool_use("t2", "validate_action", {"action": ILLEGAL_MOVE})),
        final(citations=[{"id": "lrr.sample.movement", "supports": "no moving out"}]),
    ])
    ans = Referee(ctx, client).ask("Can my cruiser in 101 join the attack on 201?")
    assert ans.verified and ans.ruling == "illegal"
    assert [c.id for c in ans.citations] == ["lrr.sample.movement"]
    # Both tool results went back in one user message.
    second = client.requests[1]["messages"]
    assert [b["tool_use_id"] for b in second[-1]["content"]] == ["t1", "t2"]


def test_request_shape(ctx):
    client = FakeClient([final(citations=[])])
    Referee(ctx, client).ask("hi")
    req = client.requests[0]
    assert req["model"] == "claude-opus-5-5"
    assert req["thinking"] == {"type": "adaptive"}
    assert req["output_config"]["format"]["type"] == "json_schema"
    assert req["betas"] == [FALLBACK_BETA] and req["fallbacks"] == "default"
    assert req["cache_control"] == {"type": "ephemeral"}
    assert "pbd00000" in req["messages"][0]["content"]
    assert "pbd00000" not in req["system"]  # volatile data stays out of the cached prefix


def test_text_only_answer_forced_unverified(ctx):
    client = FakeClient([
        turn(tool_use("t1", "rules_lookup", {"question": "sustain damage"})),
        final(request_type="rules_question", ruling="explained",
              citations=[{"id": "lrr.sample.sustain_damage", "supports": "x"}], verified=True),
    ])
    ans = Referee(ctx, client).ask("How does sustain damage work?")
    assert not ans.verified
    assert "engine did not check" in ans.unverified_reason


def test_unbacked_citations_dropped(ctx):
    client = FakeClient([
        turn(tool_use("t1", "validate_action", {"action": ILLEGAL_MOVE})),
        final(citations=[{"id": "lrr.89.2", "supports": "made up"}]),
    ])
    ans = Referee(ctx, client).ask("?")
    assert ans.dropped_citations == ["lrr.89.2"] and not ans.citations
    assert not ans.verified


def test_partial_coverage_unverified(ctx):
    # p2 still has ships in 201, so whether the invasion can happen depends on the space combat.
    legal_ish = {"type": "tactical", "player": "p3", "activate": "201",
                 "moves": [{"from": "102", "units": {"carrier": 1, "infantry": 2}}],
                 "invade": [{"planet": "quann", "infantry": 2}]}
    client = FakeClient([
        turn(tool_use("t1", "rules_lookup", {"question": "movement"}),
             tool_use("t2", "validate_action", {"action": legal_ish})),
        final(ruling="legal", citations=[{"id": "lrr.sample.movement", "supports": "x"}], verified=True),
    ])
    ans = Referee(ctx, client).ask("?")
    assert not ans.verified


def test_tool_errors_returned_to_model(ctx):
    client = FakeClient([turn(tool_use("t1", "get_state", {"viewer": "p2"})), final()])
    Referee(ctx, client).ask("what is in p2's hand?")
    result = client.requests[1]["messages"][-1]["content"][0]
    assert result["is_error"] is True


def test_refusal(ctx):
    client = FakeClient([NS(stop_reason="refusal", model="m", content=[], stop_details=NS(explanation="x", category=None))])
    ans = Referee(ctx, client).ask("?")
    assert ans.stop_reason == "refusal" and not ans.verified


def test_turn_limit(ctx):
    client = FakeClient([turn(tool_use(f"t{i}", "rules_lookup", {"question": "x"})) for i in range(3)])
    with pytest.raises(RefereeError):
        Referee(ctx, client, max_turns=3).ask("?")
