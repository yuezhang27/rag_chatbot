"""Generate a Markdown load-test report from Locust CSV outputs.

Usage:
    python scripts/generate_report.py --timestamp 20260418_120000

Reads CSV files matching reports/{timestamp}_*_users_stats.csv and produces
reports/loadtest_report_{timestamp}.md
"""

from __future__ import annotations

import argparse
import csv
import glob
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPORTS_DIR = PROJECT_ROOT / "reports"


def parse_stats_csv(filepath: str) -> dict | None:
    """Parse a Locust *_stats.csv and return the Aggregated row as a dict."""
    with open(filepath, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("Name") == "Aggregated":
                return row
    return None


def parse_stats_history_csv(filepath: str) -> list[dict]:
    """Parse a Locust *_stats_history.csv and return all rows."""
    rows = []
    with open(filepath, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows


def extract_user_count(filename: str) -> int | None:
    """Extract user count from filename like '20260418_120000_50_users_stats.csv'."""
    m = re.search(r"_(\d+)_users_stats\.csv$", filename)
    return int(m.group(1)) if m else None


def build_report(timestamp: str) -> str:
    """Build the full Markdown report."""
    pattern = str(REPORTS_DIR / f"{timestamp}_*_users_stats.csv")
    stat_files = sorted(glob.glob(pattern))

    if not stat_files:
        print(f"No stats CSV files found for timestamp {timestamp}")
        print(f"Looked for: {pattern}")
        sys.exit(1)

    levels: list[dict] = []
    for fp in stat_files:
        user_count = extract_user_count(fp)
        if user_count is None:
            continue
        agg = parse_stats_csv(fp)
        if agg is None:
            continue
        levels.append({"users": user_count, "stats": agg, "file": fp})

    levels.sort(key=lambda x: x["users"])

    lines: list[str] = []
    lines.append(f"# Load Test Report — {timestamp}")
    lines.append("")
    lines.append(f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    lines.append(f"**Levels tested:** {', '.join(str(l['users']) for l in levels)} concurrent users")
    lines.append("")

    # ---- Summary table ----
    lines.append("## Summary")
    lines.append("")
    lines.append("| Concurrent Users | Requests | Failures | Fail % | Median (ms) | P95 (ms) | P99 (ms) | Avg (ms) | RPS |")
    lines.append("|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|")

    for lvl in levels:
        s = lvl["stats"]
        total_req = s.get("Request Count", "0")
        fail_count = s.get("Failure Count", "0")

        total_int = int(total_req) if total_req else 0
        fail_int = int(fail_count) if fail_count else 0
        fail_pct = f"{fail_int / total_int * 100:.1f}%" if total_int > 0 else "N/A"

        median = s.get("Median Response Time", "N/A")
        p95 = s.get("95%", s.get("95% Percentile", "N/A"))
        p99 = s.get("99%", s.get("99% Percentile", "N/A"))
        avg = s.get("Average Response Time", "N/A")
        rps = s.get("Requests/s", "N/A")

        lines.append(
            f"| {lvl['users']} | {total_req} | {fail_count} | {fail_pct} "
            f"| {median} | {p95} | {p99} | {avg} | {rps} |"
        )

    lines.append("")

    # ---- Per-level details ----
    lines.append("## Per-Level Details")
    lines.append("")

    for lvl in levels:
        s = lvl["stats"]
        lines.append(f"### {lvl['users']} Concurrent Users")
        lines.append("")
        lines.append(f"- **Total Requests:** {s.get('Request Count', 'N/A')}")
        lines.append(f"- **Failures:** {s.get('Failure Count', 'N/A')}")
        lines.append(f"- **Median Response Time:** {s.get('Median Response Time', 'N/A')} ms")
        lines.append(f"- **Average Response Time:** {s.get('Average Response Time', 'N/A')} ms")
        lines.append(f"- **Min Response Time:** {s.get('Min Response Time', 'N/A')} ms")
        lines.append(f"- **Max Response Time:** {s.get('Max Response Time', 'N/A')} ms")
        lines.append(f"- **P95:** {s.get('95%', s.get('95% Percentile', 'N/A'))} ms")
        lines.append(f"- **P99:** {s.get('99%', s.get('99% Percentile', 'N/A'))} ms")
        lines.append(f"- **RPS:** {s.get('Requests/s', 'N/A')}")
        lines.append("")

    # ---- Cache hit rate note ----
    lines.append("## Cache Hit Rate")
    lines.append("")
    lines.append("Cache hit rate should be checked via LangSmith traces or application logs.")
    lines.append("Since the test uses random questions from a pool of 5, repeated questions will")
    lines.append("hit the semantic cache after the first occurrence. Expected cache hit rate")
    lines.append("increases with longer test durations and smaller question pools.")
    lines.append("")
    lines.append("> **Manual check:** After the test, go to LangSmith → Filter runs by")
    lines.append("> `cache_hit` metadata, or check Redis keys: `docker exec rag-redis redis-cli KEYS 'rag:cache:*'`")
    lines.append("")

    # ---- Bottleneck analysis ----
    lines.append("## Bottleneck Analysis")
    lines.append("")

    # Analyze failure patterns
    has_failures = any(int(l["stats"].get("Failure Count", "0")) > 0 for l in levels)
    first_fail_level = None
    for lvl in levels:
        if int(lvl["stats"].get("Failure Count", "0")) > 0:
            first_fail_level = lvl["users"]
            break

    if has_failures and first_fail_level:
        lines.append(f"- **Failures detected starting at {first_fail_level} concurrent users.**")
        lines.append("  - Check if failures are 429 (Azure OpenAI rate limiting) or 500 (backend errors)")
        lines.append("  - Review Locust failures CSV for detailed error messages")
        lines.append("")
    else:
        lines.append("- No failures detected across all tested levels.")
        lines.append("")

    # Latency trend
    if len(levels) >= 2:
        first_p95 = float(levels[0]["stats"].get("95%", levels[0]["stats"].get("95% Percentile", "0")) or "0")
        last_p95 = float(levels[-1]["stats"].get("95%", levels[-1]["stats"].get("95% Percentile", "0")) or "0")
        if first_p95 > 0:
            ratio = last_p95 / first_p95
            lines.append(f"- **Latency scaling:** P95 at {levels[-1]['users']} users is {ratio:.1f}x of "
                         f"P95 at {levels[0]['users']} users ({last_p95:.0f} ms vs {first_p95:.0f} ms)")
            if ratio > 3:
                lines.append("  - ⚠️ Significant latency degradation under load — consider scaling up")
            lines.append("")

    # ---- Scaling recommendations ----
    lines.append("## Scaling Recommendations")
    lines.append("")
    lines.append("Based on the test results, consider the following for production scaling:")
    lines.append("")
    lines.append("1. **Azure OpenAI Rate Limiting (429)**")
    lines.append("   - If 429 errors appear at higher concurrency, request a higher TPM quota")
    lines.append("   - Consider multi-deployment load balancing across regions")
    lines.append("")
    lines.append("2. **Container Apps Scaling**")
    lines.append("   - Configure auto-scaling rules based on concurrent HTTP requests")
    lines.append("   - Set min replicas > 0 to avoid cold-start latency spikes")
    lines.append("")
    lines.append("3. **Semantic Cache**")
    lines.append("   - Cache hit rate directly reduces LLM costs and latency")
    lines.append("   - Monitor cache hit rate in production and tune similarity threshold")
    lines.append("   - Consider Azure Cache for Redis (Premium) for production workloads")
    lines.append("")
    lines.append("4. **Azure AI Search**")
    lines.append("   - Monitor QPS against tier limits (Basic: 15 QPS, Standard S1: ~60 QPS)")
    lines.append("   - Upgrade tier or add replicas if search becomes a bottleneck")
    lines.append("")
    lines.append("5. **Distributed Load Testing**")
    lines.append("   - For 500+ concurrent users, consider distributed Locust workers")
    lines.append("   - Local machine CPU/network may become a bottleneck at high concurrency")
    lines.append("")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate load test report from Locust CSVs")
    parser.add_argument("--timestamp", required=True, help="Timestamp prefix of the CSV files")
    args = parser.parse_args()

    report = build_report(args.timestamp)

    output_path = REPORTS_DIR / f"loadtest_report_{args.timestamp}.md"
    output_path.write_text(report, encoding="utf-8")
    print(f"Report written to {output_path}")


if __name__ == "__main__":
    main()
