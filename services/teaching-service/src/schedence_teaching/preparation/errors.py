"""Located validation errors for pure teaching-demand preparation."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PreparationIssue:
    code: str
    location: tuple[str | int, ...]
    message: str

    def to_dict(self) -> dict:
        return {"code": self.code, "location": list(self.location), "message": self.message}


class PreparationError(ValueError):
    """No sessions are returned when validation fails."""

    def __init__(self, issues: list[PreparationIssue]) -> None:
        self.issues = tuple(issues)
        super().__init__("; ".join(
            f"{'.'.join(map(str, issue.location)) or 'input'}: {issue.message}" for issue in issues
        ))

    def errors(self) -> list[dict]:
        return [issue.to_dict() for issue in self.issues]
