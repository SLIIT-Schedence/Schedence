"""Read-only comparison and generated migration contract safety checks."""
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"scripts"))
import teaching_shared_plan as shared
from compare_academic_records import compare_records
from plan_existing_neon import comparison


def test_existing_matching_id_reused_without_new_record():
    result=compare_records([{"code":"M1","name":"One"}],
        [{"id":"existing-id","code":"M1","name":"One"}],key="code",fields=("name",))
    assert (result["reusable"],result["missing"],result["conflicting"])==(1,0,0)


def test_same_code_different_details_is_conflict_not_missing():
    result=compare_records([{"code":"M1","name":"Changed"}],
        [{"id":"existing-id","code":"M1","name":"One"}],key="code",fields=("name",))
    assert result["missing"]==0 and result["conflicting"]==1
    assert result["details"][0]["candidate_ids"]==["existing-id"]


def test_multiple_matching_ids_require_manual_review():
    row={"code":"M1","name":"One"}
    result=compare_records([row],[{"id":"a",**row},{"id":"b",**row}],key="code",fields=("name",))
    assert result["ambiguous"]==1 and result["reusable"]==0


def test_exam_reference_codes_are_not_silently_normalized_or_created():
    result=compare_records([{"module_code":"it3060","name":"HCI"}],
        [{"id":"IT3060","module_code":"IT3060","name":"HCI"}],key="module_code",fields=("name",))
    assert result["missing"]==1 and result["reusable"]==0
    assert result["details"][0]["key"]=="it3060"


def test_case_only_name_difference_requires_explicit_reference_review():
    result=compare_records([{"module_code":"IE3030","name":"Wide area Networks"}],
        [{"id":"IE3030","module_code":"IE3030","name":"Wide Area Networks"}],
        key="module_code",fields=("name",))
    assert result["conflicting"]==1 and result["missing"]==0


def test_unrelated_existing_rows_never_scheduled_for_change():
    assert comparison({"a":"same","b":"new"},{"a":"same","other":"untouched"})=={
        "source_rows":2,"missing":1,"identical_skip":1,"conflicts":0,
        "missing_key_sha256":["b"],"conflicting_key_sha256":[]}


def test_primary_key_collision_does_not_become_insert():
    result=comparison({"same-id":"different-content"},{"same-id":"existing-content"})
    assert result["missing"]==0 and result["conflicts"]==1


@pytest.mark.parametrize("views",[shared.CORE,shared.DRAFTS])
def test_every_view_has_explicit_canonical_and_transaction_context_filters(views):
    for spec in views.values():
        assert shared.CURRENT in spec["query"]
        assert "current_setting('schedence.institution_id',true)" in spec["query"]
        assert "44dc54c1" not in spec["query"]
        assert spec["query"].startswith("SELECT ")


def test_sensitive_sources_and_fields_are_not_public_contracts():
    forbidden=("identity.","form_distributions","form_distribution_recipients","lecturer_preferences",
               "identity_user_id","email","document_json","source_values")
    for spec in shared.CORE.values():
        for token in forbidden:
            assert token not in spec["query"]
    assert "payload" not in shared.CORE["module_source_records"]["query"]


def test_duplicate_module_code_does_not_enter_default_modules_view():
    assert "NOT EXISTS" in shared.CORE["modules"]["query"]
    assert "other.module_code=t.module_code" in shared.CORE["modules"]["query"]
    assert "module_source_records" in shared.CORE and "module_code_conflicts" in shared.CORE


def test_workload_is_versioned_draft_delivery_time_not_group_coverage_sum():
    query=shared.DRAFTS["staff_workload"]["query"]
    assert "delivery_coverage" not in query
    assert "DRAFT_VALIDATED" in query and "assigned_draft_hours" in query
    assert "version_id" in query and "GROUP BY" in query


@pytest.mark.parametrize("version,views",[(1,shared.CORE),(2,shared.DRAFTS)])
def test_sql_artifact_has_no_source_row_changes_or_unrestricted_reader_grants(version,views):
    text=shared.migration(version,views)
    for prohibited in ("DELETE FROM","UPDATE academic.","UPDATE teaching.","TRUNCATE","DROP ","CREATE OR REPLACE",
                       "DISABLE ROW LEVEL SECURITY","BYPASSRLS TO","GRANT ALL","ALTER POLICY"):
        assert prohibited not in text
    assert "security_barrier=true,security_invoker=false" in text
    assert "NOLOGIN NOINHERIT NOSUPERUSER NOBYPASSRLS" in text
    assert "INSERT INTO teaching_shared.schema_migrations" in text
    assert "RETURN; -- Identical re-run: no changes." in text
    assert "neondb" in text and "Wrong existing Neon target" in text


def test_generated_sql_and_manifest_are_reproducible():
    for version,title,views in ((1,"authoritative_catalog",shared.CORE),(2,"experimental_and_draft_catalog",shared.DRAFTS)):
        assert (shared.OUT/f"v{version:03d}_{title}.sql").read_text(encoding="utf-8")==shared.migration(version,views)
