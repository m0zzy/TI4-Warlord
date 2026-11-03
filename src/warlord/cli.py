"""Command line: ask the referee, inspect views, validate actions, run combat odds, import data."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from warlord.engine.combat import combat_sim
from warlord.kb.store import KnowledgeBase
from warlord.state.store import GameStore
from warlord.tools.registry import ToolContext, run_tool


def _default(*candidates: str) -> str:
    for c in candidates:
        if Path(c).exists():
            return c
    return candidates[-1]


def _kb(paths: list[str] | None) -> KnowledgeBase:
    paths = paths or [_default("data/private/kb", "data/sample/kb")]
    return KnowledgeBase.from_paths(paths)


def _ctx(args) -> ToolContext:
    store = GameStore(args.games) if args.game else None
    return ToolContext(kb=_kb(args.kb), store=store, game_id=args.game, viewer=args.viewer)


def cmd_ask(args) -> int:
    from warlord.agent.referee import Referee

    ref = Referee(_ctx(args), model=args.model, effort=args.effort)
    answer = ref.ask(args.question)
    print(json.dumps(answer.to_dict(), indent=2) if args.json else answer.render())
    return 0


def cmd_view(args) -> int:
    print(GameStore(args.games).view(args.game, args.viewer).model_dump_json(indent=2, exclude_none=True))
    return 0


def cmd_validate(args) -> int:
    out, err = run_tool(_ctx(args), "validate_action", {"action": json.loads(args.action)})
    print(json.dumps(json.loads(out), indent=2))
    return 1 if err else 0


def cmd_combat(args) -> int:
    result = combat_sim(json.loads(args.attacker), json.loads(args.defender), trials=args.trials, seed=args.seed)
    print(json.dumps(result, indent=2))
    return 0


def cmd_search(args) -> int:
    kb = _kb(args.kb)
    out = kb.card_lookup(args.query) if args.cards else kb.rules_lookup(args.query)
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


def cmd_import(args) -> int:
    from warlord.kb.importers.asyncti4 import import_checkout, write_jsonl

    report = import_checkout(args.data_dir, include=args.include)
    path = write_jsonl(report.records, args.out)
    print(json.dumps({**report.summary(), "written": str(path)}, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="warlord", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    def game_args(sp, required=False):
        sp.add_argument("--game", required=required, help="game id")
        sp.add_argument("--viewer", required=required, help="asking player's seat, e.g. p3")
        sp.add_argument("--games", default=_default("data/private/games", "data/sample/games"))
        sp.add_argument("--kb", action="append", help="knowledge base dir or .jsonl (repeatable)")

    a = sub.add_parser("ask", help="ask the referee a question")
    a.add_argument("question")
    game_args(a)
    a.add_argument("--model", default="claude-opus-5-5")
    a.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"])
    a.add_argument("--json", action="store_true")
    a.set_defaults(func=cmd_ask)

    v = sub.add_parser("view", help="print a seat's perspective view")
    game_args(v, required=True)
    v.set_defaults(func=cmd_view)

    va = sub.add_parser("validate", help="validate a canonical action")
    va.add_argument("action", help="action JSON")
    game_args(va, required=True)
    va.set_defaults(func=cmd_validate)

    c = sub.add_parser("combat", help="space combat odds")
    c.add_argument("attacker", help='side JSON, e.g. {"units":{"dreadnought":2}}')
    c.add_argument("defender")
    c.add_argument("--trials", type=int, default=5000)
    c.add_argument("--seed", type=int)
    c.set_defaults(func=cmd_combat)

    s = sub.add_parser("search", help="search rules (or cards with --cards)")
    s.add_argument("query")
    s.add_argument("--cards", action="store_true")
    s.add_argument("--kb", action="append")
    s.set_defaults(func=cmd_search)

    i = sub.add_parser("import-asyncti4", help="convert AsyncTI4 component JSON to KB records")
    i.add_argument("data_dir", help="path to TI4_map_generator_bot/src/main/resources/data")
    i.add_argument("--out", default="data/private/kb/components.asyncti4.jsonl")
    i.add_argument("--include", nargs="+", default=["base", "pok"])
    i.set_defaults(func=cmd_import)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
