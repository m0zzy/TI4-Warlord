# TI4 Warlord

A rules referee and, later, a strategy advisor for Twilight Imperium 4: base game, Prophecy of Kings and Thunder's Edge, with Codex content where a game's decks use it.
The design is in [`docs/design.md`](docs/design.md). Three principles drive it:

- **The engine decides legality.** Where the engine has no coverage, the agent answers from the rules text and marks the answer *unverified*.
- **Every rules answer cites a source**, either a rule, a card or a ruling.
- **Advice comes from one seat.** The agent only ever sees one player's perspective view.

## Status against the build order

| # | Step | State |
|---|------|-------|
| 1 | State schema + perspective projection | Done: `warlord.state`. The AsyncTI4 *game snapshot* importer is not written yet (waiting on the maintainers' permission, see open questions). |
| 2 | Knowledge base + `rules_lookup` / `card_lookup` | Done: `warlord.kb`, with an AsyncTI4 *component data* importer. It covers action cards, agendas, techs, units, leaders, promissory notes, relics, breakthroughs, strategy cards, decks and strategy card sets. Objectives, explores, factions and tiles are still to come. The repo ships only sample records; you load the real LRR yourself. |
| 3 | Referee v0 (text-only, cited) | Done: `warlord.agent.Referee`. Code enforces the citation and verification rules. |
| 4 | Engine `validate_action` | Partial. It checks turn order, passing, tactic tokens, activation, moving out of activated systems, units present, held strategy cards, cards in hand and open windows. It does not check movement range, capacity, fleet supply, invasion or production yet, and reports those as `unchecked`. |
| 5 | `combat_sim` | Done for space combat: space cannon offense, AFB, Sustain Damage and combat rounds. |
| 6 | Advisor (`legal_actions`, `apply_action`, `evaluate_position`) | Tool definitions only; the handlers return `not_implemented`. |
| 7–8 | Annotation rounds, further content | Dataset schemas and the by-game split are in place. Thunder's Edge is in v1 scope: rulesets name the decks a game uses, so lookups return the right printing. |

## Layout

```
src/warlord/
  ruleset.py            ruleset tags (base / pok / codex / thunders_edge)
  state/                omniscient GameState, PerspectiveView, project(), GameStore
  actions.py            canonical action format (tactical, strategic, component, transaction, pass, respond)
  kb/                   record types, errata, hybrid search, AsyncTI4 component importer
  engine/               validate_action (partial), unit stats, combat_sim
  tools/registry.py     the 8 tool definitions + handlers, bound to one game and one seat
  agent/                referee prompt, output schema, tool-use loop, contract enforcement
  datasets/schemas.py   record types for sets A–F, split_by_game
  evals/checks.py       citation validity, legality agreement, text-only honesty, leak detection
data/sample/            sample game + sample KB (paraphrased placeholders, not the LRR)
```

## Quick start

```bash
pip install -e '.[dev]'
```

```bash
pytest
```

Inspect what one seat can see, check a move, and get combat odds (no API key needed):

```bash
warlord view --game pbd00000 --viewer p3
```

```bash
warlord validate '{"type":"tactical","player":"p3","activate":"201","moves":[{"from":"101","units":{"cruiser":1}}]}' --game pbd00000 --viewer p3
```

```bash
warlord combat '{"units":{"dreadnought":2,"carrier":1,"fighter":3}}' '{"units":{"cruiser":2,"destroyer":1,"pds":1}}'
```

Ask the referee. This calls the Claude API, so it needs `ANTHROPIC_API_KEY` or an `ant auth login` profile:

```bash
warlord ask "Can my cruiser in 101 join an attack on 201 this turn?" --game pbd00000 --viewer p3
```

## How the referee keeps its promises

The model drafts a structured answer (`ruling`, `answer`, `citations`, `verified`, …). Then `Referee.enforce` applies these rules in code:

- A citation is kept only if its id appeared in a tool result during this conversation. Anything else is dropped and reported.
- An answer is **verified** only if `validate_action` ran with `coverage: "engine"` and no step fell back to text-only coverage. Pure rules questions are therefore always unverified in v0, as the design requires.
- `get_state` refuses any seat other than the asking player, and the engine only ever runs on that seat's perspective view.
- Answers paraphrase and cite instead of reproducing rule or card text, because this is a shared tool and the text is copyrighted. If an answer copies more than 20 consecutive words from a source record, the referee asks the model to rewrite it once. If the long quote is still there, it is cut and replaced with a pointer to the cited source (`trimmed_quotes` in the result).

Requests use `claude-opus-5-5` with adaptive thinking, a JSON-schema output format, prompt caching (the system prompt and tool list are byte-stable; the game id goes in the user message), and server-side refusal fallback (`fallbacks: "default"`). Pass `fallbacks=False` to `Referee` to turn that off.

## Loading real data

See [`docs/knowledge-base.md`](docs/knowledge-base.md). In short: real LRR, card and AsyncTI4 data goes under `data/private/`, which is git-ignored because the text is Fantasy Flight Games' copyright. The CLI uses `data/private/kb` when it exists and falls back to `data/sample/kb`.
