# Before making Warlord public

**Status (Nov 4, 2026): private project.** Nothing here blocks private use. Work through this list before publishing the package, opening the repository, or hosting Warlord for other people.

## 1. Rules and card text (the main issue)

The Living Rules Reference, the Thunder's Edge rulebook and all card text are Fantasy Flight Games' copyright. That includes the card text inside AsyncTI4's data files.

How Warlord handles it now:
- **The repo and package ship no rules or card text.** Only ids and numbers are included, such as `lrr_refs.json` and the color table. The sample knowledge base holds short paraphrases written for tests.
- **`warlord setup` downloads each player's own copy.** The PDFs come from FFG's own links (`OFFICIAL_PDFS` in `src/warlord/setup.py`), and the card data comes from AsyncTI4's repository. Both go straight onto the player's machine, into their private data folder.
- **Answers paraphrase.** The referee and the MCP prompt tell the agent to paraphrase, and the built-in referee cuts verbatim quotes over 20 words (`MAX_QUOTE_WORDS`).

To check before going public:
- [ ] **Automated downloads:** FFG/Asmodee's terms (or fan-content policy) on a tool fetching their PDFs for users.
- [ ] **Tool results:** whether showing rule text to the user's agent is acceptable, given each player supplies their own copy.
- [ ] **Bundling:** if you want the rules included in Warlord, ask FFG/Asmodee for written permission. Without it, keep the download-on-setup approach.
- [ ] **Hosted setups:** a hosted version (API-key mode or a hosted MCP server) serves many people from one copy. Before hosting, switch `rules_lookup` to rule ids plus short excerpts, and confirm whether that is acceptable.

## 2. AsyncTI4

- [ ] **Licence scope:** AsyncTI4's repository is public domain (the Unlicense) for its *software only*. Its `LICENSE` excludes art assets, and the card text in its data is FFG's, not theirs to license. Section 1 covers it.
- [ ] **The 50-game cap:** get the agreement on completed-game exports in writing, and confirm it still holds for a public release. `MAX_EXPORT_GAMES` in `src/warlord/state/importers/fetch.py` enforces it.
- [ ] **Public API at scale:** ask the maintainers before many users hit `/api/public/game/{name}/web-data`. Consider caching and a rate limit.
- [ ] **Discord sign-in:** a hosted server that uses Discord sign-in to map players to seats needs the maintainers' agreement, and ideally per-player opt-in.

## 3. Names and branding

- [ ] **Disclaimer:** "Twilight Imperium" and the faction and card names are FFG/Asmodee trademarks. Add a disclaimer that Warlord is an unofficial fan tool, not endorsed by FFG or Asmodee.
- [ ] **Art:** use no FFG art or logos.

## 4. Privacy

- [ ] **Imports:** imports already drop Discord ids, usernames, notepads and channel ids. Re-check this whenever the import formats change; the tests in `tests/test_game_import.py` cover it.
- [ ] **Hosted setups:** hosting needs a privacy notice saying what game data is stored, and for how long.

## 5. Practical

- [ ] **Download links:** FFG's links can move. Setup fails with a clear message and accepts a path, but check the links before each release.
- [ ] **Package name:** pick the final name on PyPI, and check it is free.
