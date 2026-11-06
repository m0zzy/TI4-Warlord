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

The importer converts action cards, agendas, technologies, units, leaders, promissory notes, relics, breakthroughs, strategy cards, factions, faction abilities, public and secret objectives, and exploration cards. It also converts decks and strategy card sets. Tiles (with each hyperlane's lane matrix per rotation) and planets come from the sibling `systems/` and `planets/` folders. A full `git clone` has them; a sparse checkout needs `src/main/resources/systems`, `planets` and `data`. It merges duplicate copies (AsyncTI4's `sabo1`…`sabo4` become one `ac.sabotage` with those aliases) and turns the free-text `window` into a structured `timing`. It also reports how many rows it skipped for each source.

Newer printings supersede older ones: a Thunder's Edge leader over its PoK version, or a Codex mech over the original. The importer records this from AsyncTI4's replacement ids and from its `-te`, `_te` and `_omega` suffixes. Lookups hide an older printing whenever a newer one is part of the game's content.

## Rules PDFs

The current official rules are the **Living Rules Reference 2.0** (Prophecy of Kings) plus the **Thunder's Edge rulebook**. As of Nov 2026 no newer LRR has been published, and the TE rulebook points back to the LRR. Both PDFs are on Fantasy Flight Games' site, and `warlord setup` downloads and imports them for you. To run the importer by hand, install the `pdf` extra:

```bash
pip install -e '.[pdf]'
```

```bash
warlord import-rules ti10_pok_living_rules_reference_20_web.pdf --kind lrr --ruleset pok --out data/private/kb/lrr.jsonl
```

```bash
warlord import-rules te_rulebook_web.pdf --kind rulebook --ruleset thunders_edge --pages "4,6-13,16" --out data/private/kb/te_rules.jsonl
```

The LRR import gives one chunk per topic (`lrr.82`) and per sub-rule (`lrr.82.2`), with related topics linked. On LRR 2.0 that is all 101 topics and 509 sub-rules; rule 58.1 does not exist in the PDF itself. The page list for the TE rulebook skips its fiction, component pages and map appendix. Each remaining ALL-CAPS section becomes one chunk (`te.coexist`, `te.the_fracture`, `te.clarifications`, and so on). Ids should follow the LRR numbering so citations read naturally, for example `lrr.89.1`.

The engine cites rules by topic (see `TOPICS` in `engine/validate.py`). `src/warlord/data/lrr_refs.json` pins each topic to its LRR 2.0 rule ids (ids only, no text). The CLI loads it, and a pinned id is used only when the knowledge base holds it. Otherwise, for example with the sample data, the engine falls back to the best search match.

## Games from AsyncTI4

Two commands pull games from AsyncTI4's public API:

```bash
warlord fetch-asyncti4 live pbd12345 --kb data/private/kb
```

```bash
warlord fetch-asyncti4 completed --count 10 --kb data/private/kb
```

- **`live`** imports a game's public view. Hands are counts, and the state marks them unknown. A player adds their own cards with `warlord set-hand --game pbd12345 --viewer ghost --action-cards ac.sabotage`.
- **`completed`** imports finished games from the export, with full hidden information, for the example datasets. It stops at 50 games in total, counted across runs in `data/private/games/_asyncti4_export_manifest.json`.
- **Privacy:** seats are keyed by faction. Discord ids, usernames, notepads and channel ids are never written; the raw export is not saved.
- **VP from saves** is rebuilt from scored objectives, custom VP sources and Support for the Throne. Other VP effects would be missed, so check against the live view when it matters.
