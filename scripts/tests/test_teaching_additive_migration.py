"""Guards for the narrow additive migration; no database connections."""
import copy
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from migrate_teaching_forms_neon import CURRENT, NEW_TABLES, compile_migration, view_contracts
from teaching_migration_eligibility import form_eligibility
from schedence_teaching.forms.contracts import FormTemplate
from schedence_teaching.forms.presets import teaching_preferences


def form_row():
    identifier = "525a43e1-98a3-47f4-a5de-d661fac4065e"
    actor = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    document = FormTemplate(id=UUID(identifier), institution_id=UUID(CURRENT), title="Preferences",
        description="", preset_id="lecturer-teaching-preferences.v1", questions=teaching_preferences().questions,
        created_by=actor, updated_by=actor, created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))
    return dict(id=identifier, institution_id=CURRENT, revision=1, document=document.model_dump(mode="json"))


def test_unreviewed_new_form_excluded_even_if_otherwise_valid():
    row = form_row()
    accepted, excluded = form_eligibility([("reviewed", row, "rowhash"), ("new", row, "extra")], ["reviewed"])
    assert len(accepted) == len(excluded) == 1
    assert excluded[0]["reason"] == "OUTSIDE_REVIEWED_335_CANDIDATES"


def test_missing_reviewed_identity_stops_migration():
    with pytest.raises(ValueError, match="Reviewed candidate missing"):
        form_eligibility([], ["reviewed"])


def test_wrong_tenant_stops_migration():
    row = form_row()
    row["institution_id"] = "44dc54c1-35df-4e53-8fc0-95a7d911368b"
    with pytest.raises(ValueError, match="Wrong institution"):
        form_eligibility([("reviewed", row, "hash")], ["reviewed"])


def test_changed_locked_scheduler_schema_stops_migration():
    row = form_row()
    row["document"]["questions"][0]["required"] = False
    with pytest.raises(ValueError, match="Locked questions"):
        form_eligibility([("reviewed", row, "hash")], ["reviewed"])


def test_form_revision_envelope_must_match_document():
    row = form_row()
    row["revision"] = 2
    with pytest.raises(ValueError, match="envelope"):
        form_eligibility([("reviewed", row, "hash")], ["reviewed"])


def test_migration_scope_has_no_experimental_or_private_shared_contracts():
    mappings = [dict(academic_id="956bed92-6cc5-4b16-9e22-58c1a92b66ac", module_code="IT2100", exam_code="IT2100", decision="REVIEWED")]
    views = view_contracts(mappings)
    assert "module_source_records" not in views
    for value in views.values():
        assert "schedence.institution_id" in value["query"] and CURRENT in value["query"]
        assert not any(text in value["query"] for text in ("form_template", "lecturer_preferences", "identity.users", "pp1_", "email"))
    assert "956bed92-6cc5-4b16-9e22-58c1a92b66ac" in views["modules"]["query"]
    assert "45ca97cb-bf9b-4b01-8ec5-77808a8aeed9" not in views["modules"]["query"]


def test_additive_artifact_changes_if_payload_changes_and_never_overwrites():
    row = form_row()
    body, signature = compile_migration({}, [row])
    changed = copy.deepcopy(row)
    changed["document"]["title"] = "Different"
    assert compile_migration({}, [changed])[1] != signature
    for command in ("DELETE FROM", "TRUNCATE", "CREATE OR REPLACE", "UPDATE academic.", "UPDATE examinations.", "ALTER TABLE academic.", "ALTER TABLE examinations.", "DISABLE ROW LEVEL SECURITY"):
        assert command not in body
    assert "IF NOT coalesce" in body
    assert "Unrecognized existing objects" in body
    assert "Migration artifact mismatch" in body
    assert set(NEW_TABLES) == {"form_template_drafts", "form_distributions", "form_distribution_recipients"}
