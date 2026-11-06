"""Import official rules PDFs into RuleChunk records.

Two layouts are supported:

* ``lrr``: the Living Rules Reference. Numbered topics ("82 STRATEGIC
  ACTION"), numbered sub-rules ("82.2 After each player ..."), lettered
  clarifications under a sub-rule, and "RELATED TOPICS:" lines. Each topic and
  each sub-rule becomes a chunk: ``lrr.82`` and ``lrr.82.2``.
* ``rulebook``: an expansion rulebook such as Thunder's Edge's, which has
  unnumbered ALL-CAPS section headings. Each section becomes a chunk:
  ``te.<heading>``.

The LRR is set in two columns, and plain text extraction interleaves them. So
text fragments are placed by position: left column, then right column, top
to bottom within each. Sub-rules carry their topic number, so a sub-rule that
lands out of order is still filed under the right topic.

The output holds Fantasy Flight Games' text. Write it under ``data/private/``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from warlord.kb.records import RuleChunk

_TOPIC = re.compile(r"^(\d{1,3})\s+([A-Z][A-Z0-9 ,’'\-—–()/&:.]*[A-Z)])$")
_SUB = re.compile(r"^(\d{1,3}) ?\. ?(\d{1,2})\s*(.*)$")  # "77. 2" appears with kerning gaps
_LETTER = re.compile(r"^([a-z])\s+(\S.*)$")
_RELATED = re.compile(r"^RELATED TOPICS:\s*(.*)$")
_PAGE_NUMBER = re.compile(r"^\d{1,3}$")


def _slug(text: str) -> str:
    text = text.lower().replace("&", " and ").replace("’", "").replace("'", "")
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


def _title(caps: str) -> str:
    small = {"and", "of", "the", "a", "to", "in", "on"}
    words = caps.lower().split()
    titled = " ".join(w if (i and w in small) else w[:1].upper() + w[1:] for i, w in enumerate(words))
    # Capitalize after dashes too: "Step 1—move Ships" -> "Step 1—Move Ships".
    return re.sub(r"([—–-])([a-z])", lambda m: m.group(1) + m.group(2).upper(), titled)


def _is_caps_heading(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    return len(letters) >= 3 and all(c.isupper() for c in letters)


# --------------------------------------------------------------- extraction


def extract_lines(
    pdf_path: str | Path,
    pages: Iterable[int] | None = None,
    columns: int = 2,
    min_font_size: float = 7.0,
) -> list[str]:
    """Lines of text in reading order: column by column, top to bottom.

    Uses pdfminer's layout analysis for the lines themselves (it keeps bold and
    regular runs of one line together), then orders the lines by column, since
    the LRR's two columns are not reliably ordered otherwise.

    Lines set smaller than ``min_font_size`` are dropped: in the LRR those are
    the example cards printed inside the rules. The exception is the small
    "a", "b", ... markers of lettered clarifications and the small sub-rule
    numbers that sometimes sit on a line of their own; those are reattached
    to the line they label.
    """
    import statistics

    from pdfminer.high_level import extract_pages  # optional: pip install 'ti4-warlord[pdf]'
    from pdfminer.layout import LAParams, LTChar, LTTextLine

    logging.getLogger("pdfminer").setLevel(logging.ERROR)
    wanted = None if pages is None else set(pages)
    out: list[str] = []

    def text_lines(node):
        for child in node:
            if isinstance(child, LTTextLine):
                yield child
            elif hasattr(child, "__iter__"):
                try:
                    yield from text_lines(child)
                except TypeError:
                    pass

    for index, layout in enumerate(extract_pages(str(pdf_path), laparams=LAParams())):
        if wanted is not None and index not in wanted:
            continue
        width = float(layout.width)
        rows: list[list] = []  # [col, top, x0, text]
        markers: list[tuple[int, float, float, str]] = []
        for line in text_lines(layout):
            text = re.sub(r"\s+", " ", line.get_text()).strip()
            if not text:
                continue
            col = min(columns - 1, int(line.x0 // (width / columns))) if columns > 1 else 0
            sizes = [c.size for c in line if isinstance(c, LTChar)]
            size = statistics.median(sizes) if sizes else min_font_size
            if re.fullmatch(r"[a-h]|\d{1,3} ?\. ?\d{1,2}", text):
                markers.append((col, line.y1, line.x0, text))
            elif size >= min_font_size:
                rows.append([col, -line.y1, line.x0, text])
        for col, top, x0, letter in markers:
            candidates = [r for r in rows if r[0] == col and r[2] > x0]
            if candidates:
                target = min(candidates, key=lambda r: abs(-r[1] - top))
                if abs(-target[1] - top) < 12:
                    target[3] = f"{letter} {target[3]}"
        rows.sort(key=lambda r: (r[0], r[1], r[2]))
        out.extend(r[3] for r in rows)
    return out


# ------------------------------------------------------------------- LRR


@dataclass
class _Draft:
    id: str
    title: str
    lines: list[str] = field(default_factory=list)
    parent: str | None = None
    related_titles: list[str] = field(default_factory=list)


def parse_lrr(lines: Iterable[str], ruleset: str = "pok", prefix: str = "lrr") -> list[RuleChunk]:
    topics: dict[str, _Draft] = {}
    subs: dict[str, _Draft] = {}
    current: _Draft | None = None
    in_related: _Draft | None = None
    started = False

    for raw in lines:
        line = raw.strip()
        if not line or _PAGE_NUMBER.match(line):
            continue
        if started and line in ("INDEX", "QUICK REFERENCE"):
            break

        m = _TOPIC.match(line)
        if m and _is_caps_heading(m.group(2)):
            num = m.group(1)
            if not started and num != "1":
                continue
            started = True
            tid = f"{prefix}.{num}"
            current = topics.setdefault(tid, _Draft(tid, _title(m.group(2))))
            in_related = None
            continue
        if not started:
            continue

        m = _SUB.match(line)
        if m:
            topic_num, sub_num, rest = m.groups()
            sid = f"{prefix}.{topic_num}.{sub_num}"
            parent = f"{prefix}.{topic_num}"
            heading = rest.split(":", 1)[0] if rest else ""
            title = _title(heading) if heading and _is_caps_heading(heading) and len(heading) < 60 else None
            current = subs.setdefault(sid, _Draft(sid, title or "", parent=parent))
            if rest:
                current.lines.append(rest)
            in_related = None
            continue

        m = _RELATED.match(line)
        if m and current is not None:
            owner = topics.get(current.parent or current.id) or current
            owner.related_titles.append(m.group(1))
            in_related = owner
            continue
        if in_related is not None:
            in_related.related_titles.append(line)
            continue

        m = _LETTER.match(line)
        if m and current is not None:
            current.lines.append(f"{m.group(1)}. {m.group(2)}")
            continue
        if current is not None:
            current.lines.append(line)

    by_title = {_slug(t.title): t.id for t in topics.values()}
    chunks: list[RuleChunk] = []
    for t in sorted(topics.values(), key=lambda d: int(d.id.split(".")[1])):
        related = []
        for name in " ".join(t.related_titles).split(","):  # wrapped lines continue a name
            rid = by_title.get(_slug(name.strip()))
            if rid and rid != t.id and rid not in related:
                related.append(rid)
        chunks.append(
            RuleChunk(id=t.id, ruleset=ruleset, title=t.title, text=_join(t.lines), related=related)  # type: ignore[arg-type]
        )
    for s in sorted(subs.values(), key=lambda d: tuple(int(x) for x in d.id.split(".")[1:])):
        parent = topics.get(s.parent or "")
        title = s.title or (parent.title if parent else s.id)
        if s.title and parent:
            title = f"{parent.title}: {s.title}"
        chunks.append(
            RuleChunk(id=s.id, ruleset=ruleset, title=title, text=_join(s.lines), parent=s.parent)  # type: ignore[arg-type]
        )
    return chunks


def _join(lines: list[str]) -> str:
    text = ""
    for line in lines:
        if not text:
            text = line
        elif re.match(r"^[a-z]\. ", line) or line.startswith("•"):
            text += "\n" + line
        elif text.endswith("-") and not text.endswith(" -"):
            text = text[:-1] + line  # re-join a hyphenated line break
        else:
            text += " " + line
    return text.strip()


# --------------------------------------------------------------- rulebooks


def parse_rulebook(
    lines: Iterable[str], ruleset: str = "thunders_edge", prefix: str = "te", min_body_words: int = 12
) -> list[RuleChunk]:
    """Split an unnumbered rulebook into sections at ALL-CAPS headings.

    Short "sections" (captions, labels on diagrams) are merged into the
    section before them rather than kept as rules of their own.
    """
    sections: list[_Draft] = []
    current: _Draft | None = None
    for raw in lines:
        line = raw.strip()
        if not line or _PAGE_NUMBER.match(line):
            continue
        if _is_caps_heading(line) and len(line) <= 60 and not line.endswith((".", ",")):
            if current is not None and current.lines and current.title:
                pass
            if current is not None and not current.lines:
                current.title = f"{current.title} {line}".strip()  # multi-line heading
                continue
            current = _Draft("", line)
            sections.append(current)
            continue
        if current is not None:
            current.lines.append(line)

    merged: list[_Draft] = []
    for s in sections:
        body_words = len(" ".join(s.lines).split())
        if merged and body_words < min_body_words:
            merged[-1].lines.extend([s.title, *s.lines])
        else:
            merged.append(s)

    chunks, seen = [], set()
    for s in merged:
        base = f"{prefix}.{_slug(s.title)}"
        cid, n = base, 2
        while cid in seen:
            cid, n = f"{base}_{n}", n + 1
        seen.add(cid)
        chunks.append(RuleChunk(id=cid, ruleset=ruleset, title=_title(s.title), text=_join(s.lines)))  # type: ignore[arg-type]
    return chunks


def import_pdf(
    pdf_path: str | Path, kind: str = "lrr", ruleset: str | None = None, pages: Iterable[int] | None = None
) -> list[RuleChunk]:
    if kind == "lrr":
        return parse_lrr(extract_lines(pdf_path, pages, columns=2), ruleset=ruleset or "pok")
    if kind == "rulebook":
        return parse_rulebook(extract_lines(pdf_path, pages, columns=2), ruleset=ruleset or "thunders_edge")
    raise ValueError(f"unknown kind {kind!r}")
