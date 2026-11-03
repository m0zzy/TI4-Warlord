from pathlib import Path

import pytest

from warlord.kb.store import KnowledgeBase
from warlord.state.store import GameStore

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "data" / "sample"


@pytest.fixture(scope="session")
def kb() -> KnowledgeBase:
    return KnowledgeBase.from_paths([SAMPLE / "kb"])


@pytest.fixture()
def store() -> GameStore:
    return GameStore(SAMPLE / "games")


@pytest.fixture()
def state(store):
    return store.load("pbd00000")


@pytest.fixture()
def view(store):
    return store.view("pbd00000", "p3")
