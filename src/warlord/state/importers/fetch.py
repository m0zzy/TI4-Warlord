"""Fetch games from AsyncTI4's public API (no login).

* ``fetch_live(name)``: one live game's public view (``web-data``).
* ``fetch_completed(n)``: completed games from the export, for the datasets.
  It is capped at ``MAX_EXPORT_GAMES`` in total, per the agreement with the
  AsyncTI4 maintainers. A manifest in the output folder records which games
  were pulled, and the cap counts them across runs.

Only the imported GameState files are written. The raw export (which holds
Discord ids, usernames and notepads) is never saved to disk.
"""

from __future__ import annotations

import io
import json
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from warlord.kb.store import KnowledgeBase
from warlord.state.importers.asyncti4 import ImportResult, from_save, from_web_data
from warlord.state.store import GameStore

API = "https://bot.asyncti4.com/api/public"
MAX_EXPORT_GAMES = 50
USER_AGENT = "ti4-warlord/0.1 (rules referee research; public API)"
MANIFEST = "_asyncti4_export_manifest.json"  # "_" keeps it out of GameStore.list_games


class CapReached(RuntimeError):
    pass


def _get(url: str, timeout: float = 60.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed https host
        return resp.read()


def fetch_live(game_name: str, kb: KnowledgeBase | None = None) -> ImportResult:
    name = urllib.parse.quote(game_name, safe="")
    data = json.loads(_get(f"{API}/game/{name}/web-data"))
    return from_web_data(data, kb)


def _manifest(out_dir: Path) -> dict:
    p = out_dir / MANIFEST
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"games": []}


def fetch_completed(
    n: int,
    store: GameStore,
    kb: KnowledgeBase | None = None,
    page: int = 0,
    getter=_get,
) -> list[ImportResult]:
    """Import up to ``n`` completed games, never more than MAX_EXPORT_GAMES in total."""
    out_dir = store.root
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = _manifest(out_dir)
    pulled = set(manifest["games"])
    room = MAX_EXPORT_GAMES - len(pulled)
    if room <= 0:
        raise CapReached(f"Already imported {len(pulled)} games; the cap is {MAX_EXPORT_GAMES}.")
    n = max(1, min(n, room))

    raw = getter(f"{API}/games/events?page={int(page)}&limit={n}")
    results: list[ImportResult] = []
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        names = sorted({Path(m).stem for m in z.namelist() if m.endswith(".txt")})
        for game in names:
            if game in pulled or len(pulled) >= MAX_EXPORT_GAMES:
                continue
            save = z.read(f"{game}.txt").decode("utf-8", errors="replace")
            events = json.loads(z.read(f"{game}.json")) if f"{game}.json" in z.namelist() else []
            result = from_save(save, events, kb)
            store.save(result.state)
            pulled.add(game)
            results.append(result)
    manifest["games"] = sorted(pulled)
    (out_dir / MANIFEST).write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return results
