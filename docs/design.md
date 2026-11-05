# TI4 Rules Referee and Strategy Advisor: Data and Tool Design

Nov 2, 2026 · @N

## Overview

One agent plays two roles: **referee** (is this legal, and what happens) and **advisor** (what should I do, and why). A rules engine decides legality, retrieval over the rules and card text supplies citations, and the model interprets the state, picks among legal options and explains them.

**Scope for v1:** base game + Prophecy of Kings + Thunder's Edge, using the matching Living Rules Reference (LRR) and current errata. Codex content is included wherever a game's decks use it, which AsyncTI4's default PoK and Thunder's Edge decks do. Twilight's Fall and homebrew are out of scope. Every rule, card, state and example carries a `ruleset` tag, and each game names the decks it draws from, so content from different releases never mixes.

**Design principles**

- **The engine decides legality.** The model never contradicts it. Where the engine has no coverage, the agent answers from the text and marks the answer *unverified*.
- **Every rules answer cites its source:** an LRR section, a card, or a ruling.
- **Advice comes from one seat.** The advisor sees only what that player can know.
- **Odds are computed, not estimated.** Combat and objective math go through tools.
- **Precedence is explicit:** card text over the LRR (both apply when both can), the LRR over the Learn to Play booklet, and the LRR FAQ over older developer rulings.

## Architecture

The agent sits between a state service and three tool groups; it never answers legality or odds on its own.

_(Architecture diagram: state service → agent → tools / knowledge base → answer.)_

Solid arrows run on every request; the dashed line is offline, where example sets shape prompts or fine-tuning.

**A request, end to end**

1. Load the game and project it to the asking player's view.
2. Classify the request: a rules question, a legality check, or a decision.
3. **Referee path:** look up the cards and rules involved, run `validate_action` if a move is named, and answer with the ruling and its citations.
4. **Advisor path:** get the legal option families, shortlist a few candidate moves, check them with `combat_sim` and `evaluate_position`, validate the pick, then recommend it with reasoning and the main alternative.
5. If the engine lacks coverage for any step, answer from the text and flag the answer as unverified.

## Game state schema

Store one omniscient state per game, and give the agent only a **perspective view** projected for one player. The fields most often missing in TI4 tools are the open timing window and the per-turn usage flags.

```json
{
  "schema_version": "0.1",
  "ruleset": { "base": true, "pok": true, "thunders_edge": false, "lrr": "pok" },
  "meta": { "game_id": "pbd00000", "source": "asyncti4", "players": 6, "vp_target": 10 },
  "turn": {
    "round": 3,
    "phase": "action",
    "step": "tactical.space_combat",
    "active_player": "p3",
    "speaker": "p1",
    "open_window": { "timing": "after", "event": "combat_round_end", "awaiting": ["p2", "p5"] },
    "resolving": [],
    "used_this_turn": ["p3:tech.gravity_drive"],
    "used_this_round": ["p1:leader.agent"]
  },
  "map": [
    {
      "position": "201",
      "tile": "25",
      "wormholes": ["beta"],
      "anomaly": null,
      "command_tokens": ["p3"],
      "space": { "p3": { "dreadnought": 1, "carrier": 2, "fighter": 3 } },
      "damaged": { "p3": { "dreadnought": 1 } },
      "planets": {
        "quann": {
          "controller": "p3",
          "exhausted": false,
          "attachments": [],
          "units": { "p3": { "infantry": 2, "pds": 1 } }
        }
      }
    }
  ],
  "players": {
    "p3": {
      "faction": "hacan",
      "vp": 4,
      "command_tokens": { "tactic": 2, "fleet": 3, "strategy": 2 },
      "trade_goods": 5,
      "commodities": { "current": 0, "max": 6 },
      "techs": [{ "id": "gravity_drive", "exhausted": false }],
      "unit_upgrades": ["carrier_2"],
      "strategy_cards": [{ "id": "trade", "primary_used": false }],
      "passed": false,
      "leaders": { "agent": "ready", "commander": "locked", "hero": "locked" },
      "relics": [],
      "promissory_in_play": [],
      "scored": ["obj.public_1", "obj.secret_hidden"],
      "hidden": { "action_cards": 3, "promissory_notes": 4, "secret_objectives": 2 }
    }
  },
  "objectives": { "public_revealed": [], "public_unrevealed": 7 },
  "laws": [{ "id": "agenda.example_law", "elected": null }],
  "discards": { "action_cards": [], "agendas": [] },
  "log": []
}
```

