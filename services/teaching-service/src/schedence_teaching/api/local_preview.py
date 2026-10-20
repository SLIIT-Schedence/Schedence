"""Read-only LOCAL DEVELOPMENT preview store; never a production data source.

Enabled only by TEACHING_READ_STORE=LOCAL_PREVIEW outside production. It serves saved, gate-validated local
generation results through the existing authorized read API, so Cognito verification, Identity grants,
institution scoping and the published-only rule for lecturers and students all apply unchanged.

Each entry is re-checked when loaded with the existing publication gate (prepare_attempt) and assembled with
the same checks as stored versions (exact coverage, delivery digest, policy provenance). Results are never
solved, written or published here, every version stays an unpublished experimental draft, and no filesystem
path reaches a response. Entries come only from a server-side manifest inside the Teaching local-data folder.
"""

import json
import pickle
import threading
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from pydantic import Field

from schedence_teaching.persistence.policy import verify_snapshot_policy, version_policy_metadata
from schedence_teaching.persistence.ports import PersistenceConflict
from schedence_teaching.persistence.retrieval import assemble_timetable
from schedence_teaching.persistence.snapshots import content_digest, prepare_attempt
from schedence_teaching.persistence.versioning import METADATA_KEY, delivery_digest
from schedence_teaching.schemas.models import InputContract, RequiredText

PREVIEW_LABEL = "Experimental — Provisional Policy — Not Published"
# Fixed namespace: identifiers are stable across restarts and never collide with database UUID4 values.
NAMESPACE = uuid.UUID("5d3f6c1e-8a4b-4f0e-9c2a-7b1d0e6f4a93")
RESULT_FILES = ("input-snapshot.json", "solver-result.json", "trusted-result.pickle")


class PreviewEntry(InputContract):
    key: RequiredText = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    # A run folder relative to the Teaching local-data folder, written by the local generation tooling.
    directory: RequiredText


class PreviewManifest(InputContract):
    entries: tuple[PreviewEntry, ...] = Field(min_length=1, max_length=20)


def local_data_root():
    return (Path(__file__).resolve().parents[3] / "local-data").resolve()


def _inside(path, root):
    path = path.resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError("local preview files must stay inside the Teaching local-data folder")
    return path


def load_preview_record(directory, key, version_number):
    """One saved run as a stored-version record, after the existing publication gate passes again."""
    from schedence_teaching.persistence.contracts import SnapshotDocument
    document = SnapshotDocument.model_validate_json((directory / "input-snapshot.json").read_text(encoding="utf-8"))
    verify_snapshot_policy(document)
    with (directory / "trusted-result.pickle").open("rb") as handle:
        result = pickle.load(handle)  # Service-created local worker output from a manifest-listed run only.
    saved = json.loads((directory / "solver-result.json").read_text(encoding="utf-8"))
    if content_digest(result.to_dict()) != content_digest(saved):
        raise PersistenceConflict("local preview result differs from its saved solver report")
    attempt = prepare_attempt(document, result)
    if not attempt.success:
        raise PersistenceConflict("local preview result does not pass independent validation")
    rows = json.loads(attempt.deliveries_json)
    metadata = dict(logical_status="VALIDATED", normalized_deliveries_sha256=delivery_digest(rows),
                    snapshot_sha256=content_digest(document), **version_policy_metadata(document))
    ident = lambda name: uuid.uuid5(NAMESPACE, f"{key}:{name}")
    run = dict(solver_success=True, solver_status=result.status.value, runtime_seconds=result.runtime_seconds,
               expected_occurrences=attempt.expected_occurrences, scheduled_occurrences=attempt.scheduled_occurrences,
               result_metadata=json.loads(attempt.result_json) | {METADATA_KEY: metadata}, snapshot_id=ident("snapshot"))
    report = dict(passed=True, hard_violations=0, expected_occurrences=attempt.expected_occurrences,
                  checked_occurrences=attempt.scheduled_occurrences, evidence={METADATA_KEY: json.loads(json.dumps(metadata))})
    version = dict(id=ident("version"), series_id=ident("series"), version_number=version_number,
                   lifecycle_status="DRAFT_VALIDATED", solver_success=True, validation_passed=True,
                   academic_complete=False, preview_label=PREVIEW_LABEL)
    deliveries, coverage = [], []
    for row in rows:
        identifier = uuid.uuid5(NAMESPACE, f"{key}:delivery:{row['delivery_reference']}")
        coverage.extend(dict(item, delivery_id=identifier) for item in row.pop("coverage"))
        deliveries.append(dict(row, id=identifier))
    return assemble_timetable(document, version, run, report, tuple(deliveries), tuple(coverage))


