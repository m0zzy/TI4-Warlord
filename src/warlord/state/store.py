"""Game storage. One omniscient JSON file per game; reads return a perspective view."""

from __future__ import annotations

from pathlib import Path

from warlord.state.perspective import project
from warlord.state.schema import GameState, PerspectiveView


class GameNotFound(KeyError):
    pass


class GameStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def _path(self, game_id: str) -> Path:
        if not game_id or "/" in game_id or "\\" in game_id or game_id.startswith("."):
            raise GameNotFound(game_id)
        return self.root / f"{game_id}.json"

    def load(self, game_id: str) -> GameState:
        path = self._path(game_id)
        if not path.exists():
            raise GameNotFound(game_id)
        return GameState.model_validate_json(path.read_text(encoding="utf-8"))

    def save(self, state: GameState) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path(state.meta.game_id)
        path.write_text(state.model_dump_json(indent=2), encoding="utf-8")
        return path

    def view(self, game_id: str, viewer: str) -> PerspectiveView:
        return project(self.load(game_id), viewer)

    def list_games(self) -> list[str]:
        if not self.root.exists():
            return []
        return sorted(p.stem for p in self.root.glob("*.json"))