The perspective view is the same shape with one addition and one rule:

- **`viewer` + `hand`:** the viewing player's own action cards, promissory notes and unscored secret objectives, by id.
- **Everything else hidden stays a count.** Opponents' `hidden` block shows only numbers, and scored secrets show only once revealed.

Fields that need care:

| Field | Why it matters |
| --- | --- |
| `turn.step` | Legal moves differ by sub-step, such as movement, space cannon, space combat, invasion and production. |
| `turn.open_window` | "When" and "after" abilities and action cards are only playable while a matching window is open. |
| `turn.resolving` | A stack of abilities mid-resolution, so cancels and responses resolve in the right order. |
| `used_this_turn`, `used_this_round` | Many abilities are once per action, turn or round. |
| `command_tokens` on each system | Records activation, which blocks moving out and producing again this round. |
| `damaged` | Sustain Damage state carries across combats and rounds until repaired. |
| `log` | Ordered events with actor and step, for replay, audits and "why is this illegal" answers. |

Tile positions follow the AsyncTI4 ring numbering so imported snapshots need no remapping. The engine derives adjacency from tiles, wormholes, hyperlanes and active effects, rather than storing it.

## Knowledge base

The knowledge base holds four kinds of record: rule chunks, component cards, rulings and strategy notes. Each record has an id the agent can cite.

| Source | Contents | Stored as | Used by |
| --- | --- | --- | --- |
| Living Rules Reference (PoK version) | Numbered rules, glossary, FAQ | One chunk per numbered rule, with its number, title and related rules | `rules_lookup` |
| AsyncTI4 bot component data | Factions, units, techs, action cards, agendas, objectives, promissory notes, leaders, relics, exploration cards, tiles and planets | One JSON record per component, keyed by id | `card_lookup`, the engine |
| Errata | Corrected card and rule text | Patch records with a date, applied over the component data | `card_lookup` |
| Rulings | Official FAQ answers, developer rulings, adjudicated community cases | Question, answer, refs, authority level, date, what it supersedes | `rules_lookup` |
| Strategy notes | Faction plans, opening priorities, objective paths | Your own write-ups, tagged by faction and game stage | Advisor retrieval |

**Rule chunk**

```json
{
  "id": "lrr.NN.1",
  "ruleset": "pok",
  "title": "Tactical Action: Activation",
  "text": "...",
  "parent": "lrr.NN",
  "related": ["lrr.MM"]
}
```

**Component card**, with timing pulled into fields so the engine and retrieval can filter by window:

```json
{
  "id": "ac.sabotage",
  "type": "action_card",
  "ruleset": "base",
  "name": "Sabotage",
  "timing": { "window": "when", "trigger": "other_player_plays_action_card" },
  "text": "...",
  "engine_ops": null,
  "errata": []
}
```

`engine_ops` is the structured effect when the engine implements the card. `null` means text-only coverage, which tells the agent to answer from the text and flag the result as unverified.

**Ruling**

```json
{
  "id": "ruling.0142",
  "question": "...",
  "answer": "...",
  "refs": ["lrr.NN.1", "ac.sabotage"],
  "authority": "official_faq",
  "date": "2020-11-01",
  "supersedes": []
}
```

Authority levels, highest first: `card_text`, `lrr`, `official_faq`, `developer`, `community`. When two records conflict, the agent cites the higher one and mentions the conflict.

The LRR and card text are Fantasy Flight Games' copyright. Keeping them in a private knowledge base for retrieval is the usual approach; check terms before redistributing the text or the trained model's outputs at scale.

## Tool definitions

Eight tools cover both roles. The referee leans on the first five; the advisor uses all eight.

| Tool | Input | Output | Notes |
| --- | --- | --- | --- |
| `get_state` | `game_id`, `viewer` | Perspective view of the current state | Imports from AsyncTI4 or a manual entry form |
| `rules_lookup` | Question text, `ruleset` | Ranked rule chunks and rulings, with ids | Hybrid search: keywords for card and unit names, embeddings for phrasing |
| `card_lookup` | Card name or id | Component record with errata applied | Fuzzy name match; players misname cards often |
| `validate_action` | State, proposed action | `legal`, violations with rule refs, `coverage` | `coverage` is `engine` or `text_only` |
| `legal_actions` | State, player | Option families (see below) | Families, not a flat list: tactical actions branch too far to enumerate |
| `apply_action` | State, action | Next state + event log | For what-if questions and checking advice |
| `combat_sim` | Attacking and defending units, upgrades, modifiers, pre-combat abilities | Win/draw/loss odds, expected survivors, hits needed | Monte Carlo; covers anti-fighter barrage, space cannon, Sustain Damage |
| `evaluate_position` | State, player | Objective progress, VP race, threats, resources and influence available | Heuristics, not a verdict; the model weighs them |

