"""Safe reports contain structural paths and source locations, never input values."""

from collections import Counter
from dataclasses import dataclass

from .contracts import InputEvidence, ReadinessStatus, SchedulingSubmission


@dataclass(frozen=True)
class ReadinessDiagnostic:
    status: ReadinessStatus
    code: str
    path: tuple
    message: str
    workbook: str | None = None
    worksheet: str | None = None
    row: int | None = None
    column: str | None = None

    def __post_init__(self):
        object.__setattr__(self, "status", ReadinessStatus(self.status))


def covering_evidence(evidence: list[InputEvidence], path: tuple):
    matches = [item for item in evidence if path[:len(item.path)] == item.path]
    if not matches:
        return []
    longest = max(len(item.path) for item in matches)
    return [item for item in matches if len(item.path) == longest]


def diagnostic(submission: SchedulingSubmission, status, code, path, message):
    path = tuple(path)
    sources = covering_evidence(submission.evidence, path)
    source = sources[0] if len(sources) == 1 else None
    location = source.location if source else None
    column = source.columns.get(str(path[-1])) if source and path else None
    return ReadinessDiagnostic(ReadinessStatus(status), code, path, message,
        location.workbook if location else None, location.worksheet if location else None,
        location.row if location else None, column)


@dataclass(frozen=True)
class ReadinessReport:
    mode: str | None
    diagnostics: tuple[ReadinessDiagnostic, ...]

    @property
    def ready_for_generation(self):
        return not any(item.status != ReadinessStatus.CONFIRMED for item in self.diagnostics)

    @property
    def ready_for_actual_campus(self):
        return self.ready_for_generation and self.mode == "ACTUAL_CAMPUS"

    def to_dict(self):
        return dict(mode=self.mode, ready_for_generation=self.ready_for_generation,
            ready_for_actual_campus=self.ready_for_actual_campus, university_ready=False,
            status_counts=dict(Counter(item.status.value for item in self.diagnostics)),
            diagnostics=[{**vars(item), "status": item.status.value, "path": list(item.path)}
                         for item in self.diagnostics])
