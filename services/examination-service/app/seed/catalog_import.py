"""Import and clean the real module catalogue for the synthetic dataset.

Reads two read-only spreadsheets supplied by the team:

* exam dataset  (sheet "Lecturer Exam Data"): module, year, semester,
  specializations, credits, exam type, duration, reading-time flag, difficulty
* Main Admin dataset (sheet "Program Mappings"): year/semester per module,
  taken from real timetables. Used only as *evidence*; it is never changed.

Writes reviewed files to ``data/`` (committed with the code):

* ``module_catalog.csv``      one clean row per module code
* ``catalog_corrections.csv`` every value that was changed, with the original
* ``removed_duplicates.csv``  every duplicate record that was dropped, and why
* ``module_review.csv``       records that still need a human decision
* ``cleaning_report.md``      short summary of all of the above
* ``module_review_decisions.csv`` (input) decisions for duplicate codes

Lecturer names are never read into the outputs. These are research-prototype
cleaning decisions, not an official curriculum verification.

Usage (from services/examination-service)::

    python -m app.seed.catalog_import --exam-file PATH --admin-file PATH
"""

from __future__ import annotations

import argparse
import csv
import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from app.seed.xlsx import read_workbook, sheet_records

EXAM_SHEET = "Lecturer Exam Data"
ADMIN_MAPPING_SHEET = "Program Mappings"
ADMIN_MODULE_SHEET = "Modules"
DATA_DIR = Path(__file__).resolve().parents[2] / "data"

EXAM_TYPE_MAP = {"written": "written", "computer-based": "computer_based", "hybrid": "hybrid"}
READING_TIME_MAP = {"yes": True, "no": False}

# Confirmed corrections (logged in catalog_corrections.csv with the original value).
NAME_CORRECTIONS = {
    "Data Sceince & Analytics": "Data Science & Analytics",
    "Software Quality Asuarance": "Software Quality Assurance",
    "DataBase Management Systems": "Database Management Systems",
    "Massive Data Processing and cloud Computing": "Massive Data Processing and Cloud Computing",
    "Wide area Networks": "Wide Area Networks",
}
# (wrong code, module name) -> correct code
CODE_CORRECTIONS = {("IT3060", "Information Retrieval and Web Analytics"): "IT3041"}

# Resolutions that rest on evidence rather than a prototype decision.
CERTAIN_RESOLUTIONS = {"single_record", "identical_records", "admin_confirmed"}

RECORD_FIELDS = (
    "source_row", "module_name", "academic_year", "semester", "specializations", "credits",
    "exam_type", "duration_minutes", "has_extra_reading_time", "difficulty",
)
CATALOG_FIELDS = ("module_code", *RECORD_FIELDS[1:], "resolution", "admin_check", "source_row")
CORRECTION_FIELDS = ("module_code", "source_row", "field", "original_value", "corrected_value", "reason")
REVIEW_FIELDS = ("module_code", "issue", *RECORD_FIELDS, "admin_evidence", "note")
REMOVED_FIELDS = (
    "module_code", *(f"removed_{f}" for f in RECORD_FIELDS),
    "kept_source_row", "kept_academic_year", "kept_semester",
    "basis", "uncertain", "admin_evidence", "note",
)
DECISION_FIELDS = ("module_code", "academic_year", "semester", "basis", "note")


@dataclass(frozen=True)
class SourceModule:
    source_row: int
    module_code: str
    module_name: str
    academic_year: int
    semester: int
    specializations: tuple[str, ...]
    credits: int
    exam_type: str
    duration_minutes: int
    has_extra_reading_time: bool
    difficulty: int

    @property
    def term(self) -> tuple[int, int]:
        return self.academic_year, self.semester


@dataclass(frozen=True)
class CatalogEntry:
    module: SourceModule
    resolution: str  # single_record | identical_records | admin_confirmed | <decision basis>
    admin_check: str  # match | differs | not_in_admin


@dataclass(frozen=True)
class Decision:
    term: tuple[int, int]  # (academic year, semester) of the record to keep
    basis: str  # e.g. team_decision, code_year_rule
    note: str = ""


@dataclass(frozen=True)
class Correction:
    module_code: str
    source_row: int
    field: str
    original_value: str
    corrected_value: str
    reason: str


@dataclass(frozen=True)
class ReviewItem:
    module_code: str
    issue: str
    module: SourceModule | None
    admin_evidence: str
    note: str
    source_row: int | None = None


@dataclass(frozen=True)
class RemovedDuplicate:
    removed: SourceModule
    kept: SourceModule
    basis: str
    admin_evidence: str
    note: str

    @property
    def uncertain(self) -> bool:
        return self.basis not in CERTAIN_RESOLUTIONS


