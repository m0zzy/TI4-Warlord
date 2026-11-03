"""TI4 Warlord as an MCP server (stdio).

The player's own agent does the reasoning. This server supplies what has to
be right: one seat's view of a game, rules and card lookup, the legality
engine and combat odds. See docs/mcp-plan.md.

One server process serves one client. ``open_game`` binds the session to a
game and a seat; every game tool then works from that seat's perspective view.
Games imported with every player's hidden cards (``asyncti4_export``) are
never opened.

Run it with ``warlord mcp`` (or ``python -m warlord.mcp_server``). Nothing but
protocol goes to stdout; logs go to stderr.
"""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from warlord.agent.prompts import REFEREE_MCP
from warlord.engine.validate import load_ref_overrides
from warlord.kb.store import KnowledgeBase
from warlord.paths import LRR_REFS, data_dir, games_dir, kb_dir
from warlord.ruleset import V1_RULESETS
from warlord.state.store import GameNotFound, GameStore
from warlord.tools.registry import TOOL_DEFS, ToolContext, run_tool

log = logging.getLogger("warlord.mcp")

INSTRUCTIONS = (
    "Rules referee tools for Twilight Imperium 4 (base, Prophecy of Kings, Thunder's Edge). "
    "Use the 'referee' prompt for how to rule. In short: open a game as the asking player, look up "
    "every rule and card you rely on, validate concrete moves with the engine and repeat its "
    "status_line, use combat_sim for odds, and cite the record ids the tools return."
)


@dataclass
class Session:
    kb: KnowledgeBase
    store: GameStore
    ref_overrides: dict[str, list[str]] = field(default_factory=dict)
    game_id: str | None = None
    seat: str | None = None
    ctx: ToolContext | None = None

    def context(self) -> ToolContext:
        if self.ctx is None:
            self.ctx = ToolContext(
                kb=self.kb,
                store=self.store if self.game_id else None,
                game_id=self.game_id,
                viewer=self.seat,
                default_ruleset=V1_RULESETS["thunders_edge"],
                ref_overrides=self.ref_overrides,
            )
        return self.ctx

    def bind(self, game_id: str | None, seat: str | None) -> None:
        self.game_id, self.seat, self.ctx = game_id, seat, None

    def require_game(self) -> None:
        if not self.game_id:
            raise ToolError("No game is open. Call list_games, then open_game with the game and your seat.")

    def call(self, name: str, args: dict) -> Any:
        if not self.kb.records and name in ("rules_lookup", "card_lookup"):
            raise ToolError("The rules and card data are not installed yet. Run `warlord setup` first.")
        content, is_error = run_tool(self.context(), name, args)
        data = json.loads(content)
        if is_error:
            raise ToolError(data.get("error", "tool failed"))
        return data


def load_session(root: Path) -> Session:
    kb_path = kb_dir(root)
    files = sorted(kb_path.glob("*.jsonl")) if kb_path.exists() else []
    kb = KnowledgeBase.from_paths(files) if files else KnowledgeBase()
    if not files:
        log.warning("No knowledge base in %s; run `warlord setup`.", kb_path)
    return Session(kb=kb, store=GameStore(games_dir(root)), ref_overrides=load_ref_overrides(LRR_REFS))


