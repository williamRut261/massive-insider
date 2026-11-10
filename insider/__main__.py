"""CLI for read-only insider scans. No brokerage commands exist."""
import argparse
import json
import math
from datetime import date, datetime, timezone
from pathlib import Path

from .filings import load_verified_filings, rank_candidates
from .market import fetch_marketwide, summarize_marketwide
from .paper import PaperClient, make_preview, paper_submit
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
    verify = sub.add_parser(
        "verify", help="Rank market-wide candidates using supplied SEC Form 4 XML")
    verify.add_argument("--input", required=True,
                        help="Complete v0.2 market-wide signals JSON")
    verify.add_argument("--manifest", help="JSON listing exact SEC XML URLs")
    verify.add_argument("--top", type=int, default=50)
    verify.add_argument("--output", default="reports/verified_shortlist.json")
    status = sub.add_parser("paper-status", help="Read-only isolated Alpaca PAPER account status")
    preview = sub.add_parser("paper-preview", help="Read-only preview of ONE capped PAPER buy")
    preview.add_argument("--input", required=True, help="SEC v0.3 verification report")
    preview.add_argument("--symbol", required=True)
    preview.add_argument("--notional", default="25.00")
    preview.add_argument("--output", default="reports/paper_preview.json")
    submit = sub.add_parser("paper-submit", help="ONE Alpaca PAPER order with explicit approval")
    submit.add_argument("--plan", required=True)
    submit.add_argument("--input", required=True, help="Original SEC-verified v0.3 research JSON")
    submit.add_argument("--expected-account", required=True)
    submit.add_argument("--confirm", required=True)
    submit.add_argument("--output", default="reports/paper_receipt.json")
    args = parser.parse_args()

    if args.command == "paper-status":
        acct = PaperClient().request("GET", "/v2/account")
        print("PAPER ONLY account ID:", acct.get("id"))
        print("Status:", acct.get("status"), "Cash:", acct.get("cash"),
              "Equity:", acct.get("equity"), "Trading blocked:", acct.get("trading_blocked"))
        print("NO ORDERS")
        return 0
    if args.command == "paper-preview":
        report = json.loads(Path(args.input).read_text(encoding="utf-8"))
        plan = make_preview(report, args.symbol, args.notional, PaperClient())
        save_json(args.output, plan)
        print("Read-only PAPER preview:", args.output)
        print("Account ID:", plan["paper_account_id"], "Symbol:", plan["symbol"],
              "Notional:", plan["notional_usd"], "Market open:", plan["market_open_at_preview"])
        print("Expires:", plan["expires_at"], "NO ORDERS")
        return 0
    if args.command == "paper-submit":
        plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
        research = json.loads(Path(args.input).read_text(encoding="utf-8"))
        result = paper_submit(plan, PaperClient(), confirm=args.confirm,
                              expected_account=args.expected_account, report=research)
        save_json(args.output, result)
        print("PAPER broker response:", result["status"], "saved:", args.output)
        print("Inspect order status at the Alpaca PAPER dashboard; submitted != filled")
        return 0

    if args.command == "verify":
        scan_doc = json.loads(Path(args.input).read_text(encoding="utf-8"))
        manifest = (json.loads(Path(args.manifest).read_text(encoding="utf-8"))
                    if args.manifest else {"filings": []})
        filings = load_verified_filings(manifest)
        result = rank_candidates(
            scan_doc, filings,
            related_owner_groups=manifest.get("related_owner_groups", []),
            top_n=args.top)
        save_json(args.output, result)
        print(
            f"Reviewed {result['scanned_candidate_count']} Massive candidates, "
            f"{result['sec_filing_count_supplied']} SEC filings supplied, "
            f"{result['sec_verified_candidate_count']} SEC purchase candidates; "
            f"saved {args.output}; NO ORDERS")
        return 0
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
