"""Shared lunch scope and attendance semantics for assessment and scheduling."""


def lunch_applicability(policy, on_date, stream, configured_streams):
    """Return True/False/None plus diagnostics; None means unresolved applicability."""
    reasons = []
    if stream not in configured_streams:
        reasons.append("group_stream_not_scheduled_on_date")
    if policy.applicable_dates is not None and on_date not in policy.applicable_dates:
        reasons.append("date_excluded_by_policy")
    if policy.applicable_streams is not None and stream not in policy.applicable_streams:
        reasons.append("stream_excluded_by_policy")
    if reasons:
        return False, tuple(reasons)
    if policy.applicable_dates is None or policy.applicable_streams is None:
        return None, ("date_or_stream_applicability_unresolved",)
    return True, ()


def attending_group_ids(prepared, direct_group_ids):
    """Direct groups and descendants attend, never parents or sibling unions."""
    attending = set(direct_group_ids)
    for identity in direct_group_ids:
        attending.update(prepared.hierarchy.descendants(identity))
    return tuple(sorted(attending))
