"""Read local staging data without exposing personal records or changing files."""

import argparse
import json
from pathlib import Path

from schedence_teaching.integration.audit import audit_development_datasets


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-data", type=Path, default=Path(__file__).resolve().parents[1] / "local-data")
    parser.add_argument("--include-row-diagnostics", action="store_true")
    args = parser.parse_args()
    report = audit_development_datasets(args.local_data, include_row_diagnostics=args.include_row_diagnostics)
    print(json.dumps(report, indent=2))
    if any(report["errors"].values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
