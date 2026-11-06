# TI4 Warlord

A rules referee and strategy advisor for Twilight Imperium 4, covering the base game, Prophecy of Kings and Thunder's Edge (with Codex content where a game's decks use it). It runs as an **MCP server**, so your own Claude client does the reasoning while Warlord supplies what has to be right: one player's view of the game, the rules and cards, a legality engine, and combat odds.

The design is in [`docs/design.md`](docs/design.md), and the delivery plan in [`docs/mcp-plan.md`](docs/mcp-plan.md). Three principles drive it:

- **The engine decides legality.** Where the engine has no coverage, the answer comes from the rules text and is labelled *unverified*.
- **Every rules answer cites a source:** a rule number, a card, or a ruling.
- **Advice comes from one seat.** The agent only ever sees the asking player's view, and opponents' hidden cards are never sent to it.

## Use it from Claude

1. Install:

   ```bash
   pip install -e '.[pdf]'
   ```

2. Build your knowledge base:

   ```bash
   warlord setup
   ```

   This downloads the [Living Rules Reference 2.0](https://images-cdn.fantasyflightgames.com/filer_public/51/55/51552c7f-c05c-445b-84bf-4b073456d008/ti10_pok_living_rules_reference_20_web.pdf) and the [Thunder's Edge rulebook](https://images-cdn.fantasyflightgames.com/filer_public/ca/e7/cae7706f-cdb5-4ee1-a20c-7f76a13d135c/te_rulebook_web.pdf) from Fantasy Flight Games, and sparse-clones AsyncTI4's card data with git (about 15 MB). It then builds and checks the knowledge base in about 15 seconds, and the report ends with checks such as "101 LRR topics".

   Everything goes to `~/.local/share/warlord` (or `$WARLORD_DATA`, or `--data DIR`). Pass `--lrr`, `--te-rulebook` or `--asyncti4` to use files you already have, or `--no-download` to fetch nothing. The rules and card text are FFG's copyright, so Warlord never ships them; see [`docs/public-release.md`](docs/public-release.md) before distributing Warlord.

3. Connect it. In **Claude Code**, run this from the clone, with the virtual environment active:

   ```bash
   claude mcp add warlord -- "$(which warlord)" mcp
   ```

   In **Claude Desktop**, add the same full path to `claude_desktop_config.json`:

   ```json
   {"mcpServers": {"warlord": {"command": "/full/path/to/.venv/bin/warlord", "args": ["mcp"]}}}
   ```

4. Ask. Use the `referee` prompt for rules and legality, or the `advisor` prompt for what to do. For example: "Import AsyncTI4 game pbd12345, I'm ghost: what should I do this turn?"

### The tools

| Tool | What it does |
| --- | --- |
| `list_games`, `open_game`, `fetch_live_game`, `set_my_hand` | Import a live AsyncTI4 game (public view), open it as your seat, and enter your own hidden cards. |
| `get_state` | Your seat's view: the map, every player's public information, your hand, and opponents' hands as counts. |
| `rules_lookup`, `card_lookup` | Search the LRR and the Thunder's Edge rulebook, and look up cards with the printing your game uses. |
| `validate_action` | The legality engine, with a `status_line` to repeat. |
| `check_ruling` | Checks a drafted ruling's citations, its "verified" claim, and its quoting before the agent answers. |
| `combat_sim` | Space or ground combat odds, built `from_game` from the real units, upgrades and PDS. |
| `legal_actions`, `apply_action`, `evaluate_position` | Advisor tools: option families, what-ifs on a copy of the game, and a position summary with objective progress and threats. |

## What the engine checks

`validate_action` returns **legal**, **illegal** (with LRR rule numbers), or **not verified** (listing what it couldn't check):

- **Turn and passing:** turn order, passing (including unused strategy cards), cards in hand, and open timing windows.
- **Activation and movement:**
  - activation, tactic tokens, and units present;
  - range along adjacency: wormholes, wormhole tokens and hyperlanes;
  - anomalies, other players' ships, and gravity rifts;
  - Gravity Drive, Antimass Deflectors, Light/Wave Deflector and Fighter II;
  - transport capacity, plus capacity and fleet-pool warnings.
- **Invasion and production:**
  - committing ground forces;
  - production limits and cost, payment and Sarween Tools;
  - blockades, and War Sun and flagship limits.
- **Strategy cards:** the primary-ability choices for all eight cards, including Thunder's Edge's Construction and Warfare.
- **When it says "not verified":**
  - when a combat's result decides what's allowed;
  - for The Fracture, and the custodians token on Mecatol Rex;
  - when a card the engine doesn't model could change the answer. Those cards are found from their text, so a reported verdict is never one an unmodelled card would overturn.

`evaluate_position` judges all 40 PoK public objectives, and the state-checkable secret objectives in your own hand. It also lists threats: other players' ships that can reach you next turn, with expected hits.

## Checking it

- **Tests:** `pytest`, also run by CI on every push.
- **Agent answers:** the manual eval kit has 50 draft questions, an answer template and a scorer; see [`docs/evals.md`](docs/evals.md).
- **Legality cases:** `warlord datasets legality` generates engine-decided legality cases (example set B) from your stored games.

## Command line

The same engine works without a Claude client, on the sample game:

```bash
warlord view --game pbd00000 --viewer p3
```

```bash
warlord validate '{"type":"tactical","player":"p3","activate":"201","moves":[{"from":"101","units":{"cruiser":1}}]}' --game pbd00000 --viewer p3
```

```bash
warlord combat '{"units":{"dreadnought":2,"carrier":1,"fighter":3}}' '{"units":{"cruiser":2,"destroyer":1,"pds":1}}'
```

Other commands:
- `warlord fetch-asyncti4 live|completed` imports games. Completed games are capped at 50, for datasets.
- `warlord import-rules` and `warlord import-asyncti4` run the importers by hand.
- `warlord eval ...` runs the eval kit.

### API-key mode (later)

`warlord ask` runs the built-in referee: our own Claude call, with the checks enforced after the model answers. That guarantees citations, labels and paraphrasing, which over MCP are only advised. It needs the `api` extra and `ANTHROPIC_API_KEY`:

```bash
pip install -e '.[api]'
```

```bash
warlord ask "Can my cruiser in 101 join an attack on 201 this turn?" --game pbd00000 --viewer p3
```

## Layout

```
src/warlord/
  mcp_server.py         the MCP server (stdio)
  setup.py, paths.py    warlord setup and the data folder
  ruleset.py            content tags and per-game decks (PoK, Thunder's Edge)
  state/                GameState, perspective views, the game store, AsyncTI4 game import
  actions.py            the canonical action format
  kb/                   knowledge base, search, errata, AsyncTI4 card and rules PDF importers
  engine/               validate_action, movement, invasion and production, strategy cards,
                        geometry, combat, what-ifs (apply), options, objectives, evaluation
  tools/                tool definitions and handlers, and check_ruling
  agent/                prompts (referee, advisor), quote checks, the API-key referee
  datasets/, evals/     example-set schemas, set B generation, the eval kit and checks
  data/                 package data: LRR rule refs, AsyncTI4 colors, eval questions and game
data/sample/            a sample game and knowledge base (paraphrased placeholders, not the LRR)
```

More on the data: [`docs/knowledge-base.md`](docs/knowledge-base.md).
