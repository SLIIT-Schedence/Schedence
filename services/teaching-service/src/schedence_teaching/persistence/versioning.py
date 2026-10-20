"""Stable save identity and normalized booking digests, independent of SQL."""

from copy import deepcopy

from .snapshots import canonical_json, content_digest

METADATA_KEY = "_teaching_persistence"


def _preference_diagnostics(value, field=None):
    # These are collections of independent observations, not lexicographic
    # objective order. Preserve every entry/value and all ordered policy lists.
    unordered = {"staff", "evaluations", "modeled_evaluations", "diagnostics", "teaching_days",
        "daily_teaching_minutes", "preference_ids", "applied_policy_references"}
    if isinstance(value, dict):
        return {key: _preference_diagnostics(item, key) for key, item in value.items()}
    if isinstance(value, list):
        items = [_preference_diagnostics(item) for item in value]
        return sorted(items, key=canonical_json) if field in unordered else items
    return value


def run_identity(snapshot_digest, result, logical_status):
    data = deepcopy(result)
    data.pop(METADATA_KEY, None)
    # Elapsed measurement and the attached (untrusted) validator summary are
    # not booking identity. Validation is always recomputed before saving.
    data.pop("runtime_seconds", None)
    data.pop("constraint_validation", None)
    data["schedule"] = sorted(data.get("schedule", []), key=canonical_json)
    if data.get("preference_optimization") is not None:
        data["preference_optimization"] = _preference_diagnostics(data["preference_optimization"])
    return content_digest(dict(snapshot_sha256=snapshot_digest, result=data, logical_status=logical_status))


def delivery_digest(rows):
    normalized = deepcopy(list(rows))
    for row in normalized:
        row["coverage"] = sorted(row["coverage"], key=canonical_json)
    return content_digest(sorted(normalized, key=canonical_json))
