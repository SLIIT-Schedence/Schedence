"""Explicit database-backed workflow; no environment loading or API wiring."""

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from uuid import UUID

from sqlalchemy import text

from .contracts import SnapshotDocument
from .models import Base
from .ports import PersistenceConflict
from .repositories import teaching_unit_of_work
from .snapshots import content_digest, replay_snapshot


def verify_source_checksums(directory: Path, expected: dict[str, str]) -> dict[str, str]:
    """Compare against a previously reviewed manifest, never self-approve drift."""
    directory = Path(directory).resolve()
    if not expected:
        raise ValueError("a previously reviewed source checksum manifest is required")
    actual = {}
    for name, checksum in expected.items():
        if not isinstance(name, str) or Path(name).name != name or name in {".", ".."}:
            raise ValueError("source manifest must use plain filenames")
        path = (directory / name).resolve()
        if path.parent != directory or not path.is_file():
            raise ValueError("a required source file is missing or outside the source directory")
        actual[name] = sha256(path.read_bytes()).hexdigest()
        if actual[name] != checksum:
            raise ValueError("a source checksum differs from the reviewed manifest")
    return actual


async def verify_runtime_security(session) -> None:
    """Fail before writes when a connection can bypass institution RLS."""
    role = (await session.execute(text("""SELECT rolsuper, rolbypassrls,
        EXISTS(SELECT 1 FROM pg_roles elevated WHERE (elevated.rolsuper OR elevated.rolbypassrls)
            AND pg_has_role(current_user,elevated.oid,'MEMBER')) AS can_assume_privileged_role
        FROM pg_roles WHERE rolname=current_user"""))).mappings().one()
    if role["rolsuper"] or role["rolbypassrls"] or role["can_assume_privileged_role"]:
        raise PersistenceConflict("Teaching runtime must not use a superuser or BYPASSRLS role")
    rows = (await session.execute(text("""SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,
        has_table_privilege(current_user,c.oid,'SELECT') AS can_select,
        has_table_privilege(current_user,c.oid,'INSERT') AS can_insert,
        pg_has_role(current_user,c.relowner,'MEMBER') AS owns_history
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='teaching' AND c.relkind IN ('r','p')"""))).mappings().all()
    owned = {table.name for table in Base.metadata.tables.values()}
    found = {row["relname"]: row for row in rows if row["relname"] in owned}
    if set(found) != owned or any(row["owns_history"] or not all(row[field] for field in
        ("relrowsecurity", "relforcerowsecurity", "can_select", "can_insert")) for row in found.values()):
        raise PersistenceConflict("Teaching runtime requires all owned tables, forced RLS and scoped read/insert privileges")


@dataclass(frozen=True)
class SnapshotImportResult:
    snapshot_ids: tuple[UUID, ...]
    expected_occurrences: tuple[int, ...]
    source_files_checked: int
    readback_equal: bool


async def import_validated_snapshots(factory, institution_id: UUID, documents: tuple[SnapshotDocument, ...], *,
                                     source_directory: Path, expected_checksums: dict[str, str]) -> SnapshotImportResult:
    """One atomic import with repeatable identity and source/read-back verification.

    Recovery protection and correct target branch are operational prerequisites;
    this function never creates roles, executes migrations or changes settings.
    """
    if not documents:
        raise ValueError("at least one validated input snapshot is required")
    verify_source_checksums(source_directory, expected_checksums)
    detached = tuple(replay_snapshot(document) for document in documents)
    if any(document.institution_id != institution_id for document, *_ in detached):
        raise PersistenceConflict("all imported snapshots must belong to the selected institution")
    source_pairs = {(name, checksum) for name, checksum in expected_checksums.items()}
    for document, *_ in detached:
        document_pairs = {(source.label, source.sha256) for source in document.sources}
        if not source_pairs <= document_pairs:
            raise ValueError("snapshot sources must retain every reviewed source checksum")
    identifiers = []
    counts = []
    async with teaching_unit_of_work(factory, institution_id) as repository:
        await verify_runtime_security(repository._session)
        for document, prepared, *_ in detached:
            identifier = await repository.store_snapshot(document)
            restored = await repository.get_snapshot(identifier)
            if content_digest(restored) != content_digest(document):
                raise PersistenceConflict("database read-back differs from validated imported inputs")
            identifiers.append(identifier)
            counts.append(len(prepared.sessions))
        # A source edit during import invalidates the whole transaction.
        verify_source_checksums(source_directory, expected_checksums)
    return SnapshotImportResult(tuple(identifiers), tuple(counts), len(expected_checksums), True)


async def load_generation_inputs(factory, institution_id: UUID, snapshot_id: UUID):
    """Return native inputs after closing the database transaction; no solver call."""
    async with teaching_unit_of_work(factory, institution_id) as repository:
        await verify_runtime_security(repository._session)
        document = await repository.get_snapshot(snapshot_id)
    return replay_snapshot(document)
