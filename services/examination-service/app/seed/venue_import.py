"""Import and clean the campus venue list supplied by the team.

Reads one read-only, tab-separated text file with one venue per line::

    code <TAB> name <TAB> type <TAB> full capacity <TAB> building <TAB> floor

Writes reviewed files to ``data/`` (committed with the code; the raw file is not):

* ``venue_catalog.csv``           one clean row per venue code
* ``venue_cleaning_report.md``    every correction, removed duplicate and rejected row

Rules (nothing is invented and nothing is merged by guessing):

* Venue codes and names are kept exactly as supplied (case, ``+`` and spaces).
* Only clear formatting problems are corrected, and each one is logged:
  surrounding whitespace, and a missing name column (the code is then used as
  the name, as for most rows).
* An exact duplicate line is dropped and logged.
* Two different records with the same code are a conflict: neither is
  imported and the run fails, so the conflict is resolved by a person.
* Exam capacity is NOT stored here; it is derived from the full capacity by
  the generator (see ``SeedAssumptions.exam_seating_ratio``).

Usage (from services/examination-service)::

    python -m app.seed.venue_import --source PATH\\to\\venue.txt
"""

from __future__ import annotations

import argparse
import csv
import math
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from app.seed.assumptions import DEFAULT_ASSUMPTIONS

DATA_DIR = Path(__file__).resolve().parents[2] / "data"

# Source label -> stored venue type.
TYPE_MAP = {
    "lecture hall": "lecture_hall",
    "smart classroom": "smart_classroom",
    "computer lab": "computer_lab",
    "byod lab": "byod_lab",
}
CATALOG_FIELDS = ("venue_id", "venue_name", "venue_type", "building", "floor", "full_capacity", "source_line")


@dataclass(frozen=True)
class SourceVenue:
    source_line: int
    venue_id: str
    venue_name: str
    venue_type: str
    building: str
    floor: int
    full_capacity: int

    @property
    def values(self) -> tuple:
        """Everything except the line number: equal values = exact duplicate."""
        return (self.venue_id, self.venue_name, self.venue_type, self.building, self.floor, self.full_capacity)


@dataclass(frozen=True)
class Correction:
    source_line: int
    venue_id: str
    field: str
    original_value: str
    corrected_value: str
    reason: str


@dataclass(frozen=True)
class Problem:
    source_line: int
    venue_id: str
    issue: str


@dataclass
class VenueResult:
    venues: list[SourceVenue] = field(default_factory=list)
    corrections: list[Correction] = field(default_factory=list)
    removed_duplicates: list[tuple[SourceVenue, SourceVenue]] = field(default_factory=list)  # (removed, kept)
    rejected: list[Problem] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.rejected


def _parse_line(number: int, line: str, corrections: list[Correction]) -> SourceVenue | Problem:
    raw = line.split("\t")
    fields = [value.strip() for value in raw]
    code = fields[0]
    for original, value in zip(raw, fields):
        if original != value:
            corrections.append(Correction(number, code, "whitespace", original, value, "surrounding whitespace removed"))

    if len(fields) == 6:
        code, name, type_label, capacity, building, floor = fields
    elif len(fields) == 5:
        code, type_label, capacity, building, floor = fields
        name = code
        corrections.append(Correction(number, code, "venue_name", "", code, "name column missing; code used as name"))
    else:
        return Problem(number, code, f"expected 6 tab-separated fields, found {len(fields)}")

    if not code or not name or not building:
        return Problem(number, code, "empty code, name or building")
    venue_type = TYPE_MAP.get(type_label.lower())
    if venue_type is None:
        return Problem(number, code, f"unknown venue type {type_label!r}")
    try:
        full_capacity, floor_number = int(capacity), int(floor)
    except ValueError:
        return Problem(number, code, f"capacity {capacity!r} or floor {floor!r} is not a whole number")
    if full_capacity <= 0:
        return Problem(number, code, f"capacity must be positive, got {full_capacity}")
    return SourceVenue(number, code, name, venue_type, building, floor_number, full_capacity)


