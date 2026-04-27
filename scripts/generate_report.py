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

# ---------------------------------------------------------------------------
# Cost projection defaults
# ---------------------------------------------------------------------------
AVG_INPUT_TOKENS = 2000
AVG_OUTPUT_TOKENS = 500
GPT4O_INPUT_PRICE = 2.50  # $/1M tokens
GPT4O_OUTPUT_PRICE = 10.00  # $/1M tokens
EMBEDDING_PRICE = 0.13  # $/1M tokens
AVG_EMBEDDING_TOKENS = 200
AZURE_SEARCH_MONTHLY = 250.0  # Standard S1
REDIS_MONTHLY = 55.0  # Basic C1
CONTAINER_APPS_MONTHLY = 50.0  # 1 vCPU / 2GB
AVG_QUERIES_PER_USER_PER_DAY = 3
CACHE_HIT_RATE = 0.60
WORKING_DAYS_PER_MONTH = 22


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


def _per_request_cost(
    input_tokens: int,
    output_tokens: int,
    embedding_tokens: int,
    gpt4o_input_price: float,
    gpt4o_output_price: float,
    embedding_price: float,
) -> tuple[float, float, float, float]:
    """Return (llm_input_cost, llm_output_cost, embed_cost, total)."""
    llm_in = input_tokens * gpt4o_input_price / 1_000_000
    llm_out = output_tokens * gpt4o_output_price / 1_000_000
    embed = embedding_tokens * embedding_price / 1_000_000
    return llm_in, llm_out, embed, llm_in + llm_out + embed


def _monthly_cost(
    users: int,
    queries_per_day: int,
    working_days: int,
    per_request_cost: float,
    cache_hit_rate: float,
) -> tuple[int, int, float]:
    """Return (monthly_queries, llm_requests, llm_cost)."""
    monthly_queries = users * queries_per_day * working_days
    llm_requests = int(monthly_queries * (1 - cache_hit_rate))
    llm_cost = llm_requests * per_request_cost
    return monthly_queries, llm_requests, llm_cost


def build_cost_projections(cfg: argparse.Namespace) -> str:
    """Build the Cost Projections section of the report."""
    lines: list[str] = []

    input_tokens = cfg.avg_input_tokens
    output_tokens = cfg.avg_output_tokens
    gpt4o_in = cfg.gpt4o_input_price
    gpt4o_out = cfg.gpt4o_output_price
    embed_price = cfg.embedding_price
    embed_tokens = cfg.avg_embedding_tokens
    queries_day = cfg.avg_queries_per_user_per_day
    cache_rate = cfg.cache_hit_rate
    work_days = cfg.working_days_per_month
    infra_cost = cfg.azure_search_monthly + cfg.redis_monthly + cfg.container_apps_monthly
    users_pilot = cfg.users_pilot
    users_company = cfg.users_company

    llm_in_cost, llm_out_cost, embed_cost, total_per_req = _per_request_cost(
        input_tokens, output_tokens, embed_tokens, gpt4o_in, gpt4o_out, embed_price
    )

    lines.append("## Cost Projections")
    lines.append("")

    # ---- Pricing Assumptions ----
    lines.append("### Pricing Assumptions")
    lines.append("| Parameter | Value | Source |")
    lines.append("|-----------|-------|--------|")
    lines.append(f"| GPT-4o input | ${gpt4o_in:.2f} / 1M tokens | Azure OpenAI pricing (2024) |")
    lines.append(f"| GPT-4o output | ${gpt4o_out:.2f} / 1M tokens | Azure OpenAI pricing (2024) |")
    lines.append(f"| text-embedding-3-large | ${embed_price:.2f} / 1M tokens | Azure OpenAI pricing (2024) |")
    lines.append(f"| Avg input tokens / request | {input_tokens:,} | Estimated from LangSmith traces |")
    lines.append(f"| Avg output tokens / request | {output_tokens:,} | Estimated from LangSmith traces |")
    lines.append(f"| Avg queries / user / day | {queries_day} | Product assumption |")
    lines.append(f"| Semantic Cache hit rate | {cache_rate:.0%} | Estimated from test runs |")
    lines.append(f"| Working days / month | {work_days} | Standard |")
    lines.append("")

    # ---- Per-Request Cost Breakdown ----
    lines.append("### Per-Request Cost Breakdown")
    lines.append("| Component | Tokens | Cost |")
    lines.append("|-----------|--------|------|")
    lines.append(f"| GPT-4o input | {input_tokens:,} | ${llm_in_cost:.4f} |")
    lines.append(f"| GPT-4o output | {output_tokens:,} | ${llm_out_cost:.4f} |")
    lines.append(f"| Embedding (query) | {embed_tokens:,} | ${embed_cost:.4f} |")
    lines.append(f"| **Total per request** | | **${total_per_req:.4f}** |")
    lines.append("")

    # ---- Monthly Cost by Scale ----
    lines.append("### Monthly Cost by Scale")
    lines.append("")

    scales = [
        ("HR Pilot", users_pilot),
        ("Company-wide", users_company),
    ]

    # Without cache
    lines.append("#### Without Semantic Cache")
    lines.append("| Scale | Users | Daily Queries | Monthly Queries | LLM Cost | Infra Cost | Total |")
    lines.append("|-------|-------|---------------|-----------------|----------|------------|-------|")
    for label, users in scales:
        if users <= 0 or queries_day <= 0:
            continue
        mq, _, llm_cost = _monthly_cost(users, queries_day, work_days, total_per_req, 0.0)
        lines.append(
            f"| {label} | {users:,} | {users * queries_day:,} | {mq:,} "
            f"| ${llm_cost:,.0f} | ${infra_cost:,.0f} | ${llm_cost + infra_cost:,.0f} |"
        )
    lines.append("")

    # With cache
    lines.append(f"#### With Semantic Cache ({cache_rate:.0%} hit rate)")
    lines.append("| Scale | Users | LLM Requests | LLM Cost | Cache Savings | Infra Cost | Total |")
    lines.append("|-------|-------|-------------|----------|---------------|------------|-------|")
    for label, users in scales:
        if users <= 0 or queries_day <= 0:
            continue
        mq_full, _, llm_full = _monthly_cost(users, queries_day, work_days, total_per_req, 0.0)
        _, llm_reqs, llm_cached = _monthly_cost(users, queries_day, work_days, total_per_req, cache_rate)
        savings = llm_full - llm_cached
        lines.append(
            f"| {label} | {users:,} | {llm_reqs:,} | ${llm_cached:,.0f} "
            f"| ${savings:,.0f} ({cache_rate:.0%}) | ${infra_cost:,.0f} "
            f"| ${llm_cached + infra_cost:,.0f} |"
        )
    lines.append("")

    # ---- Cache ROI Analysis ----
    lines.append("### Cache ROI Analysis")
    effective_per_req = total_per_req * (1 - cache_rate)
    _, _, company_savings_amount = _monthly_cost(users_company, queries_day, work_days, total_per_req, 0.0)
    _, _, company_cached_cost = _monthly_cost(users_company, queries_day, work_days, total_per_req, cache_rate)
    monthly_savings = company_savings_amount - company_cached_cost
    lines.append(f"- Cache hit -> cost per request: $0.0000 (embedding only, negligible)")
    lines.append(f"- Cache miss -> cost per request: ${total_per_req:.4f}")
    lines.append(f"- At {cache_rate:.0%} hit rate, effective cost per request: ${effective_per_req:.4f}")
    lines.append(f"- **Cache saves ~${monthly_savings:,.0f}/month at company-wide scale**")
    lines.append("")

    # ---- Scaling Decision Points ----
    lines.append("### Scaling Decision Points")
    lines.append("| Threshold | Action Required | Estimated Cost Impact |")
    lines.append("|-----------|----------------|----------------------|")
    lines.append("| > 200 concurrent users | Upgrade Container Apps (2 replicas) | +$50/month |")
    lines.append("| > 60 QPS on AI Search | Upgrade to Standard S2 or add replicas | +$500/month |")
    lines.append("| > 100K TPM on GPT-4o | Request higher quota or multi-region deployment | Quota change, no cost |")
    lines.append("| > 1000 concurrent users | Consider AKS for fine-grained scaling | +$200-500/month |")
    lines.append("")

    return "\n".join(lines)


