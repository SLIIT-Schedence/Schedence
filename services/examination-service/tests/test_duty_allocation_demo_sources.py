"""Member 3 demo settings, the local CSV source and the in-memory preview store (no database)."""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

import pytest

from app.duty_allocation.demo_database import TABLE_COLUMNS, DemoDatabaseUnavailable, DemoSchemaError, load_m3_demo_records
from app.duty_allocation.demo_local import CsvRowSource
from app.duty_allocation.demo_service import DemoServiceError, PreviewStore, StoredAllocation, StoredSeating
from app.duty_allocation.demo_settings import DemoSource, Member3DemoSettings


def test_defaults_are_disabled_and_hold_no_secret_fields():
    settings = Member3DemoSettings()
    assert settings.source is DemoSource.DISABLED and settings.missing_setup() == ()
    assert not [f for f in Member3DemoSettings.model_fields if "password" in f or f.endswith("url") or "secret" in f]


def test_settings_come_from_m3_demo_environment_variables(monkeypatch, tmp_path):
    monkeypatch.setenv("M3_DEMO_SOURCE", "LOCAL_DEMO")
    monkeypatch.setenv("M3_DEMO_SEED_DIR", str(tmp_path))
    monkeypatch.setenv("M3_DEMO_PERIOD_CODE", "PP1-2026-S2-FINAL")
    settings = Member3DemoSettings()
    assert settings.source is DemoSource.LOCAL_DEMO and settings.seed_dir == tmp_path and settings.missing_setup() == ()


def test_missing_setup_names_variables_not_values():
    assert Member3DemoSettings(source=DemoSource.LOCAL_DEMO).missing_setup() == ("M3_DEMO_SEED_DIR",)
    missing = Member3DemoSettings(source=DemoSource.NEON_READ_ONLY).missing_setup()
    assert set(missing) == {"M3_DEMO_NEON_ENV_FILE", "M3_DEMO_NEON_URL_VARIABLE", "M3_DEMO_NEON_HOST",
                            "M3_DEMO_NEON_DATABASE", "M3_DEMO_NEON_ROLE", "M3_DEMO_NEON_ACKNOWLEDGEMENT",
                            "M3_DEMO_NEON_CONFIRMED_IN_CONSOLE=true", "M3_DEMO_NEON_CREDENTIAL_ROTATED=true"}
    assert Member3DemoSettings(source=DemoSource.NEON_READ_ONLY, neon_env_file="/x/e.env", neon_url_variable="V",
                               neon_host="h", neon_database="d", neon_role="r", neon_acknowledgement="a",
                               neon_confirmed_in_console=True, neon_credential_rotated=True).missing_setup() == ()
    for bad in ("unknown", "local"):
        with pytest.raises(ValueError):
            Member3DemoSettings(source=bad)
    with pytest.raises(ValueError):
        Member3DemoSettings(period_code="lower case")


def tiny_pack(tmp_path, rows=1):
    (tmp_path / "csv").mkdir()
    headers = list(TABLE_COLUMNS["staff_admin_workload"])
    types = {"staff_id": "TEXT", "institution_id": "TEXT", "as_of": "DATE", "aggregate_status": "TEXT",
             "total_admin_hours_per_week": "NUMERIC", "combination_rule": "TEXT", "available_via_api": "BOOLEAN",
             "notes": "TEXT", "dataset_status": "TEXT"}
    (tmp_path / "manifest.json").write_text(json.dumps({"sheets": {
        "STAFF_ADMIN_WORKLOAD": {"seed": True, "headers": headers, "column_types": types},
        "NOTES": {"seed": False, "headers": [], "column_types": {}}}}), encoding="utf-8")
    lines = [",".join(headers)] + [f"s{n},i,2026-11-16,COMPUTED,{n}.5,SUM,true,n,SYNTHETIC_TEST_DATA" for n in range(rows)]
    lines.append("s9,i,2026-11-16,NOT_COMPUTED,,,false,n,SYNTHETIC_TEST_DATA")
    (tmp_path / "csv" / "staff_admin_workload.csv").write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    return CsvRowSource(tmp_path)


async def test_csv_source_converts_types_and_skips_non_seed_sheets(tmp_path):
    source = tiny_pack(tmp_path, rows=2)
    await source.begin_read_only()
    assert set(await source.schema_columns()) == {"staff_admin_workload"}
    rows = await source.fetch("staff_admin_workload")
    assert rows[0]["total_admin_hours_per_week"] == Decimal("0.5") and rows[0]["available_via_api"] is True
    assert rows[0]["as_of"] == dt.date(2026, 11, 16)
    assert rows[2]["aggregate_status"] == "NOT_COMPUTED" and rows[2]["total_admin_hours_per_week"] is None
    assert rows[2]["available_via_api"] is False
    with pytest.raises(DemoSchemaError):
        await source.fetch("pg_shadow")  # not on the allow-list
    with pytest.raises(DemoSchemaError):
        await source.fetch("staff")  # allow-listed but absent from this pack


async def test_incomplete_pack_is_rejected_by_the_normal_schema_check(tmp_path):
    with pytest.raises(DemoSchemaError, match="m3_demo.staff is missing"):
        await load_m3_demo_records(tiny_pack(tmp_path))


async def test_missing_or_corrupt_pack_is_reported_without_paths(tmp_path):
    with pytest.raises(DemoDatabaseUnavailable) as missing:
        await CsvRowSource(tmp_path / "secret-folder").schema_columns()
    assert "secret-folder" not in str(missing.value)
    (tmp_path / "manifest.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(DemoSchemaError):
        await CsvRowSource(tmp_path).schema_columns()


class Item:
    pass


def test_preview_store_is_bounded_and_typed():
    store = PreviewStore(limit=2)
    for key in "abc":
        store.add(key, Item())
    with pytest.raises(DemoServiceError) as expired:
        store.get("a", Item)  # the oldest preview was dropped
    assert expired.value.status_code == 404 and expired.value.code == "M3_PREVIEW_NOT_FOUND"
    assert isinstance(store.get("c", Item), Item)
    with pytest.raises(DemoServiceError):
        store.get("c", StoredAllocation)  # wrong kind of preview
    with pytest.raises(DemoServiceError):
        store.get("c", StoredSeating)
