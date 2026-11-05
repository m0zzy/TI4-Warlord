# TI4 Warlord

A rules referee and, later, a strategy advisor for Twilight Imperium 4: base game, Prophecy of Kings and Thunder's Edge, with Codex content where a game's decks use it.
The design is in [`docs/design.md`](docs/design.md). Three principles drive it:

- **The engine decides legality.** Where the engine has no coverage, the agent answers from the rules text and marks the answer *unverified*.
- **Every rules answer cites a source**, either a rule, a card or a ruling.
- **Advice comes from one seat.** The agent only ever sees one player's perspective view.

## Status against the build order

| # | Step | State |
|---|------|-------|
| 1 | State schema + perspective projection | Done: `warlord.state`. Games import from AsyncTI4's public API: live games as public views, completed games (capped at 50) with full hidden information. |
| 2 | Knowledge base + `rules_lookup` / `card_lookup` | Done: `warlord.kb`. It has an AsyncTI4 component importer covering every card type, factions, tiles and planets (1,221 records on the real data), and a rules PDF importer for LRR 2.0 and the Thunder's Edge rulebook. The repo ships only sample records; you run the importers yourself. |
| 3 | Referee v0 (text-only, cited) | Done: `warlord.agent.Referee`. Code enforces the citation and verification rules. |
| 4 | Engine `validate_action` | Partial, citing LRR numbers. It checks turn order, passing, tactic tokens, activation, held strategy cards, cards in hand and open windows. Movement is checked in full: range along adjacency (wormholes, wormhole tokens, hyperlanes), anomalies, other players' ships, gravity rifts, Gravity Drive, Antimass and Light/Wave Deflector, Fighter II, transport capacity, and capacity and fleet pool afterwards. Strategy card primaries are checked for all eight cards, including Thunder's Edge's Construction and Warfare. Warfare's free tactical action goes through the full tactical checks. Invasion (committing ground forces) and production (production limit, cost and payment, Sarween Tools, War Sun and flagship limits, blockades) are checked too. Where the space combat's result decides what's allowed, the result is "unverified". Cards it doesn't model are found from their text, and turn a result into "unverified" rather than a wrong verdict. |
| 5 | `combat_sim` | Done. Space combat covers space cannon offense, AFB, Sustain Damage and combat rounds. Ground combat covers bombardment, planetary shields, space cannon defense and mechs' Sustain Damage. `from_game` reads both sides from the game: units, damage, upgrades, faction units, and PDS including adjacent PDS II. |
| 6 | Advisor (`legal_actions`, `apply_action`, `evaluate_position`) | Done, with an `advisor` MCP prompt. `legal_actions` groups the options (activatable systems with the ships that reach them, unused strategy cards, component actions, responses, passing). `apply_action` is a what-if on a copy that stops at combats rather than inventing dice. `evaluate_position` summarizes the VP race, progress on each revealed public objective (all 40 PoK objectives are modelled), spending power and threats. Opponents' card plays are refused as what-ifs, since they'd depend on hidden cards. |
| 7–8 | Annotation rounds, further content | Dataset schemas and the by-game split are in place. Thunder's Edge is in v1 scope: rulesets name the decks a game uses, so lookups return the right printing. |

## Delivery

The plan is to ship as an **MCP server**, so each player's own agent (Claude Desktop, Claude Code, …) does the reasoning and calls these tools. An API-key mode that runs the model ourselves may come later. See [`docs/mcp-plan.md`](docs/mcp-plan.md). The built-in `warlord ask` referee below is that later mode's starting point, and is how automated evals will run.

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

## Use it from Claude (MCP)

1. Install, with PDF support for the rules import:

   ```bash
   pip install -e '.[pdf]'
   ```

2. Build your knowledge base:

   ```bash
   warlord setup
   ```

   This downloads the [Living Rules Reference 2.0](https://images-cdn.fantasyflightgames.com/filer_public/51/55/51552c7f-c05c-445b-84bf-4b073456d008/ti10_pok_living_rules_reference_20_web.pdf) and the [Thunder's Edge rulebook](https://images-cdn.fantasyflightgames.com/filer_public/ca/e7/cae7706f-cdb5-4ee1-a20c-7f76a13d135c/te_rulebook_web.pdf) from Fantasy Flight Games, and sparse-clones the AsyncTI4 card data with git (about 15 MB). It then builds and checks the knowledge base, which takes about 15 seconds. Pass `--lrr`, `--te-rulebook` or `--asyncti4` to use files you already have, or `--no-download` to fetch nothing. Everything goes to `~/.local/share/warlord` (or `$WARLORD_DATA`, or `--data DIR`). The report ends with checks such as "101 LRR topics" and "every engine rule reference is in the LRR".

   The rules and card text are Fantasy Flight Games' copyright, so Warlord never ships them: each player's copy comes straight from FFG. See [`docs/public-release.md`](docs/public-release.md) before distributing Warlord publicly.

3. Connect it to your client.

   In **Claude Code**:

   ```bash
   claude mcp add warlord -- warlord mcp
   ```

   In **Claude Desktop**, add this to `claude_desktop_config.json`:

   ```json
   {"mcpServers": {"warlord": {"command": "warlord", "args": ["mcp"]}}}
   ```

4. Ask a question. Use the `referee` prompt, or just ask: "Import AsyncTI4 game pbd12345, I'm ghost: can my carrier in 205 reach 301 this turn?" The agent calls `fetch_live_game`, `open_game`, `set_my_hand` (it will ask for your cards), `get_state`, the lookups and `validate_action`, and repeats the engine's status line.

To check the referee's answers, use the manual eval kit: 50 questions, a template for recording your agent's answers, and a scorer. See [`docs/evals.md`](docs/evals.md).

## Quick start (command line)

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

Ask the built-in referee (API-key mode, for later). It calls the Claude API, so it needs the `api` extra and `ANTHROPIC_API_KEY`:

```bash
pip install -e '.[api]'
```

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
