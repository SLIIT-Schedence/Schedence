"""Owner-verified database inputs, detached before a worker is accepted."""

import asyncio
from dataclasses import dataclass
import json

from schedence_teaching.api.errors import ApiError
from schedence_teaching.persistence.contracts import SnapshotDocument, SourceDocument
from schedence_teaching.persistence.database_read import load_database_evidence
from schedence_teaching.persistence.generation_inputs import build_database_generation, evidence_source
from schedence_teaching.persistence.pp1 import capture_pp1_snapshot
from schedence_teaching.persistence.snapshots import canonical_json, content_digest

from .contracts import GenerationRequest


@dataclass(frozen=True)
class GenerationWork:
    request: GenerationRequest
    document: SnapshotDocument
    # Trusted detached owner rows only, never credentials or a live DB session.
    evidence: object | None = None


def check_references(document, request):
    sources = {row.label: row for row in document.sources}
    if (document.institution_id != request.institution_id or
            document.academic_term_reference != request.academic_term_reference or
            document.calendar_label != request.calendar_label):
        raise ApiError(400, "INPUT_REFERENCE_MISMATCH", "The selected period or calendar does not match the snapshot.")
    for label, reference in (("PP1:calendar-configuration", request.calendar_reference),
                             ("PP1:synthetic-elective-registration-evidence", request.scheduling_policy_reference)):
        source = sources.get(label)
        if source is None or source.sha256 != reference or content_digest(source.provenance) != reference:
            raise ApiError(400, "INPUT_REFERENCE_MISMATCH", "Select the snapshot's exact calendar and scheduling policy references.")


def scoped_document(evidence, loaded):
    """A scoped result needs its own exact demand snapshot, never the full one."""
    original = evidence.document
    seal = None
    if original.solver_config.resolved_policy is not None:
        from schedence_teaching.persistence.policy import rebind_scoped_snapshot
        seal = lambda document: rebind_scoped_snapshot(document, original)  # P3.2: same approved revision.
    generated_labels = {"PP1:calendar-configuration", "PP1:synthetic-elective-registration-evidence",
                        "PP1:preparation-source-bindings"}
    document = capture_pp1_snapshot(loaded.enrolled, institution_id=original.institution_id,
        sources=tuple(row for row in original.sources if row.label not in generated_labels),
        blocked_scopes=loaded.blocked_scopes, academic_term_reference=original.academic_term_reference,
        timezone_name=original.timezone_name,
        registration_policy=evidence_source(original, "PP1:synthetic-elective-registration-evidence")["policy"],
        # Sealed parents: defer the seal until the derived sources are canonical, so the seal (and its recorded
        # parent digest) is identical in the accepting process and in the spawned worker, whatever the hash seed.
        seal=(lambda document: document) if seal is not None else None)
    # model_dump(mode="json") represents sets as arrays in hash-seed order.
    # Canonicalize the typed configuration before hashing NEW derived evidence;
    # preserve every historical source document and its original checksum.
    normalized_sources = []
    for source in document.sources:
        if source.label == "PP1:calendar-configuration":
            provenance = dict(source.provenance,
                configuration=json.loads(canonical_json(loaded.enrolled.batch.configuration)))
        elif source.label == "PP1:preparation-source-bindings":
            provenance = dict(source.provenance, metadata=dict(source.provenance["metadata"],
                solver_config=json.loads(canonical_json(loaded.enrolled.batch.config))))
        else:
            normalized_sources.append(source)
            continue
        normalized_sources.append(SourceDocument.model_validate(source.model_dump() | dict(
            provenance=provenance, sha256=content_digest(provenance))))
    # Preserve authoritative owner links; these do not issue replacement master IDs.
    document = SnapshotDocument.model_validate(document.model_dump() | dict(sources=normalized_sources,
        external_references=original.external_references))
    return seal(document) if seal is not None else document  # The seal replays and validates the bound document.


def prepare_database_work(evidence, request):
    check_references(evidence.document, request)
    loaded = build_database_generation(evidence, request.database_request())
    document = scoped_document(evidence, loaded)
    if request.scheduling_rules is not None:
        from schedence_teaching.persistence.generation_inputs import academic_source
        from .scheduling_rules import RulesError, apply_rules
        metadata = evidence_source(evidence.document, "PP1:preparation-source-bindings")["metadata"]
        academic = academic_source(evidence, metadata)
        try:
            document = apply_rules(document, request.scheduling_rules, academic)
        except RulesError as error:
            raise ApiError(400, "INVALID_SCHEDULING_RULES", str(error)) from None
    selection = dict(parent_snapshot_id=str(evidence.snapshot_id), parent_snapshot_sha256=content_digest(evidence.document),
        request_sha256=content_digest(request), calendar_reference=request.calendar_reference,
        scheduling_policy_reference=request.scheduling_policy_reference,
        original_source_references={row.label: row.sha256 for row in evidence.document.sources})
    document = SnapshotDocument.model_validate(document.model_dump() | dict(sources=(*document.sources,
        SourceDocument(label="API:parent-snapshot-selection", owner="TEACHING_SERVICE", provenance_kind="SERVICE_EXPORT",
            sha256=content_digest(selection), provenance=selection))))
    return GenerationWork(request, document, evidence)


class DatabaseGenerationInputs:
    def __init__(self, engines, target):
        self.engines, self.target = engines, target

    async def prepare(self, request):
        evidence = await load_database_evidence(self.engines, request.institution_id, request.snapshot_id, self.target)
        return await asyncio.to_thread(prepare_database_work, evidence, request)
