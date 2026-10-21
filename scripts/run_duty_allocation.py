r"""Start/check staff duty allocation against the existing Neon database.

From the repository root:
    .venv\Scripts\python.exe scripts/run_duty_allocation.py
    .venv\Scripts\python.exe scripts/run_duty_allocation.py check
"""

from pathlib import Path
import subprocess
import sys


def main() -> int:
    directory = Path(__file__).resolve().parents[1] / "services" / "examination-service"
    arguments = sys.argv[1:] or ["serve"]
    return subprocess.call([sys.executable, "-m", "app.duty_allocation.neon_shared", *arguments], cwd=directory)


if __name__ == "__main__":
    raise SystemExit(main())
