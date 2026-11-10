"""CLI for read-only insider scans. No brokerage commands exist."""
import argparse
import json
from pathlib import Path
from datetime import date

from .scanner import fetch_pages, summarize


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m insider",
                                     description="Read-only Massive insider research; never places orders")
    sub = parser.add_subparsers(dest="command", required=True)
    scan = sub.add_parser("scan")
    scan.add_argument("--input", help="Saved Massive API JSON response (offline)")
    scan.add_argument("--from-date", default=None)
    scan.add_argument("--as-of", default=date.today().isoformat())
    scan.add_argument("--window-days", type=int, default=30)
    scan.add_argument("--min-buy-usd", type=float, default=250000.0)
    scan.add_argument("--output", default="reports/signals.json")
    args = parser.parse_args()
    if args.command != "scan":
        raise ValueError("Unsupported command")
    if args.window_days < 1 or args.min_buy_usd < 0:
        parser.error("Invalid scan parameters")
    if args.input:
        payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            parser.error("Input must be a Massive JSON response with results")
        rows = payload["results"]
        complete = not bool(payload.get("next_url"))
    else:
        if not args.from_date:
            parser.error("--from-date is required for API fetch")
        rows = fetch_pages(args.from_date, args.as_of)
        complete = True
    summary = summarize(rows, args.as_of, window_days=args.window_days,
                        min_buy_usd=args.min_buy_usd)
    result = {
        "project": "Massive Insider", "mode": "READ_ONLY_RESEARCH_NO_ORDERS",
        "as_of": args.as_of, "record_count": len(rows),
        "pagination_complete": complete, "signals": summary,
        "warning": "Not a validated return forecast, brokerage order or investment recommendation."
    }
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Saved {len(summary)} issuer summaries to {path}; complete={complete}; NO ORDERS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