@dataclass
class CatalogResult:
    entries: list[CatalogEntry] = field(default_factory=list)
    corrections: list[Correction] = field(default_factory=list)
    review: list[ReviewItem] = field(default_factory=list)
    removed: list[RemovedDuplicate] = field(default_factory=list)


@dataclass(frozen=True)
class AdminEvidence:
    terms: Mapping[str, frozenset[tuple[int, int]]]  # code -> {(year, semester)}
    names: Mapping[str, str]  # code -> module name

    def describe(self, code: str) -> str:
        terms = self.terms.get(code)
        if not terms:
            return "not in Main Admin dataset"
        return "Main Admin: " + ", ".join(f"Y{y} S{s}" for y, s in sorted(terms))


# --- Parsing -------------------------------------------------------------------


def _int(value: object, name: str) -> int:
    text = str(value).strip()
    for prefix in ("Semester ", "Year "):
        text = text.removeprefix(prefix)
    try:
        return int(float(text))
    except ValueError:
        raise ValueError(f"{name}={value!r} is not a number") from None


def parse_exam_row(raw: Mapping[str, object]) -> SourceModule:
    exam_type = EXAM_TYPE_MAP.get(str(raw["Exam_Type"]).strip().lower())
    if exam_type is None:
        raise ValueError(f"unknown Exam_Type {raw['Exam_Type']!r}")
    reading = READING_TIME_MAP.get(str(raw["Extra_Reading_Time"]).strip().lower())
    if reading is None:
        raise ValueError(f"unknown Extra_Reading_Time {raw['Extra_Reading_Time']!r}")
    specializations = tuple(sorted({s.strip().upper() for s in str(raw["Specialization"]).split(",") if s.strip()}))
    if not specializations:
        raise ValueError("no specialization")
    return SourceModule(
        source_row=int(raw["_row"]),
        module_code=str(raw["Module_Code"]).strip().upper(),
        module_name=" ".join(str(raw["Module_Name"]).split()),
        academic_year=_int(raw["Year"], "Year"),
        semester=_int(raw["Semester"], "Semester"),
        specializations=specializations,
        credits=_int(raw["Module_Credits"], "Module_Credits"),
        exam_type=exam_type,
        duration_minutes=_int(raw["Exam_Duration_Minutes"], "Exam_Duration_Minutes"),
        has_extra_reading_time=reading,
        difficulty=_int(raw["Difficulty_Level"], "Difficulty_Level"),
    )


def build_admin_evidence(mappings: Iterable[Mapping[str, object]], modules: Iterable[Mapping[str, object]]) -> AdminEvidence:
    terms: dict[str, set[tuple[int, int]]] = defaultdict(set)
    for row in mappings:
        code = str(row["Module Code"]).strip().upper()
        terms[code].add((_int(row["Related Year"], "Related Year"), _int(row["Semester"], "Semester")))
    names = {str(m["Module Code"]).strip().upper(): " ".join(str(m["Module Name"]).split()) for m in modules}
    return AdminEvidence({c: frozenset(t) for c, t in terms.items()}, names)


# --- Cleaning ------------------------------------------------------------------


def _apply_corrections(module: SourceModule, admin: AdminEvidence, out: list[Correction]) -> SourceModule:
    def fix(field_name: str, new_value: str, reason: str) -> None:
        nonlocal module
        old_value = getattr(module, field_name)
        out.append(Correction(module.module_code, module.source_row, field_name, old_value, new_value, reason))
        module = replace(module, **{field_name: new_value})

    new_code = CODE_CORRECTIONS.get((module.module_code, module.module_name))
    if new_code:
        reason = "confirmed correction (code reused for another module)"
        if new_code in admin.names:
            reason += f"; Main Admin dataset lists {new_code} = {admin.names[new_code]}"
        fix("module_code", new_code, reason)
    normalized = unicodedata.normalize("NFKC", module.module_name)
    if normalized != module.module_name:
        fix("module_name", normalized, "unicode normalization (e.g. 'fi' ligature)")
    if module.module_name in NAME_CORRECTIONS:
        fix("module_name", NAME_CORRECTIONS[module.module_name], "spelling correction")
    return module