def build_server(session: Session) -> MCPServer:
    server = MCPServer("ti4-warlord", instructions=INSTRUCTIONS)

    # ---------------------------------------------------------------- games

    @server.tool(description="List the games this server can open, with their seats (factions).")
    def list_games() -> dict:
        games = []
        for gid in session.store.list_games():
            try:
                st = session.store.load(gid)
            except Exception as e:  # noqa: BLE001 - report unreadable files, keep listing
                games.append({"game": gid, "error": f"unreadable: {e}"})
                continue
            if st.meta.source == "asyncti4_export":
                continue
            games.append(
                {
                    "game": gid,
                    "seats": sorted(st.players),
                    "round": st.turn.round,
                    "phase": st.turn.phase,
                    "thunders_edge": st.ruleset.thunders_edge,
                }
            )
        return {"games": games, "open": {"game": session.game_id, "seat": session.seat}}

    @server.tool(
        description=(
            "Open a game as one seat (the asking player's faction, e.g. 'ghost'). Every game tool then "
            "works from that seat's view only. Opening another seat starts a fresh session record."
        )
    )
    def open_game(game: str, seat: str) -> dict:
        try:
            st = session.store.load(game)
        except GameNotFound as e:
            raise ToolError(f"No game {game!r}. Call list_games, or fetch_live_game to import it.") from e
        if st.meta.source == "asyncti4_export":
            raise ToolError("This game holds every player's hidden cards and cannot be opened here.")
        if seat not in st.players:
            raise ToolError(f"No seat {seat!r} in {game}. Seats: {', '.join(sorted(st.players))}.")
        session.bind(game, seat)
        me = st.players[seat]
        return {
            "game": game,
            "seat": seat,
            "faction": me.faction,
            "round": st.turn.round,
            "phase": st.turn.phase,
            "hand_known": me.private.known,
            "note": None if me.private.known else "Your hand is not known yet: ask the player and call set_my_hand.",
        }

    @server.tool(
        description=(
            "Import an AsyncTI4 game's public view by its name (e.g. 'pbd12345'). Hands come in as counts; "
            "the player can then add their own cards with set_my_hand."
        )
    )
    def fetch_live_game(name: str) -> dict:
        from warlord.state.importers.fetch import fetch_live

        try:
            result = fetch_live(name, session.kb)
        except Exception as e:  # noqa: BLE001 - network and format errors go back to the agent
            raise ToolError(f"Could not import {name!r} from AsyncTI4: {e}") from e
        session.store.save(result.state)
        return {
            "game": result.state.meta.game_id,
            "seats": sorted(result.state.players),
            "notes": result.notes,
            "unmapped": result.unmapped,
        }

    @server.tool(
        description=(
            "Record the asking player's own hidden cards in the open game, by name or id. Only the open "
            "seat's hand can be set. Opponents still see only counts."
        )
    )
    def set_my_hand(
        action_cards: list[str] | None = None,
        promissory_notes: list[str] | None = None,
        secret_objectives: list[str] | None = None,
    ) -> dict:
        from warlord.state.importers.asyncti4 import set_hand

        session.require_game()
        state = session.store.load(session.game_id)  # type: ignore[arg-type]
        resolved: dict[str, list[dict]] = {}

        def resolve(names: list[str] | None, kind: str) -> list[str]:
            ids = []
            for n in names or []:
                out = session.kb.card_lookup(n, ruleset=state.ruleset)
                match = out.get("match")
                if match is None or match.get("type") != kind:
                    raise ToolError(f"{n!r} is not a known {kind.replace('_', ' ')} in this game.")
                ids.append(match["id"])
                resolved.setdefault(kind, []).append({"given": n, "id": match["id"], "name": match["name"]})
            return ids

        set_hand(
            state,
            session.seat,  # type: ignore[arg-type]
            resolve(action_cards, "action_card"),
            resolve(promissory_notes, "promissory_note"),
            resolve(secret_objectives, "secret_objective"),
        )
        session.store.save(state)
        session.bind(session.game_id, session.seat)  # reload the view
        return {"saved": True, "resolved": resolved}

    @server.tool(description=TOOL_DEFS["get_state"]["description"])
    def get_state() -> dict:
        session.require_game()
        return session.call("get_state", {})

    # ---------------------------------------------------------------- rules

    @server.tool(description=TOOL_DEFS["rules_lookup"]["description"])
    def rules_lookup(question: str, k: int = 6) -> dict:
        return session.call("rules_lookup", {"question": question, "k": k})

    @server.tool(description=TOOL_DEFS["card_lookup"]["description"])
    def card_lookup(name: str) -> dict:
        return session.call("card_lookup", {"name": name})

    @server.tool(
        description=TOOL_DEFS["validate_action"]["description"]
        + " Includes status_line: repeat it to the player."
        + " " + TOOL_DEFS["validate_action"]["input_schema"]["properties"]["action"]["description"]
    )
    def validate_action(action: dict) -> dict:
        session.require_game()
        return session.call("validate_action", {"action": action})

    @server.tool(description=TOOL_DEFS["combat_sim"]["description"])
    def combat_sim(
        attacker: dict,
        defender: dict,
        trials: int = 5000,
        space_cannon: bool = True,
        afb: bool = True,
    ) -> dict:
        return session.call(
            "combat_sim",
            {"attacker": attacker, "defender": defender, "trials": trials, "space_cannon": space_cannon, "afb": afb},
        )

    # -------------------------------------------------------------- prompts

    @server.prompt(description="How to act as the TI4 rules referee with these tools.")
    def referee(question: str = "") -> str:
        return REFEREE_MCP + (f"\nThe player asks: {question}" if question else "")

    return server


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="warlord mcp", description="Run the TI4 Warlord MCP server over stdio.")
    p.add_argument("--data", help="data folder (default: $WARLORD_DATA or ~/.local/share/warlord)")
    args = p.parse_args(argv)
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    server = build_server(load_session(data_dir(args.data)))
    server.run("stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
