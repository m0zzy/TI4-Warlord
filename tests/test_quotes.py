import json
from types import SimpleNamespace as NS

from warlord.agent.quotes import TRIM_MARK, long_quotes, trim_quotes
from warlord.agent.referee import Referee
from warlord.tools.registry import ToolContext

SOURCE = (
    "During movement, the active player may move ships into the active system. Ships cannot move "
    "out of a system that contains the moving player's command token. A ship moves a number of "
    "systems up to its move value."
)
QUOTE = (
    "Ships cannot move out of a system that contains the moving player's command token. "
    "A ship moves a number of systems up to its move value."
)


def test_short_phrases_are_allowed():
    assert long_quotes("Ships cannot move out of a system that contains your token.", [SOURCE]) == []


def test_long_quote_found_case_and_punctuation_insensitive():
    spans = long_quotes(f'The rule says: "{QUOTE.upper()}"', [SOURCE])
    assert len(spans) == 1 and spans[0].startswith("ships cannot move out")


def test_trim_replaces_span():
    text = f"No. {QUOTE} So the cruiser stays."
    out, removed = trim_quotes(text, long_quotes(text, [SOURCE]))
    assert out == f"No. {TRIM_MARK}. So the cruiser stays." and removed


class FakeClient:
    def __init__(self, responses):
        self.responses, self.requests = list(responses), []
        self.beta = NS(messages=NS(create=self._create))

    def _create(self, **kw):
        self.requests.append(kw)
        return self.responses.pop(0)


def final(answer):
    draft = {"request_type": "rules_question", "ruling": "explained", "answer": answer,
             "citations": [{"id": "lrr.sample.movement", "supports": "movement out of activated systems"}],
             "verified": False, "unverified_reason": "text only", "conflicts": []}
    return NS(stop_reason="end_turn", model="m", content=[NS(type="text", text=json.dumps(draft))])


def lookup():
    return NS(stop_reason="tool_use", model="m",
              content=[NS(type="tool_use", id="t1", name="rules_lookup", input={"question": "move out of activated system"})])


def test_referee_asks_for_a_rewrite(kb, store):
    kb_movement_text = kb.get("lrr.sample.movement").text
    quoted = kb_movement_text[kb_movement_text.index("Ships cannot"):]
    client = FakeClient([lookup(), final(f"No. {quoted}"), final("No: ships can't leave a system holding your token.")])
    ans = Referee(ToolContext(kb=kb, store=store, game_id="pbd00000", viewer="p3"), client).ask("?")
    assert ans.answer.startswith("No: ships can't")
    assert "word for word" in client.requests[2]["messages"][-1]["content"]
    assert ans.trimmed_quotes == []


def test_referee_trims_if_rewrite_still_quotes(kb, store):
    text = kb.get("lrr.sample.movement").text
    quoted = text[text.index("Ships cannot"):]
    client = FakeClient([lookup(), final(f"No. {quoted}"), final(f"Still no. {quoted}")])
    ans = Referee(ToolContext(kb=kb, store=store, game_id="pbd00000", viewer="p3"), client).ask("?")
    assert TRIM_MARK in ans.answer and ans.trimmed_quotes
    assert "Ships cannot move out" not in ans.answer
