"""``warlord setup``: build a player's knowledge base from their own files.

The player brings:

* the Living Rules Reference PDF (``--lrr``),
* the Thunder's Edge rulebook PDF (``--te-rulebook``, optional),
* AsyncTI4 component data (``--asyncti4``): a checkout of the bot repository,
  or its ``src/main/resources/data`` folder. ``--download-asyncti4`` fetches
  only the folders needed, with a sparse git clone.

Setup runs the importers into ``<data>/kb`` and then checks the result, so a
wrong or partial file shows up now instead of as bad rulings later.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from warlord.engine.validate import load_ref_overrides
from warlord.kb.importers.asyncti4 import import_checkout, write_jsonl
from warlord.kb.store import KnowledgeBase
from warlord.paths import LRR_REFS, games_dir, kb_dir

ASYNCTI4_REPO = "https://github.com/AsyncTI4/TI4_map_generator_bot"
ASYNCTI4_FOLDERS = ["src/main/resources/data", "src/main/resources/systems", "src/main/resources/planets"]

LRR_TOPICS = 101  # LRR 2.0
TE_RULEBOOK_PAGES = 16
TE_RULES_PAGES = [3, 5, 6, 7, 8, 9, 10, 11, 12, 15]  # zero-based: skips fiction, components, maps


class SetupError(RuntimeError):
    pass


@dataclass
class SetupReport:
    data: str
    written: dict[str, int] = field(default_factory=dict)
    checks: list[dict] = field(default_factory=list)

    def check(self, name: str, ok: bool, detail: str) -> None:
        self.checks.append({"check": name, "ok": ok, "detail": detail})

    @property
    def ok(self) -> bool:
        return all(c["ok"] for c in self.checks)

    def to_dict(self) -> dict:
        return {"data": self.data, "ok": self.ok, "written": self.written, "checks": self.checks}


def resolve_asyncti4(path: str | Path) -> Path:
    """Accept a repo checkout or its data folder; return the data folder."""
    p = Path(path).expanduser()
    for candidate in (p / "src" / "main" / "resources" / "data", p):
        if (candidate / "action_cards").is_dir():
            return candidate
    raise SetupError(f"{p} is not an AsyncTI4 checkout or data folder (no action_cards/ inside).")


def download_asyncti4(dest: Path) -> Path:
    """Sparse, shallow clone of just the component folders (a few MB)."""
    if shutil.which("git") is None:
        raise SetupError("git is not installed; clone the AsyncTI4 repository yourself and pass --asyncti4.")
    if not (dest / ".git").exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "clone", "--quiet", "--depth", "1", "--filter=blob:none", "--sparse", ASYNCTI4_REPO, str(dest)],
            check=True,
        )
    subprocess.run(["git", "-C", str(dest), "sparse-checkout", "set", *ASYNCTI4_FOLDERS], check=True)
    return dest / "src" / "main" / "resources" / "data"


def run_setup(
    data: Path,
    lrr: str | Path | None = None,
    te_rulebook: str | Path | None = None,
    asyncti4: str | Path | None = None,
    download: bool = False,
) -> SetupReport:
    kb_path = kb_dir(data)
    kb_path.mkdir(parents=True, exist_ok=True)
    games_dir(data).mkdir(parents=True, exist_ok=True)
    report = SetupReport(data=str(data))

    # Components.
    if asyncti4 is None and download:
        asyncti4 = download_asyncti4(data / "raw" / "asyncti4")
    if asyncti4 is not None:
        result = import_checkout(resolve_asyncti4(asyncti4))
        write_jsonl([*result.records, *result.decks], kb_path / "components.asyncti4.jsonl")
        report.written["components"] = len(result.records)
        report.written["decks"] = len(result.decks)

    # Rules.
    if lrr is not None or te_rulebook is not None:
        from warlord.kb.importers.rules_pdf import import_pdf  # needs the pdf extra

        if lrr is not None:
            chunks = import_pdf(_must_exist(lrr), kind="lrr", ruleset="pok")
            write_jsonl(chunks, kb_path / "lrr.jsonl")  # type: ignore[arg-type]
            report.written["lrr_chunks"] = len(chunks)
        if te_rulebook is not None:
            pages = TE_RULES_PAGES if _page_count(te_rulebook) == TE_RULEBOOK_PAGES else None
            chunks = import_pdf(_must_exist(te_rulebook), kind="rulebook", ruleset="thunders_edge", pages=pages)
            write_jsonl(chunks, kb_path / "te_rules.jsonl")  # type: ignore[arg-type]
            report.written["te_rule_chunks"] = len(chunks)
            if pages is None:
                report.check(
                    "te_rulebook_layout",
                    False,
                    "Unexpected page count, so every page was imported; check te_rules.jsonl for map and component noise.",
                )

    verify(kb_path, report)
    return report


def verify(kb_path: Path, report: SetupReport) -> None:
    files = sorted(kb_path.glob("*.jsonl"))
    if not files:
        report.check("knowledge_base", False, "No knowledge base files yet: pass --lrr and --asyncti4.")
        return
    kb = KnowledgeBase.from_paths(files)
    rules = [r for r in kb.records.values() if getattr(r, "kind", "") == "rule"]
    lrr_topics = [r for r in rules if r.id.startswith("lrr.") and r.id.count(".") == 1]
    components = [r for r in kb.records.values() if getattr(r, "kind", "") == "component"]

    report.check(
        "lrr_topics",
        len(lrr_topics) == LRR_TOPICS,
        f"{len(lrr_topics)} LRR topics (expected {LRR_TOPICS} for LRR 2.0)",
    )
    report.check("te_rules", any(r.id.startswith("te.") for r in rules), "Thunder's Edge rulebook sections present")
    report.check("components", len(components) > 500, f"{len(components)} components")
    unresolved = sum(len(d.unresolved) for d in kb.decks.values())
    report.check("decks", bool(kb.decks), f"{len(kb.decks)} decks, {unresolved} deck entries without a card")
    pinned = load_ref_overrides(LRR_REFS)
    missing = sorted({r for refs in pinned.values() for r in refs if r not in kb})
    report.check(
        "engine_rule_refs",
        not missing,
        "every engine rule reference is in the LRR" if not missing else f"missing: {', '.join(missing)}",
    )


def _must_exist(path: str | Path) -> Path:
    p = Path(path).expanduser()
    if not p.exists():
        raise SetupError(f"File not found: {p}")
    return p


def _page_count(pdf: str | Path) -> int | None:
    try:
        from pdfminer.pdfpage import PDFPage

        with open(_must_exist(pdf), "rb") as fh:
            return sum(1 for _ in PDFPage.get_pages(fh))
    except (ImportError, OSError):
        return None


def summary_json(report: SetupReport) -> str:
    return json.dumps(report.to_dict(), indent=2)
