# Manual eval kit (M5)

This kit tests the MCP referee through your own Claude client, with no API key. It holds 50 questions:
- 42 are rules questions (38 LRR, 4 Thunder's Edge). Correct answers to these come from the rules text, so they should be labelled **unverified**.
- 8 are legality questions on the sample game `pbd00000`, seat `p3`. Seven should come back **engine-verified**. One (`a.049`, landing on Quann) depends on a space combat, so it should be unverified.

The questions live in `src/warlord/data/evals/rules_qa.jsonl`. Each gold answer is written in our own words, with the rule ids that support it. All 50 are `"review": "draft"` until you approve them.

## 1. Review the questions

```bash
warlord eval questions --answers > review.md
```

Read `review.md`. For each question, edit `rules_qa.jsonl`: set `"review"` to `"approved"` or `"rejected"`, and fix any gold answer you disagree with. I checked every rule id against the imported LRR 2.0 and TE rulebook, and checked every legality answer against the engine. The wording and the rulings are still yours to confirm.

## 2. Ask your agent

Install the sample game into your data folder, so the legality questions can use it:

```bash
warlord eval install
```

Then print the questions:

```bash
warlord eval questions
```

Ask each one in a fresh conversation in Claude Code or Claude Desktop, with the warlord MCP server connected. The referee prompt tells the agent to cite rule ids, run `check_ruling`, and end with the label.

## 3. Record and score

```bash
warlord eval template answers.jsonl
```

Paste each final answer into `"answer"`. Then set `"correct"` to `true` or `false` after comparing it with the gold answer. Leave it `null` if you skipped the question.

```bash
warlord eval score answers.jsonl
```

The score checks these against the design doc's pass bars:

| Check | Meaning | Bar |
| --- | --- | --- |
| accuracy | You marked it correct, and it cites a gold rule (or its topic or sub-rule) | 90% |
| citation_validity | It cites at least one id, and every cited id exists in your knowledge base | 100% |
| label_honesty | Rules-text answers say "Unverified"; engine-checked moves say "Engine-verified" | 100% |
| no_long_quotes | No passage copies more than 20 words of rules or card text | 100% |

Add `--json` for a machine-readable report. The "Needs a look" list names each failing question and what went wrong.