def build_report(timestamp: str, cfg: argparse.Namespace | None = None) -> str:
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

    # ---- Cost Projections ----
    if cfg is not None:
        lines.append(build_cost_projections(cfg))

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate load test report from Locust CSVs")
    parser.add_argument("--timestamp", required=True, help="Timestamp prefix of the CSV files")

    # Cost projection overrides
    parser.add_argument("--avg-input-tokens", type=int, default=AVG_INPUT_TOKENS)
    parser.add_argument("--avg-output-tokens", type=int, default=AVG_OUTPUT_TOKENS)
    parser.add_argument("--avg-embedding-tokens", type=int, default=AVG_EMBEDDING_TOKENS)
    parser.add_argument("--gpt4o-input-price", type=float, default=GPT4O_INPUT_PRICE)
    parser.add_argument("--gpt4o-output-price", type=float, default=GPT4O_OUTPUT_PRICE)
    parser.add_argument("--embedding-price", type=float, default=EMBEDDING_PRICE)
    parser.add_argument("--azure-search-monthly", type=float, default=AZURE_SEARCH_MONTHLY)
    parser.add_argument("--redis-monthly", type=float, default=REDIS_MONTHLY)
    parser.add_argument("--container-apps-monthly", type=float, default=CONTAINER_APPS_MONTHLY)
    parser.add_argument("--avg-queries-per-user-per-day", type=int, default=AVG_QUERIES_PER_USER_PER_DAY)
    parser.add_argument("--cache-hit-rate", type=float, default=CACHE_HIT_RATE)
    parser.add_argument("--working-days-per-month", type=int, default=WORKING_DAYS_PER_MONTH)
    parser.add_argument("--users-pilot", type=int, default=200)
    parser.add_argument("--users-company", type=int, default=10000)

    args = parser.parse_args()

    # Validate
    if args.cache_hit_rate < 0 or args.cache_hit_rate > 1:
        parser.error("--cache-hit-rate must be between 0 and 1")
    for field in ("avg_input_tokens", "avg_output_tokens", "avg_embedding_tokens",
                  "avg_queries_per_user_per_day", "working_days_per_month",
                  "users_pilot", "users_company"):
        if getattr(args, field) <= 0:
            parser.error(f"--{field.replace('_', '-')} must be a positive integer")

    report = build_report(args.timestamp, cfg=args)

    output_path = REPORTS_DIR / f"loadtest_report_{args.timestamp}.md"
    output_path.write_text(report, encoding="utf-8")
    print(f"Report written to {output_path}")


if __name__ == "__main__":
    main()
