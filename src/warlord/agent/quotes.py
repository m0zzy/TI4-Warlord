"""Detect long verbatim quotes of source text in an answer.

The tool is shared, and LRR and card text are Fantasy Flight Games'
copyright, so answers paraphrase and cite instead of reproducing the text. A
quote counts as too long when more than ``max_words`` consecutive words of
the answer match a source record word for word (case and punctuation ignored).
"""

from __future__ import annotations

import re
from typing import Iterable

MAX_QUOTE_WORDS = 20

_WORD = re.compile(r"[a-z0-9]+(?:'[a-z0-9]+)?")


def _words(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def long_quotes(answer: str, sources: Iterable[str], max_words: int = MAX_QUOTE_WORDS) -> list[str]:
    """Spans of ``answer`` (as normalized words) longer than ``max_words`` that appear verbatim in a source."""
    n = max_words + 1
    grams: set[tuple[str, ...]] = set()
    for src in sources:
        w = _words(src)
        grams.update(tuple(w[i : i + n]) for i in range(len(w) - n + 1))
    if not grams:
        return []

    words = _words(answer)
    hits = [i for i in range(len(words) - n + 1) if tuple(words[i : i + n]) in grams]

    spans: list[str] = []
    start = prev = None
    for i in hits:
        if start is None:
            start = prev = i
        elif i == prev + 1:
            prev = i
        else:
            spans.append(" ".join(words[start : prev + n]))
            start = prev = i
    if start is not None:
        spans.append(" ".join(words[start : prev + n]))
    return spans


TRIM_MARK = "[long quote removed; see the cited source]"


def trim_quotes(text: str, spans: Iterable[str]) -> tuple[str, list[str]]:
    """Replace each over-long verbatim span in ``text`` with a short marker.

    Returns the new text and the spans that were found and removed.
    """
    removed = []
    for span in spans:
        words = span.split()
        pattern = r"(?<![A-Za-z0-9])" + r"[\W_]+".join(re.escape(w) for w in words) + r"(?![A-Za-z0-9])"
        text, count = re.subn(pattern, TRIM_MARK, text, flags=re.IGNORECASE)
        if count:
            removed.append(span)
    return text, removed
