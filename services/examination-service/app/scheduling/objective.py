"""CP-SAT workload objective. Independent evaluation is in evaluation.py."""

from collections import Counter, defaultdict
from itertools import combinations

from app.scheduling.availability import allowed_starts, minute_offset
from app.scheduling.contracts import SchedulingValidationError, Violation
from app.scheduling.optimization_policy import workload_units


def add_objective(model, starts, snapshot, policy, baseline=None):
    modules = {m.module_code: m for m in snapshot.modules}
    candidates = {code: allowed_starts(snapshot, m) for code, m in modules.items()}
    by_student = defaultdict(list)
    for r in snapshot.registrations:
        by_student[r.student_id].append(r.module_code)
    patterns = Counter(tuple(sorted(codes)) for codes in by_student.values())
    scale = policy.score_scale
    # Validate the largest possible weighted objective before CP-SAT int arithmetic.
    max_pairs = max(len(codes) * (len(codes) - 1) // 2 for codes in patterns)
    if (len(by_student) * scale * sum(policy.weights) > 2**60
            or max_pairs * scale * scale > 2**60
            or policy.difficult_target_minutes * scale > 2**60):
        raise SchedulingValidationError((Violation("OPTIMIZATION", "Objective exceeds safe integer bounds; reduce weights or score_scale."),))
    horizon = ((snapshot.period.end_date - snapshot.period.start_date).days + 1) * 1440
    days = {}
    for code in modules:
        days[code] = model.new_int_var(0, horizon // 1440, f"day:{code}")
        model.add_division_equality(days[code], starts[code], 1440)

    def scaled_fraction(value, upper, denominator, name):
        numerator = model.new_int_var(0, upper * scale, name + ":numerator")
        model.add(numerator == value * scale)
        result = model.new_int_var(0, scale, name)
        model.add_division_equality(result, numerator, denominator)
        return result

    pairs = sorted({pair for codes in patterns for pair in combinations(codes, 2)})
    pair_penalties = {}
    for a, b in pairs:
        tag = f"{a}:{b}"
        da = modules[a].duration_minutes + modules[a].reading_time_minutes
        db = modules[b].duration_minutes + modules[b].reading_time_minutes
        gap = model.new_int_var(0, horizon, "gap:" + tag)
        model.add_max_equality(gap, [starts[b] - starts[a] - da, starts[a] - starts[b] - db])
        same_day = model.new_bool_var("same_day:" + tag)
        model.add(days[a] == days[b]).only_enforce_if(same_day)
        model.add(days[a] != days[b]).only_enforce_if(same_day.Not())
        penalties = []
        for low, high, active, name in (
            (policy.preparation_target_minutes, policy.difficult_target_minutes,
             min(modules[a].difficulty, modules[b].difficulty) >= policy.high_difficulty_minimum, "S1"),
            (policy.short_gap_minutes, policy.preparation_target_minutes, True, "S2"),
        ):
            if not active:
                penalties.append(0)
                continue
            capped = model.new_int_var(low, max(horizon, low), name + ":clamp:" + tag)
            model.add_max_equality(capped, [gap, low])
            missing = model.new_int_var(0, high - low, name + ":missing:" + tag)
            model.add_max_equality(missing, [0, high - capped])
            penalties.append(scaled_fraction(missing, high - low, high - low, name + ":" + tag))
        missing = model.new_int_var(0, policy.short_gap_minutes, "S3:missing:" + tag)
        model.add_max_equality(missing, [0, policy.short_gap_minutes - gap])
        short = scaled_fraction(missing, policy.short_gap_minutes, policy.short_gap_minutes, "S3:short:" + tag)
        p3 = model.new_int_var(0, scale, "S3:" + tag)
        model.add_max_equality(p3, [same_day * scale, short])
        penalties.append(p3)
        pair_penalties[a, b] = penalties
    span = (snapshot.period.end_date - snapshot.period.start_date).days + 1
    # Only distinct clipped windows; work is bounded by the period, even if W > span.
    windows = sorted({(max(0, x), min(span, x + policy.window_days))
                      for x in (*range(span), *range(1 - policy.window_days, span - policy.window_days + 1))})
    members = {}
    for code, domain in candidates.items():
        for i, (lo, hi) in enumerate(windows):
            inside = model.new_bool_var(f"window:{code}:{i}")
            model.add_allowed_assignments([starts[code], inside], [(t, int(lo <= t // 1440 < hi)) for t in domain])
            members[code, i] = inside
    loads = {code: workload_units(m, policy) for code, m in modules.items()}
    objective_terms = []
    # All normalized values are exact floor divisions, independently recomputed later.
    for index, (codes, multiplicity) in enumerate(sorted(patterns.items())):
        n = len(codes)
        pair_bound = n * (n - 1) // 2 * scale
        total_load = sum(loads[c] for c in codes)
        deviations = {c: n * loads[c] - total_load for c in codes}
        single_excess = max(0, *deviations.values())
        load_bound = sum(max(0, d) for d in deviations.values()) - single_excess
        if sum(abs(d) for d in deviations.values()) * scale > 2**60:
            raise SchedulingValidationError((Violation("OPTIMIZATION", "Workload exceeds safe integer bounds; reduce load_scale or reference ratios."),))
        density_bound = max(0, n - policy.preferred_window_exams)
        raw = [sum(pair_penalties[a, b][k] for a, b in combinations(codes, 2)) for k in range(3)]
        if load_bound:
            peak = model.new_int_var(0, load_bound, f"load_peak:{index}")
            model.add_max_equality(peak, [0, *(sum(deviations[c] * members[c, i] for c in codes) - single_excess
                                             for i in range(len(windows)))])
            raw.append(peak)
        else:
            raw.append(0)
        if density_bound:
            peak = model.new_int_var(0, density_bound, f"density_peak:{index}")
            model.add_max_equality(peak, [0, *(sum(members[c, i] for c in codes) - policy.preferred_window_exams
                                             for i in range(len(windows)))])
            raw.append(peak)
        else:
            raw.append(0)
        for k, (value, bound, weight) in enumerate(zip(raw, (pair_bound, pair_bound, pair_bound, load_bound, density_bound), policy.weights)):
            if bound and weight:
                normalized = model.new_int_var(0, scale, f"normalized:{index}:{k}")
                # CP-SAT integer division requires an affine numerator: channel sums first.
                numerator = model.new_int_var(0, bound * scale, f"numerator:{index}:{k}")
                model.add(numerator == value * scale)
                model.add_division_equality(normalized, numerator, bound)
                objective_terms.append(weight * multiplicity * normalized)
    objective = sum(objective_terms)
    model.minimize(objective)
    if baseline is not None:
        baseline_sessions = {s.module_code: s for s in baseline.sessions}
        for code, var in starts.items():
            model.add_hint(var, minute_offset(snapshot, baseline_sessions[code].start))
    return objective
