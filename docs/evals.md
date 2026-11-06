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

## The easy way: let Claude Code run it

If Claude Code is installed and logged in, Warlord can drive it. No API key or copy-pasting is needed:

```bash
warlord eval run answers.jsonl --limit 10
```

- **What it does:** sends each question to `claude -p` (Claude Code's headless mode) in a fresh conversation, with only the warlord MCP server connected and the referee instructions added. It saves the final answer, the tools the agent called, the turn count and the cost.
- **Resuming:** already-answered questions are skipped, so you can stop and resume. `--rerun` asks again.
- **Choosing questions:** `--ids a.001,a.043` runs specific questions. Leave out `--limit` for all 50; each takes 20 to 40 seconds.
- **Usage:** it runs on your Claude Code login and counts toward your usage like any other conversation.

Then grade, by hand or with Claude as the judge:

```bash
warlord eval grade answers.jsonl
```

This shows each answer next to the gold answer and asks y (correct), n (incorrect), s (skip) or q (quit).

```bash
warlord eval grade answers.jsonl --judge
```

This has Claude compare each answer with the gold answer. Your own y/n marks always win over the judge's, so you can spot-check the judge afterwards with plain `warlord eval grade`.

```bash
warlord eval score answers.jsonl
```

The score says how many answers were graded by the judge alone.

## The manual way

### 2. Ask your agent

Install the sample game into your data folder, so the legality questions can use it:

```bash
warlord eval install
```

Then print the questions:

```bash
warlord eval questions
```

Ask each one in a fresh conversation in Claude Code or Claude Desktop, with the warlord MCP server connected. The referee prompt tells the agent to cite rule ids, run `check_ruling`, and end with the label.

### 3. Record and score

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

Add `--json` for a machine-readable report.

## Legality cases (set B)

`warlord datasets legality --games data/private/games --kb ~/.local/share/warlord/kb --out data/private/datasets` generates legality cases from your stored games. For example, the 50 completed games from `warlord fetch-asyncti4 completed`.

For every seat, it samples candidate actions on the board as stored, and again as at the start of that seat's action phase. It keeps only cases the engine decides fully, balanced between legal and illegal. Each case's perspective view is saved under `states/`, so the case replays to the same verdict. The games hold FFG card ids and players' hidden cards, so keep the output under `data/private/`. The "Needs a look" list names each failing question and what went wrong.
