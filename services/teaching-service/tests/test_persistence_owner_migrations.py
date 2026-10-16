"""Verify owner migrations without connecting or requiring other services' APIs."""

import importlib.util
from pathlib import Path
import re

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

ROOT = Path(__file__).resolve().parents[3]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def tables(sql):
    return {name: {line.strip().rstrip(",").strip() for line in body.strip().splitlines()}
        for name, body in re.findall(r"CREATE TABLE resource\.(\w+) \((.*?)\n\)", sql, re.DOTALL)}


def test_resource_frozen_revision_matches_owning_service_metadata():
    model = load(ROOT / "services/resource-service/src/schedence_resource/models.py", "resource_model_test")
    frozen = (ROOT / "services/resource-service/migrations/versions/20261016_0001_venue_inventory.sql").read_text(encoding="utf-8")
    compiled = "\n".join(str(CreateTable(table).compile(dialect=postgresql.dialect())) for table in model.metadata.sorted_tables)
    assert tables(frozen) == tables(compiled)
    assert set(tables(frozen)) == {"source_datasets", "venues"}


@pytest.mark.parametrize("folder,filename,count", [
    ("academic-data-service/alembic", "20261016_0002_teaching_inputs", 12),
    ("resource-service/migrations", "20261016_0001_venue_inventory", 2),
])
def test_new_owner_revisions_force_tenant_rls_without_touching_existing_tables(folder, filename, count):
    path = ROOT / "services" / folder / "versions" / f"{filename}.py"
    revision = load(path, filename)
    assert len(revision.TABLES) == count
    source = path.read_text(encoding="utf-8")
    assert "ENABLE ROW LEVEL SECURITY" in source and "FORCE ROW LEVEL SECURITY" in source
    assert "WITH CHECK ({predicate})" in source and "schedence.institution_id" in source
    sql = path.with_suffix(".sql").read_text(encoding="utf-8")
    assert not re.search(r"^\s*(DROP|TRUNCATE|DELETE|UPDATE|ALTER)\b", sql, re.MULTILINE)
    assert "CREATE TABLE academic.staff (" not in sql
    assert "identity." not in sql and "examinations." not in sql
    for table in revision.TABLES:
        body = re.search(rf"CREATE TABLE \w+\.{table} \((.*?)\n\)", sql, re.DOTALL).group(1)
        assert "institution_id UUID NOT NULL" in body
        names = re.findall(r"CONSTRAINT (\w+)", body)
        assert len(names) == len(set(names))


def test_module_codes_remain_nonunique_and_staff_references_preserve_source_ids():
    sql = (ROOT / "services/academic-data-service/alembic/versions/20261016_0002_teaching_inputs.sql").read_text(encoding="utf-8")
    assert not re.search(r"UNIQUE\s*\([^)]*module_code", sql)
    assert "REFERENCES academic.staff (institution_id, staff_number)" in sql
    assert "pp1_test_candidate BOOLEAN NOT NULL" in sql
    assert "academic_classification VARCHAR(40) NOT NULL" in sql