def clean_catalog(
    exam_rows: Iterable[Mapping[str, object]],
    admin: AdminEvidence,
    decisions: Mapping[str, Decision] | None = None,
) -> CatalogResult:
    """Clean exam-dataset rows into exactly one entry per module code (pure function).

    Duplicate codes are never resolved by guessing. A duplicate is resolved
    only when exactly one candidate matches a recorded decision, or exactly one
    matches the Main Admin dataset's year/semester. Otherwise every candidate
    is reported for manual review and the module is left out of the catalogue.
    Every dropped record is logged in ``result.removed``.
    """
    decisions = decisions or {}
    result = CatalogResult()
    by_code: dict[str, list[SourceModule]] = defaultdict(list)

    for raw in exam_rows:
        try:
            module = parse_exam_row(raw)
        except (KeyError, ValueError) as exc:
            code = str(raw.get("Module_Code") or "?").strip()
            result.review.append(ReviewItem(code, "invalid_record", None, "", str(exc), int(raw["_row"])))
            continue
        module = _apply_corrections(module, admin, result.corrections)
        by_code[module.module_code].append(module)

    for code in sorted(by_code):
        candidates = by_code[code]
        admin_terms = admin.terms.get(code, frozenset())
        decision = decisions.get(code)
        note = ""

        if len({replace(c, source_row=0) for c in candidates}) == 1:
            chosen = candidates[0]
            resolution = "single_record" if len(candidates) == 1 else "identical_records"
        else:
            decided = [c for c in candidates if decision and c.term == decision.term]
            matched = [c for c in candidates if c.term in admin_terms]
            if len(decided) == 1:
                chosen, resolution, note = decided[0], decision.basis, decision.note
            elif decision is None and len(matched) == 1:
                chosen, resolution = matched[0], "admin_confirmed"
            else:
                note = "conflicting records; add a row to module_review_decisions.csv to choose one"
                if decision:
                    note = f"decision Y{decision.term[0]} S{decision.term[1]} matches no candidate"
                for c in candidates:
                    result.review.append(ReviewItem(code, "duplicate_unresolved", c, admin.describe(code), note))
                continue

        for other in candidates:
            if other is chosen:
                continue
            lost = sorted(set(other.specializations) - set(chosen.specializations))
            extra = f"specializations only in removed record: {', '.join(lost)}" if lost else ""
            result.removed.append(
                RemovedDuplicate(other, chosen, resolution, admin.describe(code), "; ".join(x for x in (note, extra) if x))
            )

        if not admin_terms:
            admin_check = "not_in_admin"
        elif chosen.term in admin_terms:
            admin_check = "match"
        else:
            admin_check = "differs"
            result.review.append(
                ReviewItem(code, "admin_year_semester_differs", chosen, admin.describe(code),
                           "kept from exam dataset; please confirm year/semester")
            )
        admin_name = admin.names.get(code)
        if admin_name and admin_name.lower() != chosen.module_name.lower():
            result.review.append(
                ReviewItem(code, "name_differs_from_admin", chosen, f"Main Admin name: {admin_name}",
                           "exam dataset name kept")
            )
        result.entries.append(CatalogEntry(chosen, resolution, admin_check))

    return result


# --- Files ------------------------------------------------------------------------


def read_decisions(path: Path) -> dict[str, Decision]:
    if not path.is_file():
        return {}
    with path.open(newline="", encoding="utf-8") as handle:
        return {
            row["module_code"].strip().upper(): Decision(
                (_int(row["academic_year"], "academic_year"), _int(row["semester"], "semester")),
                (row.get("basis") or "manual_decision").strip(),
                (row.get("note") or "").strip(),
            )
            for row in csv.DictReader(handle)
            if (row.get("module_code") or "").strip()
        }


def _record_columns(m: SourceModule | None, prefix: str = "") -> dict[str, object]:
    if m is None:
        return {}
    columns = {
        "source_row": m.source_row,
        "module_name": m.module_name,
        "academic_year": m.academic_year,
        "semester": m.semester,
        "specializations": ";".join(m.specializations),
        "credits": m.credits,
        "exam_type": m.exam_type,
        "duration_minutes": m.duration_minutes,
        "has_extra_reading_time": "yes" if m.has_extra_reading_time else "no",
        "difficulty": m.difficulty,
    }
    return {f"{prefix}{k}": v for k, v in columns.items()}


