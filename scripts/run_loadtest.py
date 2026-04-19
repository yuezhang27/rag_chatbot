"""Automated stepped load-test runner.

Runs Locust in headless mode at 10 / 50 / 100 / 500 concurrent users,
exports CSV stats per level, and then invokes generate_report.py.

Usage:
    python scripts/run_loadtest.py --host https://your-app.azurecontainerapps.io
    python scripts/run_loadtest.py --host http://localhost:8000  # local
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
REPORTS_DIR = PROJECT_ROOT / "reports"
LOCUSTFILE = SCRIPT_DIR / "locustfile.py"

# Stepped concurrency levels: (users, spawn_rate, run_time)
LEVELS = [
    (10, 2, "3m"),
    (50, 10, "3m"),
    (100, 20, "3m"),
    (500, 50, "5m"),
]


def run_level(host: str, users: int, spawn_rate: int, run_time: str, timestamp: str) -> None:
    """Run a single Locust level and save CSV output."""
    csv_prefix = str(REPORTS_DIR / f"{timestamp}_{users}_users")
    cmd = [
        sys.executable, "-m", "locust",
        "-f", str(LOCUSTFILE),
        f"--host={host}",
        f"--users={users}",
        f"--spawn-rate={spawn_rate}",
        f"--run-time={run_time}",
        "--headless",
        f"--csv={csv_prefix}",
        "--only-summary",
    ]
    print(f"\n{'='*60}")
    print(f"  Level: {users} users | spawn-rate: {spawn_rate} | duration: {run_time}")
    print(f"{'='*60}\n")
    subprocess.run(cmd, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Stepped load test runner")
    parser.add_argument(
        "--host",
        default=os.environ.get("TARGET_HOST", "http://localhost:8000"),
        help="Target host URL (default: $TARGET_HOST or http://localhost:8000)",
    )
    parser.add_argument(
        "--levels",
        type=str,
        default=None,
        help="Comma-separated user counts to test, e.g. '10,50'. Default: 10,50,100,500",
    )
    args = parser.parse_args()

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    # Determine which levels to run
    if args.levels:
        requested = [int(x.strip()) for x in args.levels.split(",")]
        levels = [(u, sr, rt) for u, sr, rt in LEVELS if u in requested]
        if not levels:
            print(f"No matching levels for {requested}. Available: {[u for u,_,_ in LEVELS]}")
            sys.exit(1)
    else:
        levels = LEVELS

    print(f"Target host : {args.host}")
    print(f"Levels      : {[u for u,_,_ in levels]}")
    print(f"Timestamp   : {timestamp}")
    print(f"Reports dir : {REPORTS_DIR}")

    for users, spawn_rate, run_time in levels:
        run_level(args.host, users, spawn_rate, run_time, timestamp)

    # Generate report
    print(f"\n{'='*60}")
    print("  Generating report...")
    print(f"{'='*60}\n")
    subprocess.run(
        [sys.executable, str(SCRIPT_DIR / "generate_report.py"), "--timestamp", timestamp],
        check=True,
    )

    print(f"\nDone! Reports saved to {REPORTS_DIR}/")


if __name__ == "__main__":
    main()
