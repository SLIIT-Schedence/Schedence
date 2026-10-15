"""Run `python student-group-pilot.py --mode DEMO` for the labelled local example.

Without explicit DEMO, this defaults to REAL_CAMPUS and requires reviewed inputs.
"""

from schedence_teaching.pilot.__main__ import main


if __name__ == "__main__":
    raise SystemExit(main())
