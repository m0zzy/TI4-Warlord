# Delivery plan: MCP server first, API-key mode later

Nov 3, 2026

## Decision

TI4 Warlord ships first as an **MCP server**. The player's own agent (Claude Desktop, Claude Code or any MCP client) does the reasoning. Our server supplies the parts that must be right: the per-seat game view, rules and card lookup, the legality engine, combat odds and a ruling check. The project pays for no model calls.

An **API-key mode** may come later. In it we run the model ourselves, for a hosted referee or for automated evals. Both modes share one core, so nothing built now is thrown away.

```
                 ┌──────────── warlord core ────────────┐
                 │ state + perspective view              │
                 │ knowledge base (rules, cards, decks)  │
                 │ engine (validate_action, geometry,    │
                 │         combat_sim)                   │
                 │ tool registry + check_ruling          │
                 └──────────┬───────────────┬────────────┘
                            │               │
              now: MCP server         later: API-key mode
      (player's agent reasons)     (our model reasons: Referee,
                                    hosted bot, automated evals)
```

## What the server enforces, and what it only advises

The player's agent writes the final answer, and our server never sees it. Anything we must guarantee therefore goes into what the server hands out. The rest is guidance.

| Concern | Under MCP | How |
| --- | --- | --- |
| Hidden information | **Enforced** | `get_state` returns only the bound seat's view. Games with full hidden information (the completed-game export) are never served. |
| Legality | **Enforced at the source** | `validate_action`'s verdict comes from the engine, with a one-line status the agent can repeat ("Illegal: lrr.89.2", "Unverified: movement range not checked"). |
| Copyright of rule text | **Enforced at the source** | Locally, each player loads their own copy of the LRR. In a hosted setup, `rules_lookup` returns ids and short excerpts only. |
| Correct citations | Advised | `check_ruling` flags citations that weren't looked up or don't exist. |
| Honest "verified" label | Advised | `check_ruling` returns the label the engine supports. |
| Long quotes in the answer | Advised | `check_ruling` flags them. The source-side cap above keeps the risk small. |

## The MCP server

**Transport.** Local stdio first: the player's client starts `warlord mcp` as a process. Streamable HTTP comes only with the hosted setup.

**Tools.**

| Tool | Notes |
| --- | --- |
| `list_games`, `open_game` | `open_game(game, seat)` binds this session to one seat. Switching seats resets the session record. |
| `fetch_live_game` | Imports an AsyncTI4 game's public view (`web-data`). |
| `set_my_hand` | The player enters their own hidden cards. Opponents' hands stay counts. |
| `get_state` | The bound seat's view only. `hand.known: false` tells the agent to ask. |
| `rules_lookup`, `card_lookup` | As today. `card_lookup` reports `not_in_this_game`. |
| `validate_action` | As today, plus a `status_line` field. |
| `combat_sim` | As today. |
| `check_ruling` | Input: the draft answer, its citations and the claimed verified flag. Output: problems found, the verified label the session supports, and quotes to shorten. Runs `Referee.enforce`'s checks against the session record. |
| `legal_actions`, `apply_action`, `evaluate_position` | Listed only once implemented (advisor). |

**Prompts.** `referee` and, later, `advisor`, carrying today's referee instructions: precedence, citations, verification, paraphrasing and hidden information. Clients that support MCP prompts show them as commands; the tool descriptions repeat the essentials for clients that don't.

**Session record.** One server process serves one client, and it keeps what has been looked up and validated since the last `check_ruling` call (or since the seat changed). `check_ruling` judges the draft against that record, then clears it.

**Data the server reads.** A data folder holds the knowledge base and the player's live games. It defaults to `~/.local/share/warlord`, with `WARLORD_DATA` to override. The completed-game dataset lives elsewhere and is never served.

**Setup for a player.**

```bash
pip install 'ti4-warlord[pdf]'
```

```bash
warlord setup --lrr path/to/ti10_pok_living_rules_reference_20_web.pdf --te-rulebook path/to/te_rulebook_web.pdf --download-asyncti4
```

```bash
claude mcp add warlord -- warlord mcp
```

Claude Desktop users add the same command in its MCP server settings. `warlord setup` runs the existing importers into the data folder and checks the results (101 LRR topics, card counts, deck resolution).

## Evals without an API key

- **Deterministic layers:** the engine, projection, importers, `check_ruling` and the tool contracts are covered by pytest and need no model.
- **Agent behaviour:** run by hand in your own client against a fixed question set. A short script turns saved transcripts into the design doc's checks (citation validity, text-only honesty, leaks).
- **Automated runs** wait for API-key mode.

## API-key mode (later)

Turn it on when one of these is wanted:

1. **Automated evals:** run sets A–C through a reference agent on every change to rules data or prompts. The `Referee` class already does this.
2. **A hosted referee** (Discord bot or web page), where answers must be checked before a player sees them. `Referee.enforce` sits after the model, so citations, the verified label and quotes become guaranteed again.
3. **A hosted MCP server with Discord sign-in:** the seat comes from the login (`web-data` lists each seat's Discord id), and rule text goes out as excerpts.

What it needs:
- `ANTHROPIC_API_KEY`, read from the environment and never stored in the repo.
- Cost limits per user and per day.
- Model and effort settings in config.
- Hosting, for 2 and 3.

The `Referee` code and its tests stay in the repo so this mode is ready to switch on.

## Milestones

| # | Milestone | Done when | Needs from you |
| --- | --- | --- | --- |
| M1 ✅ | `warlord setup` + data folder | One command builds the knowledge base from your files and reports counts | The LRR PDF, TE rulebook and AsyncTI4 checkout on your machine |
| M2 ✅ | MCP server (stdio) with today's tools, `open_game`, `set_my_hand`, `status_line` | Claude Code and Claude Desktop can open a game and get a cited, engine-checked ruling | Try it in your own client |
| M3 | `check_ruling` + `referee` prompt | The checks from `Referee.enforce` run over MCP; tests cover each check | — |
| M4 | Movement range, capacity, fleet supply in the engine | Common tactical actions come back engine-verified instead of unverified | — |
| M5 | Manual eval kit | Question set (start with 50 from set A) + transcript scorer | Review and approve the questions |
| M6 | Advisor tools (`legal_actions`, `apply_action`, `evaluate_position`) + `advisor` prompt | Advice validated by the engine before it is given | Review sample advice |
| Later | API-key mode | See above | API key, hosting choice |

## Risks

- **The agent skips the tools.** Mitigated by the prompt, by tool descriptions and by putting the guarantees on the output side. It cannot be eliminated without API-key mode.
- **Client differences.** MCP prompt support varies by client, so tool descriptions carry the key instructions too.
- **Setup friction.** Players must bring their own rules PDFs. `warlord setup` validates them, and errors name the missing file.
