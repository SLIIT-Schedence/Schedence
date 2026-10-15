"""Bounded small/medium synthetic audit; not a campus scalability claim."""

import json
from pathlib import Path
import runpy


def main():
    fixture = runpy.run_path(str(Path(__file__).with_name("integration_fixture.py")))
    rows = []
    for name, copies, limit in (("small", 1, 5.0), ("medium", 3, 8.0)):
        case = fixture["build_case"](copies=copies, time_limit=limit)
        _, stats = fixture["run_case"](case)
        rows.append(dict(case=name, total_native_search_budget_seconds=limit, **stats))
    output = dict(synthetic=True, university_ready=False, real_campus_scalability_established=False,
        timing_note="Search shares one budget across stages. Fixture preparation, model construction and independent validation are outside it. Single worker, seed 7; machine/load affect results.",
        benchmarks=rows)
    print(json.dumps(output, indent=2))
    return output


if __name__ == "__main__":
    main()