def _write_csv(path: Path, fields: Sequence[str], rows: Iterable[Mapping[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def write_outputs(result: CatalogResult, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(
        out_dir / "module_catalog.csv",
        CATALOG_FIELDS,
        ({"module_code": e.module.module_code, **_record_columns(e.module), "resolution": e.resolution,
          "admin_check": e.admin_check} for e in result.entries),
    )
    _write_csv(out_dir / "catalog_corrections.csv", CORRECTION_FIELDS, (vars(c) for c in result.corrections))
    _write_csv(
        out_dir / "removed_duplicates.csv",
        REMOVED_FIELDS,
        ({"module_code": r.kept.module_code, **_record_columns(r.removed, "removed_"),
          "kept_source_row": r.kept.source_row, "kept_academic_year": r.kept.academic_year,
          "kept_semester": r.kept.semester, "basis": r.basis, "uncertain": "yes" if r.uncertain else "no",
          "admin_evidence": r.admin_evidence, "note": r.note} for r in result.removed),
    )
    _write_csv(
        out_dir / "module_review.csv",
        REVIEW_FIELDS,
        ({"module_code": r.module_code, "issue": r.issue, **_record_columns(r.module),
          **({"source_row": r.source_row} if r.module is None else {}),
          "admin_evidence": r.admin_evidence, "note": r.note} for r in result.review),
    )
    (out_dir / "cleaning_report.md").write_text(cleaning_report(result), encoding="utf-8")
    decisions = out_dir / "module_review_decisions.csv"
    if not decisions.exists():
        _write_csv(decisions, DECISION_FIELDS, [])


def cleaning_report(result: CatalogResult) -> str:
    """Markdown summary of one cleaning run (generated; do not edit by hand)."""
    issues: dict[str, set[str]] = defaultdict(set)
    for item in result.review:
        issues[item.issue].add(item.module_code)
    resolutions: dict[str, int] = defaultdict(int)
    for entry in result.entries:
        resolutions[entry.resolution] += 1
    lines = [
        "# Module catalogue cleaning report",
        "",
        "Generated by `python -m app.seed.catalog_import`. Research-prototype data cleaning,",
        "not an official university curriculum verification.",
        "",
        f"- Modules in catalogue (one per code): **{len(result.entries)}**",
        f"- Duplicate records removed: **{len(result.removed)}** "
        f"({sum(r.uncertain for r in result.removed)} marked uncertain)",
        f"- Value corrections: **{len(result.corrections)}**",
        "",
        "## How each module was resolved",
        "",
        "| Resolution | Modules |",
        "|---|---|",
        *(f"| {name} | {count} |" for name, count in sorted(resolutions.items())),
        "",
        "## Corrections",
        "",
        "| Code | Row | Field | Original | Corrected | Reason |",
        "|---|---|---|---|---|---|",
        *(f"| {c.module_code} | {c.source_row} | {c.field} | {c.original_value} | {c.corrected_value} | {c.reason} |"
          for c in result.corrections),
        "",
        "## Removed duplicate records",
        "",
        "| Code | Removed (row, year/sem) | Kept (row, year/sem) | Basis | Uncertain | Note |",
        "|---|---|---|---|---|---|",
        *(f"| {r.kept.module_code} | {r.removed.source_row}, Y{r.removed.academic_year} S{r.removed.semester} "
          f"| {r.kept.source_row}, Y{r.kept.academic_year} S{r.kept.semester} | {r.basis} "
          f"| {'yes' if r.uncertain else 'no'} | {r.note} |" for r in result.removed),
        "",
        "## Still needs review",
        "",
        *(f"- {issue}: {len(codes)} module(s): {', '.join(sorted(codes))}" for issue, codes in sorted(issues.items())),
    ]
    if not issues:
        lines.append("- nothing")
    return "\n".join(lines) + "\n"


def import_catalog(exam_file: Path, admin_file: Path, out_dir: Path = DATA_DIR) -> CatalogResult:
    exam_rows = sheet_records(read_workbook(exam_file)[EXAM_SHEET])
    admin_book = read_workbook(admin_file)
    admin = build_admin_evidence(
        sheet_records(admin_book[ADMIN_MAPPING_SHEET]), sheet_records(admin_book[ADMIN_MODULE_SHEET])
    )
    result = clean_catalog(exam_rows, admin, read_decisions(out_dir / "module_review_decisions.csv"))
    write_outputs(result, out_dir)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.seed.catalog_import", description=__doc__.splitlines()[0])
    parser.add_argument("--exam-file", type=Path, required=True, help="Exam dataset .xlsx (read only)")
    parser.add_argument("--admin-file", type=Path, required=True, help="Main Admin dataset .xlsx (read only)")
    parser.add_argument("--out-dir", type=Path, default=DATA_DIR)
    args = parser.parse_args(argv)

    result = import_catalog(args.exam_file, args.admin_file, args.out_dir)
    issues: dict[str, set[str]] = defaultdict(set)
    for item in result.review:
        issues[item.issue].add(item.module_code)
    print(f"Catalogue modules:  {len(result.entries)}")
    print(f"Corrections:        {len(result.corrections)}")
    print(f"Removed duplicates: {len(result.removed)} ({sum(r.uncertain for r in result.removed)} uncertain)")
    for issue, codes in sorted(issues.items()):
        print(f"Review - {issue}: {len(codes)} module(s)")
    print(f"Written to: {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
