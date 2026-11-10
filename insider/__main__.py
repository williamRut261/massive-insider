"""CLI for read-only insider scans. No brokerage commands exist."""
import argparse
import json
import math
from datetime import date, datetime, timezone
from pathlib import Path

from .market import fetch_marketwide, summarize_marketwide
from .scanner import fetch_pages, summarize


def save_json(pathname, data):
    path = Path(pathname)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Create JSON before writing; never output incomplete pagination as complete.
    text = json.dumps(data, indent=2, allow_nan=False) + "\n"
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="python -m insider",
        description="Read-only Massive Insider research; no broker, no orders")
    sub = parser.add_subparsers(dest="command", required=True)
    scan = sub.add_parser("scan")
    scan.add_argument("--input", help="Offline Massive JSON response or raw archive")
    scan.add_argument("--market-wide", action="store_true",
                      help="Scan ALL Massive reporting issuers; not vetted current US stocks")
    scan.add_argument("--from-date", default=None)
    scan.add_argument("--as-of", default=date.today().isoformat())
    scan.add_argument("--window-days", type=int, default=30)
    scan.add_argument("--min-buy-usd", type=float, default=250000.0)
    scan.add_argument("--raw-output", help="Save all raw activity records, for offline review")
    scan.add_argument("--output", default="reports/signals.json")
    args = parser.parse_args()

    if args.command != "scan":
        parser.error("Only scan is supported")
    if args.window_days < 1 or not math.isfinite(args.min_buy_usd) or args.min_buy_usd < 0:
        parser.error("Invalid window or purchase threshold")
    date.fromisoformat(args.as_of)
    if args.from_date:
        if date.fromisoformat(args.from_date) > date.fromisoformat(args.as_of):
            parser.error("from-date later than as-of")
    if args.market_wide and not args.from_date:
        parser.error("--market-wide requires --from-date to make historical scope explicit")
    if args.market_wide and not args.input and not args.raw_output:
        parser.error("--market-wide API capture requires --raw-output for auditable records")
    if args.raw_output and Path(args.raw_output).resolve() == Path(args.output).resolve():
        parser.error("--raw-output and --output must be different paths")

    if args.input:
        payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            parser.error("Input must be a Massive JSON response containing results")
        rows = payload["results"]
        complete = bool(payload.get("pagination_complete", not bool(payload.get("next_url"))))
    else:
        if not args.from_date:
            parser.error("--from-date is required for API fetch")
        rows = (fetch_marketwide(args.from_date, args.as_of)
                if args.market_wide else fetch_pages(args.from_date, args.as_of))
        complete = True

    if args.market_wide:
        summary = summarize_marketwide(
            rows, from_date=args.from_date, as_of=args.as_of,
            window_days=args.window_days, min_buy_usd=args.min_buy_usd)
    else:
        summary = summarize(
            rows, args.as_of, window_days=args.window_days,
            min_buy_usd=args.min_buy_usd)

    result = {
        "project": "Massive Insider",
        "version": "0.2.0",
        "mode": "READ_ONLY_RESEARCH_NO_ORDERS",
        "scope": ("ALL_MASSIVE_REPORTING_ISSUERS_NOT_VERIFIED_COMMON_STOCKS"
                  if args.market_wide else "30_STOCK_WATCHLIST"),
        "as_of": args.as_of,
        "from_date": args.from_date,
        "record_count": len(rows),
        "issuer_count": len(summary),
        "research_candidate_count": sum(bool(s["review"]) for s in summary),
        "pagination_complete": complete,
        "signals": summary,
        "warning": (
            "Issuer activity is NOT a validated list of current tradable US common stocks. "
            "A review flag is NOT a trade signal. Confirm SEC filings and historical availability "
            "before any backtest or PAPER execution.")
    }

    # Raw records are necessary for future true event-based backtesting. Never
    # upload them or API secrets into a PUBLIC repository automatically.
    if args.raw_output:
        raw = {
            "source": "Massive Insider Trading Activity",
            "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
            "scope": result["scope"],
            "from_date": args.from_date,
            "as_of": args.as_of,
            "pagination_complete": complete,
            "results": rows,
        }
        save_json(args.raw_output, raw)
    save_json(args.output, result)
    print(
        f"Saved {len(summary)} issuer summaries ({result['research_candidate_count']} "
        f"review candidates, {len(rows)} records) to {args.output}; "
        f"complete={complete}; NO ORDERS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
