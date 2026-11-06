"""Where Warlord keeps its data.

The data folder holds the knowledge base built by ``warlord setup`` and the
games the MCP server can open:

    <data>/kb/      components.asyncti4.jsonl, lrr.jsonl, te_rules.jsonl
    <data>/games/   one JSON file per game (live, public-view imports)

It is ``$WARLORD_DATA`` when set, else ``$XDG_DATA_HOME/warlord``, else
``~/.local/share/warlord``. Everything in it is private to the player: it holds
their copy of the rules text.
"""

from __future__ import annotations

import os
from pathlib import Path

PACKAGE_DATA = Path(__file__).resolve().parent / "data"
LRR_REFS = PACKAGE_DATA / "lrr_refs.json"


def data_dir(override: str | os.PathLike | None = None) -> Path:
    if override:
        return Path(override).expanduser()
    if os.environ.get("WARLORD_DATA"):
        return Path(os.environ["WARLORD_DATA"]).expanduser()
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "warlord"


def kb_dir(root: Path) -> Path:
    return root / "kb"


def games_dir(root: Path) -> Path:
    return root / "games"
