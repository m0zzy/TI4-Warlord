# Populating the knowledge base

The knowledge base is a folder of `.jsonl` files. Each line is one record, and its `kind` field says which type it is. Lines starting with `//` are comments. Load the folder with `KnowledgeBase.from_paths([...])`, or pass `--kb DIR` to the CLI.

The LRR, card text and AsyncTI4 exports are Fantasy Flight Games' copyright, so keep them in `data/private/` (git-ignored). Check the licence terms before you redistribute them.

## Record kinds

| kind | Model | Id convention | Notes |
|------|-------|---------------|-------|
| `rule` | `RuleChunk` | `lrr.<number>` or `lrr.<number>.<sub>` | One chunk per numbered rule. Set `parent` on sub-rules, because `rules_lookup` pulls the parent in. |
| `component` | `ComponentCard` | `ac.`, `agenda.`, `tech.`, `unit.`, `pn.`, `obj.`, `so.`, `relic.`, `leader.` + slug | Set `engine_ops` only when the engine really implements the card. Leave it `null` to mark text-only coverage. |
| `errata` | `ErrataPatch` | `errata.<n>` | Patches are applied oldest first. When `old` is set, the patch replaces that substring and fails loudly if it is missing. |
| `ruling` | `Ruling` | `ruling.<n>` | `authority` is one of `card_text`, `lrr`, `official_faq`, `developer`, `learn_to_play` or `community`. `supersedes` hides older rulings from the results. |
| `strategy_note` | `StrategyNote` | `note.<slug>` | Your own write-ups, tagged by faction and stage, for the advisor. |

Every record has a `ruleset` tag (`base`, `pok`, `codex` or `thunders_edge`). Lookups only return records that the game's ruleset allows.

## Component data from AsyncTI4

Clone the bot repository, then convert its data folder. Only `base` and `pok` sources are kept by default. Add `codex` or `thunders_edge` when they come into scope:

```bash
git clone --depth 1 https://github.com/AsyncTI4/TI4_map_generator_bot data/raw/asyncti4
```

```bash
warlord import-asyncti4 data/raw/asyncti4/src/main/resources/data --out data/private/kb/components.asyncti4.jsonl
```

The importer currently converts action cards, agendas, technologies and units. It merges duplicate copies (AsyncTI4's `sabo1`…`sabo4` become one `ac.sabotage` with those aliases) and turns the free-text `window` into a structured `timing`. It also reports how many rows it skipped for each source.

## The Living Rules Reference

No LRR parser ships yet, because the source format (PDF or a community text export) is still to be decided. Whatever produces the chunks should emit one `rule` record per numbered rule. Ids should follow the LRR numbering so citations read naturally, for example `lrr.89.1`.

The engine cites rules by topic (see `TOPICS` in `engine/validate.py`). Once the LRR is loaded, pin each topic to its exact rule ids by passing `ref_overrides` to `ToolContext`; otherwise the engine falls back to the best search match.
