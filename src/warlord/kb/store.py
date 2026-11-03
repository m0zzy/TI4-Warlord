"""The knowledge base: load records, apply errata, filter by ruleset, search."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

from pydantic import TypeAdapter

from warlord.kb.records import (
    ComponentCard,
    Deck,
    ErrataPatch,
    Record,
    RuleChunk,
    Ruling,
    StrategyNote,
    Timing,
    authority_of,
)
from warlord.kb.search import BM25, Embedder, cosine, name_similarity, rrf
from warlord.ruleset import Ruleset

_ANY = TypeAdapter(RuleChunk | ComponentCard | Ruling | StrategyNote | ErrataPatch | Deck)


class DuplicateRecord(ValueError):
    pass


@dataclass
class Hit:
    record: Record
    score: float

    def to_dict(self, *, text_limit: int | None = 1200) -> dict:
        data = self.record.model_dump(exclude_none=True)
        for key in ("text", "answer"):
            if text_limit and isinstance(data.get(key), str) and len(data[key]) > text_limit:
                data[key] = data[key][:text_limit] + " …"
        data["authority_level"] = authority_of(self.record).name
        data["score"] = round(self.score, 4)
        return data


@dataclass
class KnowledgeBase:
    records: dict[str, Record] = field(default_factory=dict)
    errata: list[ErrataPatch] = field(default_factory=list)
    decks: dict[str, Deck] = field(default_factory=dict)
    embedder: Embedder | None = None

    _index_ids: list[str] = field(default_factory=list, init=False, repr=False)
    _bm25: BM25 | None = field(default=None, init=False, repr=False)
    _vectors: list[list[float]] | None = field(default=None, init=False, repr=False)
    _deck_sets: dict[str, frozenset[str]] = field(default_factory=dict, init=False, repr=False)
    _supersession: dict[str, list[str]] | None = field(default=None, init=False, repr=False)

    # ------------------------------------------------------------------ loading

    @classmethod
    def from_paths(cls, paths: Iterable[str | Path], embedder: Embedder | None = None) -> "KnowledgeBase":
        kb = cls(embedder=embedder)
        for p in paths:
            p = Path(p)
            files = sorted(p.rglob("*.jsonl")) if p.is_dir() else [p]
            for f in files:
                kb.add_many(_read_jsonl(f))
        kb.finalize()
        return kb

    def add(self, item: Record | ErrataPatch | Deck | dict) -> None:
        if isinstance(item, dict):
            item = _ANY.validate_python(item)
        if isinstance(item, ErrataPatch):
            self.errata.append(item)
            return
        if isinstance(item, Deck):
            if item.id in self.decks:
                raise DuplicateRecord(item.id)
            self.decks[item.id] = item
            self._deck_sets.pop(item.id, None)
            return
        if item.id in self.records:
            raise DuplicateRecord(item.id)
        self.records[item.id] = item
        self._bm25 = None
        self._supersession = None

    def add_many(self, items: Iterable[Record | ErrataPatch | Deck | dict]) -> None:
        for item in items:
            self.add(item)

    def finalize(self) -> None:
        """Apply errata (oldest first) and build the search index."""
        for patch in sorted(self.errata, key=lambda e: e.date):
            card = self.records.get(patch.target)
            if not isinstance(card, ComponentCard) or patch.id in card.errata:
                continue
            self.records[card.id] = apply_errata(card, patch)
        self._build_index()

    def _build_index(self) -> None:
        self._index_ids = list(self.records)
        self._bm25 = BM25([_search_text(self.records[i]) for i in self._index_ids])
        self._vectors = (
            self.embedder.embed([_search_text(self.records[i]) for i in self._index_ids])
            if self.embedder
            else None
        )

    # ------------------------------------------------------------------ queries

    def get(self, record_id: str) -> Record | None:
        return self.records.get(record_id)

    def __contains__(self, record_id: str) -> bool:
        return record_id in self.records

    def _allowed(self, record: Record, ruleset: Ruleset | None) -> bool:
        if ruleset is None:
            return True
        if not ruleset.allows(record.ruleset):
            return False
        if not self._in_content(record, ruleset):
            return False
        # Hide older printings when a newer one is part of this game's content.
        for newer in self._superseded_by().get(record.id, ()):
            rec = self.records.get(newer)
            if rec is not None and self._in_content(rec, ruleset):
                return False
        return True

    def _in_content(self, record: Record, ruleset: Ruleset) -> bool:
        """Tag and deck checks, without supersession."""
        if not ruleset.allows(record.ruleset):
            return False
        if isinstance(record, ComponentCard) and record.faction is None:
            loaded = [d for d in ruleset.deck_ids(record.type) if d in self.decks]
            if loaded:
                return any(record.id in self._deck_set(d) for d in loaded)
        return True

    def _superseded_by(self) -> dict[str, list[str]]:
        """older id -> newer ids, following chains (a -> b -> c gives a: [b, c])."""
        if self._supersession is None:
            direct: dict[str, list[str]] = {}
            for r in self.records.values():
                for old in getattr(r, "supersedes", []) or []:
                    direct.setdefault(old, []).append(r.id)
            full: dict[str, list[str]] = {}
            for old in direct:
                seen, stack = [], list(direct[old])
                while stack:
                    n = stack.pop()
                    if n not in seen:
                        seen.append(n)
                        stack.extend(direct.get(n, []))
                full[old] = seen
            self._supersession = full
        return self._supersession

    def _deck_set(self, deck_id: str) -> frozenset[str]:
        if deck_id not in self._deck_sets:
            self._deck_sets[deck_id] = frozenset(self.decks[deck_id].cards)
        return self._deck_sets[deck_id]

    def search(
        self,
        query: str,
        *,
        kinds: Sequence[type] | None = None,
        ruleset: Ruleset | None = None,
        k: int = 8,
    ) -> list[Hit]:
        if self._bm25 is None:
            self._build_index()
        assert self._bm25 is not None
        candidates = [
            i
            for i, rid in enumerate(self._index_ids)
            if self._allowed(self.records[rid], ruleset)
            and (kinds is None or isinstance(self.records[rid], tuple(kinds)))
        ]
        if not candidates:
            return []

        kw = self._bm25.scores(query)
        names = [
            max(
                [name_similarity(query, n) for n in _names(self.records[self._index_ids[i]])]
                + [0.0]
            )
            for i in range(len(self._index_ids))
        ]
        rankings = [
            [i for i in sorted(candidates, key=lambda i: -kw[i]) if kw[i] > 0],
            [i for i in sorted(candidates, key=lambda i: -names[i]) if names[i] >= 0.6],
        ]
        if self._vectors is not None and self.embedder is not None:
            qv = self.embedder.embed([query])[0]
            sims = {i: cosine(qv, self._vectors[i]) for i in candidates}
            rankings.append(sorted(candidates, key=lambda i: -sims[i]))

        fused = rrf(rankings)
        ranked = sorted(fused, key=lambda i: -fused[i])[:k]
        return [Hit(self.records[self._index_ids[i]], fused[i]) for i in ranked]

    def rules_lookup(self, question: str, ruleset: Ruleset | None = None, k: int = 6) -> dict:
        """Ranked rule chunks and rulings, highest authority first among ties.

        Rulings superseded by another returned ruling are dropped, and conflicts
        between a ruling and a higher-authority record are surfaced.
        """
        hits = self.search(question, kinds=(RuleChunk, Ruling), ruleset=ruleset, k=k * 2)
        superseded = {
            s for h in hits if isinstance(h.record, Ruling) for s in h.record.supersedes
        }
        hits = [h for h in hits if h.record.id not in superseded][:k]

        # Pull in parents of matched sub-rules: context often lives there.
        seen = {h.record.id for h in hits}
        for h in list(hits):
            parent = getattr(h.record, "parent", None)
            if parent and parent not in seen and parent in self.records:
                rec = self.records[parent]
                if self._allowed(rec, ruleset):
                    hits.append(Hit(rec, h.score * 0.5))
                    seen.add(parent)

        return {
            "results": [h.to_dict() for h in hits],
            "superseded_dropped": sorted(superseded),
        }

    def card_lookup(self, name_or_id: str, ruleset: Ruleset | None = None, k: int = 3) -> dict:
        exact = self.records.get(name_or_id)
        if isinstance(exact, ComponentCard) and self._allowed(exact, ruleset):
            return {"match": Hit(exact, 1.0).to_dict(text_limit=None), "alternatives": []}

        scored = sorted(
            (
                (max(name_similarity(name_or_id, n) for n in _names(r)), r)
                for r in self.records.values()
                if isinstance(r, ComponentCard) and self._allowed(r, ruleset)
            ),
            # Ties go to cards over map tiles: "Quann" the planet, not tile 25.
            key=lambda t: (-t[0], t[1].type == "tile"),
        )
        scored = [(s, r) for s, r in scored if s >= 0.55][:k]
        excluded = self._excluded_match(name_or_id, ruleset) if (not scored or scored[0][0] < 0.9) else None
        if not scored:
            result: dict = {"match": None, "alternatives": [], "note": f"No component matches {name_or_id!r}."}
        else:
            best_score, best = scored[0]
            alternatives = [
                {"id": r.id, "name": r.name, "type": r.type, "similarity": round(s, 3)}
                for s, r in scored[1:]
            ]
            result = {"match": Hit(best, best_score).to_dict(text_limit=None), "alternatives": alternatives}
            if best_score < 0.8:
                result["note"] = "Low-confidence name match; confirm the card with the player."
        if excluded is not None:
            result["not_in_this_game"] = {
                "id": excluded.id,
                "name": excluded.name,
                "ruleset": excluded.ruleset,
                "note": "This card exists but is not part of this game's content (expansion or deck).",
            }
        return result

    def _excluded_match(self, name: str, ruleset: Ruleset | None) -> ComponentCard | None:
        """A near-exact name match that the ruleset filtered out, if any."""
        if ruleset is None:
            return None
        best: tuple[float, ComponentCard] | None = None
        for r in self.records.values():
            if isinstance(r, ComponentCard) and not self._allowed(r, ruleset):
                sim = max(name_similarity(name, n) for n in _names(r))
                if sim >= 0.9 and (best is None or sim > best[0]):
                    best = (sim, r)
        return best[1] if best else None

    def strategy_lookup(
        self, query: str, faction: str | None = None, ruleset: Ruleset | None = None, k: int = 4
    ) -> list[dict]:
        hits = self.search(query, kinds=(StrategyNote,), ruleset=ruleset, k=k * 3)
        if faction:
            hits = [
                h
                for h in hits
                if not h.record.factions or faction in h.record.factions  # type: ignore[union-attr]
            ]
        return [h.to_dict() for h in hits[:k]]


# ---------------------------------------------------------------------- helpers


def apply_errata(card: ComponentCard, patch: ErrataPatch) -> ComponentCard:
    data = card.model_dump()
    if patch.field == "timing":
        if not isinstance(patch.new, dict):
            raise ValueError(f"errata {patch.id}: timing patch needs an object")
        data["timing"] = Timing.model_validate(patch.new).model_dump()
    else:
        if not isinstance(patch.new, str):
            raise ValueError(f"errata {patch.id}: {patch.field} patch needs a string")
        current = data[patch.field]
        if patch.old is not None:
            if patch.old not in current:
                raise ValueError(
                    f"errata {patch.id}: text to replace not found on {card.id}"
                )
            data[patch.field] = current.replace(patch.old, patch.new)
        else:
            data[patch.field] = patch.new
    data["errata"] = [*card.errata, patch.id]
    return ComponentCard.model_validate(data)


def _names(record: Record) -> list[str]:
    if isinstance(record, ComponentCard):
        return [record.name, *record.aliases, record.id.split(".", 1)[-1].replace("_", " ")]
    if isinstance(record, (RuleChunk, StrategyNote)):
        return [record.title]
    return []


def _search_text(record: Record) -> str:
    if isinstance(record, RuleChunk):
        return f"{record.title}\n{record.title}\n{record.text}"
    if isinstance(record, ComponentCard):
        timing = ""
        if record.timing:
            timing = f"{record.timing.window} {record.timing.trigger or ''}"
        return f"{record.name}\n{' '.join(record.aliases)}\n{record.type}\n{timing}\n{record.text}"
    if isinstance(record, Ruling):
        return f"{record.question}\n{record.answer}"
    return f"{record.title}\n{' '.join(record.factions)}\n{record.text}"


def _read_jsonl(path: Path) -> list[dict]:
    rows = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as e:
            raise ValueError(f"{path}:{n}: {e}") from e
    return rows