def clean_venues(lines: Iterable[str]) -> VenueResult:
    result = VenueResult()
    parsed: list[SourceVenue] = []
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        record = _parse_line(number, line, result.corrections)
        if isinstance(record, Problem):
            result.rejected.append(record)
        else:
            parsed.append(record)

    by_code: dict[str, list[SourceVenue]] = defaultdict(list)
    for venue in parsed:
        by_code[venue.venue_id].append(venue)
    for code, records in by_code.items():
        first = records[0]
        if any(r.values != first.values for r in records[1:]):
            lines_ = ", ".join(str(r.source_line) for r in records)
            result.rejected.append(Problem(first.source_line, code, f"conflicting records for one code (lines {lines_})"))
            continue
        result.venues.append(first)
        result.removed_duplicates.extend((r, first) for r in records[1:])

    # Codes that differ only in letter case are probably one venue typed twice: never merge, ask.
    folded = Counter(code.casefold() for code in by_code)
    for code in by_code:
        if folded[code.casefold()] > 1:
            result.rejected.append(Problem(by_code[code][0].source_line, code, "code differs from another only by case"))
    names = Counter(v.venue_name for v in result.venues)
    for venue in result.venues:
        if names[venue.venue_name] > 1:
            result.rejected.append(Problem(venue.source_line, venue.venue_id, "venue name used by another code"))
    return result


def exam_capacity(full_capacity: int, ratio: float = DEFAULT_ASSUMPTIONS.exam_seating_ratio) -> int:
    return math.floor(full_capacity * ratio)


def write_outputs(result: VenueResult, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "venue_catalog.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CATALOG_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(
            {"venue_id": v.venue_id, "venue_name": v.venue_name, "venue_type": v.venue_type,
             "building": v.building, "floor": v.floor, "full_capacity": v.full_capacity,
             "source_line": v.source_line}
            for v in result.venues
        )
    (out_dir / "venue_cleaning_report.md").write_text(cleaning_report(result), encoding="utf-8")


def cleaning_report(result: VenueResult) -> str:
    """Markdown summary of one cleaning run (generated; do not edit by hand)."""
    totals: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    for v in result.venues:
        totals[v.venue_type][0] += 1
        totals[v.venue_type][1] += v.full_capacity
        totals[v.venue_type][2] += exam_capacity(v.full_capacity)
    ratio = DEFAULT_ASSUMPTIONS.exam_seating_ratio
    lines = [
        "# Venue catalogue cleaning report",
        "",
        "Generated by `python -m app.seed.venue_import`. Codes and names are kept exactly as supplied.",
        f"Exam capacity = floor(full capacity x {ratio}); it is derived, not stored in the CSV.",
        "",
        f"- Venues in catalogue (one per code): **{len(result.venues)}**",
        f"- Exact duplicate lines removed: **{len(result.removed_duplicates)}**",
        f"- Formatting corrections: **{len(result.corrections)}**",
        f"- Rejected rows: **{len(result.rejected)}**",
        "",
        "## Capacity by type",
        "",
        "| Type | Venues | Full capacity | Exam capacity |",
        "|---|---|---|---|",
        *(f"| {t} | {n} | {full} | {exam} |" for t, (n, full, exam) in sorted(totals.items())),
        "",
        "## Corrections",
        "",
        "| Line | Code | Field | Original | Corrected | Reason |",
        "|---|---|---|---|---|---|",
        *(f"| {c.source_line} | {c.venue_id} | {c.field} | {c.original_value!r} | {c.corrected_value} | {c.reason} |"
          for c in result.corrections),
        "",
        "## Removed exact duplicates",
        "",
        *(f"- line {removed.source_line} ({removed.venue_id}) = line {kept.source_line}"
          for removed, kept in result.removed_duplicates),
        *([] if result.removed_duplicates else ["- none"]),
        "",
        "## Rejected rows (need a person to decide)",
        "",
        *(f"- line {p.source_line} ({p.venue_id}): {p.issue}" for p in result.rejected),
        *([] if result.rejected else ["- none"]),
    ]
    return "\n".join(lines) + "\n"


def import_venues(source: Path, out_dir: Path = DATA_DIR) -> VenueResult:
    result = clean_venues(source.read_text(encoding="utf-8-sig").splitlines())
    if result.ok:  # never overwrite the catalogue with a partial import
        write_outputs(result, out_dir)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.seed.venue_import", description=__doc__.splitlines()[0])
    parser.add_argument("--source", type=Path, required=True, help="Tab-separated venue list (read only)")
    parser.add_argument("--out-dir", type=Path, default=DATA_DIR)
    args = parser.parse_args(argv)

    result = import_venues(args.source, args.out_dir)
    print(f"Venues:             {len(result.venues)}")
    print(f"Corrections:        {len(result.corrections)}")
    print(f"Removed duplicates: {len(result.removed_duplicates)}")
    for problem in result.rejected:
        print(f"REJECTED line {problem.source_line} ({problem.venue_id}): {problem.issue}")
    if not result.ok:
        print("Nothing written: fix the rejected rows first.")
        return 1
    print(f"Written to: {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