**Canonical action format**, shared by `validate_action`, `apply_action` and the example data:

```json
{
  "type": "tactical",
  "player": "p3",
  "activate": "201",
  "moves": [
    { "from": "102", "units": { "carrier": 1, "fighter": 2, "infantry": 2 } }
  ],
  "invade": [{ "planet": "quann", "infantry": 2 }],
  "produce": []
}
```

Other action types: `strategic` (card + primary choices), `component` (an action card, tech or ability), `transaction`, `pass`, and `respond` (play into or decline an open window).

**Option families** from `legal_actions`:

- Systems that can be activated, with units able to reach each one.
- Strategy cards whose primary is unused.
- Component actions available now (exhaustible techs, action cards, leaders).
- Responses available for the open window, if any.
- Whether passing is allowed.

The advisor picks a family, drafts a concrete action, and runs it through `validate_action` before recommending it.

## Training and example data sets

Six sets, each usable as few-shot examples, fine-tuning data, or test cases. Sizes are starting targets, not requirements.

| Set | Record | Source | Starting target |
| --- | --- | --- | --- |
| A. Rules Q&A | Question, optional state, answer, refs, authority | Hand-written from real disputes and the LRR FAQ | 300 |
| B. Legality cases | State, proposed action, legal or not, violations with refs | Engine-generated, plus hand-written edge cases; about half illegal | 1,000 |
| C. Timing windows | State with an open window, which responses are available and why | Hand-written around action cards, leaders and faction abilities | 200 |
| D. Advisor decisions | Perspective state, decision type, options, chosen, rationale, refs | AsyncTI4 replays + expert annotation | 500 annotated |
| E. Contrastive advice | Two candidate moves, which is better, why | Expert annotation on set D states | 200 pairs |
| F. Teaching explanations | State, a newer player's question, a plain-language answer | Written for the "how do I play this turn" use | 150 |

**Advisor decision record (set D)**

```json
{
  "id": "adv.000231",
  "ruleset": "pok",
  "state_ref": "asyncti4:pbd00000@r3.strategy",
  "viewer": "p3",
  "decision": "strategy_card_pick",
  "options": ["leadership", "diplomacy", "warfare"],
  "chosen": "warfare",
  "alternatives": [
    { "option": "leadership", "why_not": "..." }
  ],
  "rationale": "...",
  "refs": ["card.strategy.warfare"],
  "annotator": "expert_02",
  "outcome": { "vp_change_this_round": 1, "won_game": false }
}
```

Decision types to cover: tactical action, strategy card pick, production, agenda vote, response to an open window, transaction offer, and status-phase scoring.

**Mining AsyncTI4 replays**

1. Pull completed games from AsyncTI4's public export (`/api/public/games/events`), at most 50 games in total. Strip Discord ids, usernames and teammate ids.
2. The export already filters to competitive games: Thunder's Edge + PoK, 3–8 players, a winner, at least three rounds, and no homebrew, Fog of War, Galactic Events or scenarios.
3. Each game ships with its final save file, which holds full hidden information, and a coarse event log. Label decision types from the events: strategy card picks, research, scoring, card plays and transactions. Snapshots before tactical actions need the events replayed through `apply_action`, and the logs may not cover every round.
4. Attach outcomes (VP gained that round, game winner) as weak labels only. A move by the eventual winner is not automatically a good move.
5. Send a sample to expert annotators for rationale and alternatives. The replay supplies the state; the expert supplies the why.

**Negative examples** carry more signal than they look: illegal actions with the violated rule (set B), advice that loses to the alternative (set E), and "looks legal but isn't" cases such as moving out of an activated system or playing a card outside its window.

## Evaluation

Hold out 20% of sets A to E, split by game rather than by record so no game appears in both training and test. Run the whole suite on every change to the LRR version, card data or prompts.

