"""Retrieval: BM25 over text plus fuzzy name matching, with an optional embedding hook.

Keyword scoring catches card and unit names exactly; an embedding backend (if
one is plugged in) catches paraphrased questions. Scores from the two are
blended with reciprocal-rank fusion so neither needs calibrating.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from difflib import SequenceMatcher
from typing import Protocol, Sequence

_TOKEN = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")
_STOP = frozenset(
    "a an and are as at be by can do does for from has have i if in into is it its "
    "my of on or that the their then there they this to was what when where which "
    "who will with you your".split()
)


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP]


def normalize_name(name: str) -> str:
    return " ".join(_TOKEN.findall(name.lower()))


class Embedder(Protocol):
    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class BM25:
    def __init__(self, docs: Sequence[str], k1: float = 1.4, b: float = 0.75):
        self.k1, self.b = k1, b
        self.docs = [Counter(tokenize(d)) for d in docs]
        self.lengths = [sum(d.values()) for d in self.docs]
        self.avg = (sum(self.lengths) / len(self.lengths)) if self.lengths else 0.0
        df: Counter[str] = Counter()
        for d in self.docs:
            df.update(d.keys())
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def scores(self, query: str) -> list[float]:
        q = tokenize(query)
        out = []
        for doc, length in zip(self.docs, self.lengths):
            s = 0.0
            for t in q:
                tf = doc.get(t, 0)
                if not tf:
                    continue
                denom = tf + self.k1 * (1 - self.b + self.b * length / (self.avg or 1))
                s += self.idf.get(t, 0.0) * tf * (self.k1 + 1) / denom
            out.append(s)
        return out


def name_similarity(query: str, name: str) -> float:
    """0..1 similarity tolerant of misspellings and partial names."""
    q, n = normalize_name(query), normalize_name(name)
    if not q or not n:
        return 0.0
    if q == n:
        return 1.0
    ratio = SequenceMatcher(None, q, n).ratio()
    if q in n or n in q:
        ratio = max(ratio, 0.85 * min(len(q), len(n)) / max(len(q), len(n)) + 0.15)
    return ratio


def rrf(rankings: Sequence[Sequence[int]], k: int = 60) -> dict[int, float]:
    fused: dict[int, float] = {}
    for ranking in rankings:
        for rank, idx in enumerate(ranking):
            fused[idx] = fused.get(idx, 0.0) + 1.0 / (k + rank + 1)
    return fused


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0
