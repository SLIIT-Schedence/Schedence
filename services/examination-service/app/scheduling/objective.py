"""CP-SAT workload objective. Independent evaluation is in evaluation.py.

Students are scored on the exams they actually sit. Students whose modules all
have one session share their module starts and are grouped by module pattern,
as before. A student with a split module (several sessions) gets their own
group, whose start for that module is the start of their chosen session.
"""

from collections import Counter, defaultdict
from itertools import combinations
from fractions import Fraction

from ortools.sat.python import cp_model

from app.scheduling.availability import allowed_starts, minute_offset
from app.scheduling.contracts import SchedulingValidationError, Violation
from app.scheduling.optimization_policy import workload_units


def _order(key):
    """Sort key for timing keys: a module code sorts like before, a (module, student) key right after it."""
    return (key, "") if isinstance(key, str) else key


def add_objective(model, layout, snapshot, policy, baseline=None, *, fairness=None, baseline_evaluation=None):
    """``layout`` is the SessionLayout returned by build_hard_model."""
    modules = {m.module_code: m for m in snapshot.modules}
    candidates = {code: allowed_starts(snapshot, m) for code, m in modules.items()}
    by_student = defaultdict(list)
    for r in snapshot.registrations:
        by_student[r.student_id].append(r.module_code)
    split = layout.split
    # A timing key names one start variable: the module (shared) or (module, student) for a split module.
    starts = {code: layout.starts[code, 0] for code in modules if code not in split}
    domains = dict(candidates)
    groups = defaultdict(list)  # tuple of timing keys -> students scored on exactly those starts
    for student, codes in by_student.items():
        keys = []
        for code in sorted(codes):
            if code in split:
                key = (code, student)
                own = model.new_int_var_from_domain(cp_model.Domain.from_values(candidates[code]), f"own:{student}:{code}")
                for k in range(layout.sessions[code]):
                    model.add(own == layout.starts[code, k]).only_enforce_if(layout.assigned[student, code, k])
                starts[key], domains[key] = own, candidates[code]
                keys.append(key)
            else:
                keys.append(code)
        groups[tuple(keys)].append(student)
    module_of = {key: key if isinstance(key, str) else key[0] for key in starts}
    patterns = Counter({key: len(students) for key, students in groups.items()})
    scale = policy.score_scale
    protected = fairness is not None and fairness.enabled
    baseline_units = {}
    if protected:
        if baseline is None or baseline_evaluation is None:
            raise SchedulingValidationError((Violation("FAIRNESS", "Fairness protection requires a validated baseline."),))
        if (baseline.input_fingerprint != snapshot.fingerprint
                or baseline_evaluation.input_fingerprint != snapshot.fingerprint
                or baseline_evaluation.configuration_fingerprint != policy.fingerprint):
            raise SchedulingValidationError((Violation("FAIRNESS", "Baseline must use the same frozen input and scoring configuration."),))
        baseline_units = {s.student_id: s.objective_units for s in baseline_evaluation.student_penalties}
        if baseline_units.keys() != by_student.keys():
            raise SchedulingValidationError((Violation("FAIRNESS", "Baseline student penalties do not match the frozen registrations."),))
        denominator = scale * sum(policy.weights)
        individual_allowance = (Fraction(str(fairness.max_individual_increase_points)) * denominator / 100).__floor__()
        worst_allowance = (Fraction(str(fairness.max_worst_increase_points)) * denominator / 100).__floor__()
        worst_limit = max(baseline_units.values()) + worst_allowance
        baseline_by_pattern = {key: min(baseline_units[s] for s in students) + individual_allowance
                               for key, students in groups.items()}
    # Validate the largest possible weighted objective before CP-SAT int arithmetic.
    max_pairs = max(len(codes) * (len(codes) - 1) // 2 for codes in patterns)
    if (len(by_student) * scale * sum(policy.weights) > 2**60
            or max_pairs * scale * scale > 2**60
            or policy.difficult_target_minutes * scale > 2**60):
        raise SchedulingValidationError((Violation("OPTIMIZATION", "Objective exceeds safe integer bounds; reduce weights or score_scale."),))
    horizon = ((snapshot.period.end_date - snapshot.period.start_date).days + 1) * 1440
    days = {}
    for key in starts:
        name = key if isinstance(key, str) else f"{key[0]}:{key[1]}"
        days[key] = model.new_int_var(0, horizon // 1440, f"day:{name}")
        model.add_division_equality(days[key], starts[key], 1440)

    def scaled_fraction(value, upper, denominator, name):
        numerator = model.new_int_var(0, upper * scale, name + ":numerator")
        model.add(numerator == value * scale)
        result = model.new_int_var(0, scale, name)
        model.add_division_equality(result, numerator, denominator)
        return result

    pairs = sorted({pair for codes in patterns for pair in combinations(codes, 2)},
                   key=lambda pair: tuple(map(_order, pair)))
    pair_penalties = {}
    for a, b in pairs:
        ma, mb = modules[module_of[a]], modules[module_of[b]]
        tag = f"{a}:{b}" if isinstance(a, str) and isinstance(b, str) else f"{a}:{b}".replace("'", "")
        da = ma.duration_minutes + ma.reading_time_minutes
        db = mb.duration_minutes + mb.reading_time_minutes
        gap = model.new_int_var(0, horizon, "gap:" + tag)
        model.add_max_equality(gap, [starts[b] - starts[a] - da, starts[a] - starts[b] - db])
        same_day = model.new_bool_var("same_day:" + tag)
        model.add(days[a] == days[b]).only_enforce_if(same_day)
        model.add(days[a] != days[b]).only_enforce_if(same_day.Not())
        penalties = []
        for low, high, active, name in (
            (policy.preparation_target_minutes, policy.difficult_target_minutes,
             min(ma.difficulty, mb.difficulty) >= policy.high_difficulty_minimum, "S1"),
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
    for key, domain in domains.items():
        if key not in starts:
            continue  # a split module: scored through its candidates' own starts
        for i, (lo, hi) in enumerate(windows):
            inside = model.new_bool_var(f"window:{key}:{i}")
            model.add_allowed_assignments([starts[key], inside], [(t, int(lo <= t // 1440 < hi)) for t in domain])
            members[key, i] = inside
    module_loads = {code: workload_units(m, policy) for code, m in modules.items()}
    loads = {key: module_loads[module_of[key]] for key in starts}
    objective_terms = []
    # All normalized values are exact floor divisions, independently recomputed later.
    for index, (codes, multiplicity) in enumerate(sorted(patterns.items(), key=lambda item: tuple(map(_order, item[0])))):
        student_terms = []
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
                student_terms.append(weight * normalized)
        if protected:
            # Apply limits to ONE student, not the pattern's population total.
            student_objective = sum(student_terms)
            model.add(student_objective <= baseline_by_pattern[codes])
            if fairness.protect_worst_student:
                model.add(student_objective <= worst_limit)
    objective = sum(objective_terms)
    model.minimize(objective)
    if baseline is not None:
        # Hint every session start, and every split candidate's session, from the baseline.
        numbered = {(s.module_code, s.session_number - 1): s for s in baseline.sessions}
        for (code, k), var in layout.starts.items():
            if (code, k) in numbered:
                model.add_hint(var, minute_offset(snapshot, numbered[code, k].start))
        for (student, code, k), var in layout.assigned.items():
            session = numbered.get((code, k))
            model.add_hint(var, int(session is not None and any(
                seat.student_id == student for room in session.rooms for seat in room.seats)))
    return objective
