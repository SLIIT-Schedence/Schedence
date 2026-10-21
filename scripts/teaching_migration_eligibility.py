"""Read-only eligibility review for the approved 335 canonical local candidates.

Experimental immutable snapshots and their history are excluded as a unit.
New local rows outside the reviewed candidate hashes are never silently included.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from psycopg import sql
from sqlalchemy.engine import make_url

from dev_seed import connect, database_identity
from plan_existing_neon import CURRENT, LOCAL, ROOT, sha


def form_eligibility(rows, approved_hashes):
    approved = set(approved_hashes)
    by_hash = {key: (row, digest) for key, row, digest in rows}
    if len(by_hash) != len(rows) or approved - by_hash.keys():
        raise ValueError("Reviewed candidate missing or duplicate source identity")
    accepted, excluded = [], []
    for key, row, digest in rows:
        item = dict(id=str(row["id"]), key_sha256=key, row_sha256=digest,
                    revision=row["revision"])
        if key not in approved:
            excluded.append(item | {"reason": "OUTSIDE_REVIEWED_335_CANDIDATES"})
        else:
            if row["institution_id"] != CURRENT:
                raise ValueError("Wrong institution in approved Forms candidate")
            from schedence_teaching.forms.contracts import FormTemplate
            from schedence_teaching.forms.presets import validate_questions
            document = FormTemplate.model_validate(row["document"])
            validate_questions(document.preset_id, document.questions)
            if (str(document.id), str(document.institution_id), document.revision) != (
                    row["id"], row["institution_id"], row["revision"]):
                raise ValueError("Forms envelope identity/revision mismatch")
            accepted.append(item)
    return accepted, excluded


def review():
    baseline = json.loads((ROOT / "scripts/neon-review/existing-neondb-audit-20261021-v1.json").read_text())
    approved = baseline["insert_dry_run"]
    config = ROOT / "services/teaching-service/local-data/persistence-preview/step12b4-reviewed/isolated-database.json"
    with connect(make_url(json.loads(config.read_text())["admin_url"])) as conn:
        if database_identity(conn) != LOCAL:
            raise ValueError("Unexpected local source")
        snapshots = []
        for identifier, payload in conn.execute("SELECT id,payload FROM teaching.input_snapshots WHERE institution_id=%s", (CURRENT,)):
            document = json.loads(payload["document_json"])
            experimental = document["calendar"].get("synthetic_data") is True
            groups = document["teaching_input"]["student_groups"]
            if not experimental or not groups or not all(
                    g["group_id"].startswith(("PP1-", "PLAN-")) and "PP1" in g["group_id"] for g in groups):
                raise ValueError("Snapshot eligibility changed; requires renewed dependency review")
            snapshots.append(dict(id=str(identifier), reason="SYNTHETIC_PP1_CALENDAR_AND_EXPERIMENTAL_GROUPS",
                                  student_groups=len(groups)))
        rows = conn.execute(sql.SQL("SELECT {},to_jsonb(t),{} FROM teaching.form_template_drafts t WHERE institution_id=%s").format(
            sha(sql.SQL("jsonb_build_array(t.id)")), sha(sql.SQL("to_jsonb(t)"))), (CURRENT,)).fetchall()
        eligible, unreviewed = form_eligibility(rows, approved["teaching.form_template_drafts"]["missing_key_sha256"])
        counts = {}
        for table, plan in approved.items():
            count = conn.execute(sql.SQL("SELECT count(*) FROM {} WHERE institution_id=%s").format(sql.Identifier(*table.split("."))), (CURRENT,)).fetchone()[0]
            if table != "teaching.form_template_drafts" and count != plan["source_rows"]:
                raise ValueError("Reviewed non-Forms source counts changed")
            counts[table] = dict(approved=plan["source_rows"], source_now=count,
                                 eligible=len(eligible) if table.endswith("form_template_drafts") else 0,
                                 excluded=0 if table.endswith("form_template_drafts") else plan["source_rows"])
        active = conn.execute("SELECT count(*) FROM teaching.generation_jobs WHERE institution_id=%s AND state IN ('PENDING','RUNNING')", (CURRENT,)).fetchone()[0]
        if active:
            raise ValueError("Canonical source generation is active")
    return dict(created_utc=datetime.now(timezone.utc).isoformat(), mode="READ_ONLY_NO_APPLY",
                institution_id=CURRENT, approved_candidates=sum(p["source_rows"] for p in approved.values()),
                eligible=len(eligible), skipped_experimental=sum(v["excluded"] for v in counts.values()),
                extra_unreviewed=len(unreviewed), forms=eligible, unreviewed=unreviewed,
                snapshots=snapshots, tables=counts, active_canonical_jobs=active,
                note="All 331 non-Forms candidates belong to the two excluded PP1 snapshot/history aggregates; "
                     "do not rewrite immutable snapshots or detach history to bypass the exclusion.")


if __name__ == "__main__":
    result = review()
    output = ROOT / "scripts/neon-review/approved-migration-eligibility-20261021-v1.json"
    with output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2)
    print(json.dumps({k: v for k, v in result.items() if k in {
        "approved_candidates", "eligible", "skipped_experimental", "extra_unreviewed", "active_canonical_jobs"}}, indent=2))