def _matches(record, series_id, years, semesters, streams, specializations, groups, lecturer, venue, lifecycle_status):
    """The database repository's filter semantics: scope rows, then optional lecturer/venue bookings."""
    if series_id is not None and record.version["series_id"] != series_id:
        return False
    if lifecycle_status and record.logical_status != lifecycle_status:
        return False
    scopes = [scope for scope in record.document.scopes if all(not wanted or value in wanted for wanted, value in (
        (years, scope.academic_year), (semesters, scope.semester), (streams, scope.stream.value),
        (specializations, scope.specialization)))]
    if groups:
        scopes = [scope for scope in scopes if set(scope.group_references) & set(groups)]
    if not scopes:
        return False
    if lecturer is not None and not any(row["staff_reference"] == lecturer for row in record.deliveries):
        return False
    return venue is None or any(row["venue_reference"] == venue for row in record.deliveries)


class _Repository:
    def __init__(self, records, institution_id):
        self.records = tuple(row for row in records if row.document.institution_id == institution_id)

    async def get_timetable(self, version_id):
        record = next((row for row in self.records if row.version["id"] == version_id), None)
        if record is None:
            raise PersistenceConflict("timetable version not found in this institution")
        return record

    async def find_timetables(self, *, offset=0, limit=20, series_id=None, **filters):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("bounded pagination is required")
        selected = [row for row in self.records if _matches(row, series_id, **filters)]
        return tuple(selected[offset:offset + limit]), len(selected)


class LocalPreviewTimetableStore:
    """Validates the manifest's runs once in the background, then serves them read-only per institution.

    Re-running the publication gate takes a while, so the first reads get an explicit "still loading" error
    instead of a timeout; no unvalidated or partial set is ever served.
    """

    def __init__(self, manifest_path, *, root=None):
        self.root = (root or local_data_root()).resolve()
        self.manifest_path = _inside(Path(manifest_path), self.root)
        self._records, self._error, self._thread = None, None, None
        self._lock = threading.Lock()

    def load(self):
        """Synchronous validation of every manifest entry (also used by the background warm-up)."""
        manifest = PreviewManifest.model_validate_json(self.manifest_path.read_text(encoding="utf-8"))
        if len({row.key for row in manifest.entries}) != len(manifest.entries):
            raise ValueError("local preview keys must be unique")
        records = []
        for entry in manifest.entries:
            directory = _inside(self.root / entry.directory, self.root)
            if not all((directory / name).is_file() for name in RESULT_FILES):
                raise ValueError(f"local preview {entry.key} is missing its saved result files")
            records.append(load_preview_record(directory, entry.key, 1))
        return tuple(records)

    def _warm(self):
        try:
            records = self.load()
        except Exception as error:  # Reported generically; details stay in server logs, never in responses.
            self._error = error
        else:
            self._records = records

    def start(self):
        with self._lock:
            if self._thread is None:
                self._thread = threading.Thread(target=self._warm, name="teaching-local-preview", daemon=True)
                self._thread.start()

    @asynccontextmanager
    async def read(self, institution_id):
        from .errors import ApiError
        if self._records is None:
            self.start()
            if self._error is not None:
                raise ApiError(503, "PREVIEW_UNAVAILABLE", "The local timetable preview could not be validated.")
            raise ApiError(503, "PREVIEW_LOADING", "The local timetable preview is still being validated. Try again shortly.")
        yield _Repository(self._records, institution_id)
