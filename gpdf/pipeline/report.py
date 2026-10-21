# SPDX-License-Identifier: AGPL-3.0-or-later
"""JSON files of the split step (schema 1): the decisions file the owner reviews, and the
report of a dry or real run. Page numbers in both are 1-based, as shown to the user."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

SCHEMA = 1
SPLIT, KEEP = "split", "keep"
UNREVIEWED = "flagged and not reviewed"   # PageResult.left_whole of such pages


class DecisionsError(ValueError):
    """The decisions file is malformed or does not fit the source."""


@dataclass
class Detected:
    is_spread: bool
    cut: list[float] | None            # [top, bottom] as fractions of the displayed width
    confidence: float
    flagged: bool
    reasons: list[str] = field(default_factory=list)


@dataclass
class PageDecision:
    page: int                          # 1-based
    detected: Detected
    action: str                        # SPLIT or KEEP
    cut: list[float] | None            # overrides the detector when reviewed
    reviewed: bool


@dataclass
class Decisions:
    source: str
    source_size: int
    source_mtime_ns: int
    pages: list[PageDecision]
    schema: int = SCHEMA

    def to_dict(self) -> dict:
        d = asdict(self)
        return {"schema": d.pop("schema"), **d}

    def save(self, path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=1, ensure_ascii=False) + "\n",
                              encoding="utf-8")

    @classmethod
    def load(cls, path) -> "Decisions":
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise DecisionsError(f"cannot read decisions file {path}: {e}") from None
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw) -> "Decisions":
        if not isinstance(raw, dict) or raw.get("schema") != SCHEMA:
            raise DecisionsError(f"not a schema {SCHEMA} decisions file")
        problems = []
        pages = []
        for k, e in enumerate(raw.get("pages") or []):
            where = f"entry {k + 1}"
            try:
                if e["page"] != k + 1:
                    problems.append(f"{where}: page {e['page']}, expected {k + 1}")
                if e["action"] not in (SPLIT, KEEP):
                    problems.append(f"page {e['page']}: action must be \"split\" or \"keep\"")
                if not isinstance(e["reviewed"], bool):
                    problems.append(f"page {e['page']}: reviewed must be true or false")
                cut = e.get("cut")
                if cut is not None and not _valid_cut(cut):
                    problems.append(f"page {e['page']}: cut must be null or [top, bottom] "
                                    "fractions between 0 and 1")
                det = e["detected"]
                pages.append(PageDecision(
                    e["page"], Detected(bool(det["is_spread"]), det.get("cut"),
                                        float(det["confidence"]), bool(det["flagged"]),
                                        list(det.get("reasons") or [])),
                    e["action"], [float(v) for v in cut] if _valid_cut(cut) else None,
                    e["reviewed"]))
            except (KeyError, TypeError, ValueError) as err:
                problems.append(f"{where}: malformed ({err})")
        if not pages:
            problems.append("no pages")
        if problems:
            more = f" (and {len(problems) - 5} more)" if len(problems) > 5 else ""
            raise DecisionsError("bad decisions file: " + "; ".join(problems[:5]) + more)
        try:
            return cls(str(raw["source"]), int(raw["source_size"]), int(raw["source_mtime_ns"]),
                       pages)
        except (KeyError, TypeError, ValueError) as err:
            raise DecisionsError(f"bad decisions file: missing source stamp ({err})") from None


def _valid_cut(cut) -> bool:
    return (isinstance(cut, list) and len(cut) == 2
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) and 0 < v < 1
                    for v in cut))


@dataclass
class PageResult:
    page: int                          # 1-based source page
    action: str                        # what was done: SPLIT or KEEP
    out_pages: list[int]               # 1-based output pages (empty in a dry run)
    cut: list[float] | None = None
    confidence: float = 1.0
    flagged: bool = False
    reviewed: bool = True
    reasons: list[str] = field(default_factory=list)    # detector reasons
    left_whole: str = ""               # why a flagged or spread page was kept whole
    encoding: str = ""                 # g4 / jpeg / flate for split pages
    whitened_px: int = 0
    needs_ocr: bool = False            # a text layer was dropped
    links_dropped: int = 0
    annots_dropped: int = 0


@dataclass
class SplitReport:
    source: str
    output: str | None                 # None for a dry run
    pages_in: int
    pages_out: int
    pages: list[PageResult]
    dry_run: bool = False
    page_labels_dropped: bool = False
    links_retargeted: int = 0          # links on kept pages that pointed at a split page
    seconds: float = 0.0
    schema: int = SCHEMA

    @property
    def spreads(self) -> list[int]:
        return [p.page for p in self.pages if p.action == SPLIT]

    @property
    def flagged(self) -> list[PageResult]:
        return [p for p in self.pages if p.flagged]

    @property
    def unreviewed(self) -> list[PageResult]:
        """Pages left whole because they were flagged and not reviewed."""
        return [p for p in self.pages if p.left_whole == UNREVIEWED]

    @property
    def unsplittable(self) -> list[PageResult]:
        return [p for p in self.pages if p.left_whole and p.left_whole != UNREVIEWED]

    @property
    def needs_ocr(self) -> list[int]:
        return [p.page for p in self.pages if p.needs_ocr]

    @property
    def exit_code(self) -> int:
        """0 ok; 2 finished, but flagged pages were left whole unreviewed, or spreads could
        not be split."""
        return 2 if any(p.left_whole for p in self.pages) else 0

    def to_dict(self) -> dict:
        d = asdict(self)
        d = {"schema": d.pop("schema"), **d}
        d["summary"] = {
            "spreads_split": len(self.spreads),
            "singles": self.pages_in - len(self.spreads),
            "flagged": [p.page for p in self.flagged],
            "unreviewed_left_whole": [p.page for p in self.unreviewed],
            "unsplittable_left_whole": [p.page for p in self.unsplittable],
            "needs_ocr": self.needs_ocr,
            "links_dropped": sum(p.links_dropped for p in self.pages),
            "annots_dropped": sum(p.annots_dropped for p in self.pages),
            "whitened_px": sum(p.whitened_px for p in self.pages),
        }
        return d

    def save(self, path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=1, ensure_ascii=False) + "\n",
                              encoding="utf-8")