| Check | Measures | Pass bar for v1 |
| --- | --- | --- |
| Rules Q&A accuracy | Answer correct, and the cited record actually supports it | 90% correct with a valid citation |
| Legality agreement | Agent's legal/illegal call vs the engine on held-out states | 100% where coverage is `engine` |
| Text-only honesty | Share of `text_only` answers flagged unverified | 100% flagged |
| Hidden-information leaks | Advice that references cards or secrets the viewer can't see | Zero |
| Advice quality | Blind expert ranking of the agent's move against the actual move and one alternative | Ranked first or tied in at least half of cases |
| Explanation quality | Rubric: correct, cites, readable by a newer player | Average 4 of 5 |

The pass bars are proposals to tune once you have a first baseline.

For leak testing, build states where the omniscient record contains a decisive hidden card, then check that the advisor's reasoning never depends on it. Also red-team with questions like "what's in their hand?".

## Build order and open questions

Ship a text-only referee first: it is useful on day one and produces the citations and test cases everything later depends on.

1. **State schema + AsyncTI4 importer** for base + PoK, with the perspective projection.
2. **Knowledge base:** LRR chunks, component data with errata, a first batch of rulings. Wire up `rules_lookup` and `card_lookup`.
3. **Referee v0:** answers rules questions from text, always cited, always flagged `text_only`. Build set A alongside it.
4. **Engine integration:** `validate_action` for movement, activation, production, command tokens and the combat sequence. Build sets B and C from it.
5. **`combat_sim`.**
6. **Advisor v0:** `legal_actions`, `apply_action`, `evaluate_position`; few-shot from a first 100 records of set D.
7. **Expert annotation rounds** to grow sets D and E; consider fine-tuning once they're large enough to beat few-shot.
8. **Expand** to further content (such as Twilight's Fall) only if wanted.

**Decisions** (Nov 3, 2026)

- [x] **AsyncTI4 data:** only public data is used. That means the public API (`/api/public/**`, no login) and the bot's open-source component data.
  - The live game view (`/game/{name}/web-data`) shows public information only: hands are counts. It is the source for `get_state` on live games. A player's own hidden cards are not available publicly, so they enter them themselves.
  - The completed-game export carries full saves and events, and is capped at 50 games for the datasets. That covers the first 100 records of set D; the 500-record target may need other sources.
- [x] **Thunder's Edge:** in scope for v1 (decided Nov 3, 2026), because the public export contains only Thunder's Edge + PoK games. Rulesets carry deck ids (for example `deck.action_cards_te`) so lookups return the printing a game actually uses.
- [x] **Engine:** grow the new Python engine in this repo (`warlord.engine`). Movement range, transport capacity, fleet supply, invasion and production are done (Nov 5, 2026). Next: strategy card primaries and component actions. Cards the engine doesn't model are detected from their text. One that could allow a move turns a refusal into "unverified"; one that could forbid a move (laws, opponents' cards) does the same for an approval. A reported verdict is therefore never contradicted by an unmodelled card.
- [x] **Deals between players:** the advisor records stated deals as context and mentions them, but never relies on them. Every plan assumes the other player may break the deal.
- [x] **Licence:** a shared free tool. LRR and card text stay in a private knowledge base. Answers paraphrase and cite rather than reproduce the text: no verbatim quote longer than 20 words, enforced in code.
- [x] **Rules distribution** (Nov 4, 2026): the project is private for now. Warlord never ships rules or card text; `warlord setup` downloads each player's own copy from FFG and AsyncTI4. Before any public release, work through [public-release.md](public-release.md).
- [x] **Annotators:** one annotator (the project owner) for sets D and E, so no disagreement process. Odds-based labels are cross-checked against `combat_sim`.
- [x] **LRR source:** parse the official rules PDFs into one chunk per numbered rule, tagged with their release; output goes under `data/private/`. As of Nov 2026 the current official rules are LRR 2.0 (PoK) plus the Thunder's Edge rulebook, so both are imported (`warlord import-rules`). No newer LRR has been published.
- [x] **Delivery:** an MCP server first, so the player's own agent does the reasoning and the project pays for no model calls. Guarantees move into what the server hands out: the per-seat view, engine verdicts and rule text from the player's own LRR copy. Citation and verification checks become a `check_ruling` tool the agent is told to call. An API-key mode (hosted referee, automated evals) may follow. See [mcp-plan.md](mcp-plan.md).
- [x] **Rulings:** no official FAQ document exists beyond the rulebooks. The Thunder's Edge rulebook's Clarifications section is imported with the rules. Developer and community rulings still have to be collected by hand, each with its source and date.
