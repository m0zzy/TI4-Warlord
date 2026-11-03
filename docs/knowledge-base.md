# Populating the knowledge base

The knowledge base is a folder of `.jsonl` files. Each line is one record, and its `kind` field says which type it is. Lines starting with `//` are comments. Load the folder with `KnowledgeBase.from_paths([...])`, or pass `--kb DIR` to the CLI.

The LRR, card text and AsyncTI4 exports are Fantasy Flight Games' copyright, so keep them in `data/private/` (git-ignored). Check the licence terms before you redistribute them.

## Record kinds

| kind | Model | Id convention | Notes |
|------|-------|---------------|-------|
| `rule` | `RuleChunk` | `lrr.<number>` or `lrr.<number>.<sub>` | One chunk per numbered rule. Set `parent` on sub-rules, because `rules_lookup` pulls the parent in. |
| `component` | `ComponentCard` | `ac.`, `agenda.`, `tech.`, `unit.`, `pn.`, `obj.`, `so.`, `relic.`, `leader.`, `breakthrough.`, `sc.` + slug | Set `engine_ops` only when the engine really implements the card. Leave it `null` to mark text-only coverage. |
| `errata` | `ErrataPatch` | `errata.<n>` | Patches are applied oldest first. When `old` is set, the patch replaces that substring and fails loudly if it is missing. |
| `ruling` | `Ruling` | `ruling.<n>` | `authority` is one of `card_text`, `lrr`, `official_faq`, `developer`, `learn_to_play` or `community`. `supersedes` hides older rulings from the results. |
| `strategy_note` | `StrategyNote` | `note.<slug>` | Your own write-ups, tagged by faction and stage, for the advisor. |
| `deck` | `Deck` | `deck.<alias>`, `deck.sc_set.<alias>` | The cards in one AsyncTI4 deck or strategy card set, as component ids. |

Every record has a `ruleset` tag (`base`, `pok`, `codex` or `thunders_edge`), and lookups only return records the game's ruleset allows. A ruleset can also name a deck per card type. The presets `POK_RULESET` and `THUNDERS_EDGE_RULESET` in `warlord.ruleset` use AsyncTI4's default decks. Lookups of that type then return only cards in the deck, so a game gets the base, Codex or Thunder's Edge printing it actually uses. Faction cards are not filtered by decks. When a lookup matches a card that exists but isn't in the game, `card_lookup` reports it under `not_in_this_game`.

## Component data from AsyncTI4

Clone the bot repository, then convert its data folder. The `base`, `pok`, `codex` and `thunders_edge` sources are kept by default. Twilight's Fall and homebrew are skipped:

```bash
git clone --depth 1 https://github.com/AsyncTI4/TI4_map_generator_bot data/raw/asyncti4
```

```bash
warlord import-asyncti4 data/raw/asyncti4/src/main/resources/data --out data/private/kb/components.asyncti4.jsonl
```

The importer converts action cards, agendas, technologies, units, leaders, promissory notes, relics, breakthroughs and strategy cards, plus decks and strategy card sets. Objectives, explores, factions and tiles are not converted yet. It merges duplicate copies (AsyncTI4's `sabo1`…`sabo4` become one `ac.sabotage` with those aliases) and turns the free-text `window` into a structured `timing`. It also reports how many rows it skipped for each source.

## The Living Rules Reference

No LRR parser ships yet. The agreed source is the official LRR PDF, in the edition that covers Thunder's Edge. Tag each chunk with the release it belongs to. The parser should emit one `rule` record per numbered rule. Ids should follow the LRR numbering so citations read naturally, for example `lrr.89.1`.

The engine cites rules by topic (see `TOPICS` in `engine/validate.py`). Once the LRR is loaded, pin each topic to its exact rule ids by passing `ref_overrides` to `ToolContext`; otherwise the engine falls back to the best search match.
