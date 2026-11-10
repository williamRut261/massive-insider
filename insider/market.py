"""Market-wide issuer activity collection and research-only triage.

This endpoint describes SEC reporting issuers, NOT a verified inventory of
currently listed, US common stocks. All outputs are non-tradable research flags.
"""
from __future__ import annotations

import os
import time
import urllib.parse
from collections import defaultdict
from datetime import date, timedelta

from .scanner import ENDPOINT, _amount, check_url, read_api

PAGE_DELAY_SECONDS = 13.1  # Conservative pacing for a 5-requests/minute plan.
MAX_RECORDS = 500_000


def fetch_marketwide(start: str, as_of: str, *, token: str | None = None,
                     request=read_api, pause=time.sleep, max_pages=100,
                     limit=5000) -> list[dict]:
    """Download all pages for a date window, or raise without returning partial data."""
    first, last = date.fromisoformat(start), date.fromisoformat(as_of)
    if first > last:
        raise ValueError("from-date later than as-of")
    if not (1 <= limit <= 10000) or max_pages < 1:
        raise ValueError("Invalid pagination settings")
    if token is None:
        token = os.environ.get("MASSIVE_API_KEY", "").strip()
    if not token:
        raise ValueError("Set a rotated MASSIVE_API_KEY environment variable")

    query = urllib.parse.urlencode({
        "transaction_date.gte": start,
        "transaction_date.lte": as_of,
        "sort": "transaction_date.desc",
        "limit": str(limit),
    })
    # Deliberately NO ticker filter. Never add key to the URL.
    url = ENDPOINT + "?" + query
    seen = set()
    collected: list[dict] = []
    for page in range(max_pages):
        check_url(url)
        if url in seen:
            raise ValueError("Repeated market-wide pagination URL")
        seen.add(url)
        if page:
            pause(PAGE_DELAY_SECONDS)
        payload = request(url, token)
        if not isinstance(payload, dict) or payload.get("status") != "OK":
            raise ValueError("Unexpected Massive response status")
        part = payload.get("results")
        if not isinstance(part, list) or not all(isinstance(row, dict) for row in part):
            raise ValueError("Missing or malformed results")
        if len(part) + len(collected) > MAX_RECORDS:
            raise RuntimeError("Exceeded in-memory record limit; split date windows")
        collected.extend(part)
        next_url = payload.get("next_url")
        if not next_url:
            return collected
        if not part:
            raise ValueError("Next page supplied after empty results")
        check_url(next_url)
        url = next_url
    raise RuntimeError("Pagination incomplete at max-pages; no results returned")


def summarize_marketwide(rows: list[dict], *, from_date: str, as_of: str,
                         window_days=90, min_buy_usd=250_000.0) -> list[dict]:
    """Summarize issuer/day aggregates; rank only for SEC filing research.

    'Purchases not marked planned' are NOT confirmed discretionary purchases.
    Purchaser counts are unique within a single issuer/day, not across dates.
    """
    first, last = date.fromisoformat(from_date), date.fromisoformat(as_of)
    if first > last or not isinstance(window_days, int) or window_days < 1:
        raise ValueError("Invalid market-wide date window")
    if min_buy_usd < 0:
        raise ValueError("min_buy_usd cannot be negative")
    cutoff = max(first, last - timedelta(days=window_days))
    grouped: dict[str, dict] = {}
    seen = set()

    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Invalid issuer activity record")
        cik = str(row.get("issuer_cik") or "")
        if len(cik) != 10 or not cik.isdigit():
            raise ValueError("Missing or invalid 10-digit issuer CIK")
        txn = date.fromisoformat(row["transaction_date"])
        filing = date.fromisoformat(row["last_filing_date"])
        if txn > filing:
            raise ValueError("Transaction date after filing date")
        if not (first <= txn <= last):
            raise ValueError("Record outside requested transaction date window")
        key = (cik, txn)
        if key in seen:
            raise ValueError("Duplicate issuer/day across pages; refusing double count")
        seen.add(key)

        buy, planned_buy = _amount(row, "purchase_value"), _amount(row, "planned_purchase_value")
        sell, planned_sell = _amount(row, "sale_value"), _amount(row, "planned_sale_value")
        if planned_buy > buy + .01 or planned_sell > sell + .01:
            raise ValueError("Planned value exceeds total value")
        purchasers = row.get("purchaser_count", 0)
        if type(purchasers) is not int or purchasers < 0:
            raise ValueError("Invalid purchaser_count")
        tickers = row.get("tickers") or []
        if not isinstance(tickers, list) or any(not isinstance(t, str) for t in tickers):
            raise ValueError("Malformed ticker aliases")
        issuer_name = row.get("issuer_name")
        if issuer_name is not None and not isinstance(issuer_name, str):
            raise ValueError("Malformed issuer name")
        if filing > last or filing < cutoff:
            continue  # Refuse look-ahead; use the latest filing for conservative as-of.
        if cik not in grouped:
            grouped[cik] = {
                "issuer_cik": cik,
                "issuer_name": issuer_name or "",
                "historical_tickers": set(),
                "latest_filing_date": filing,
                "records": 0,
                "purchase_usd": 0.0,
                "planned_purchase_usd": 0.0,
                "sale_usd": 0.0,
                "planned_sale_usd": 0.0,
                "max_distinct_purchasers_on_one_day": 0,
                "same_day_multiple_purchasers": False,
            }
        item = grouped[cik]
        item["historical_tickers"].update(t for t in tickers if t)
        item["latest_filing_date"] = max(item["latest_filing_date"], filing)
        item["records"] += 1
        item["purchase_usd"] += buy
        item["planned_purchase_usd"] += planned_buy
        item["sale_usd"] += sell
        item["planned_sale_usd"] += planned_sell
        item["max_distinct_purchasers_on_one_day"] = max(
            item["max_distinct_purchasers_on_one_day"], purchasers)
        item["same_day_multiple_purchasers"] |= purchasers >= 2

    output = []
    for item in grouped.values():
        unplanned = max(0.0, item["purchase_usd"] - item["planned_purchase_usd"])
        item["purchase_not_marked_planned_usd"] = round(unplanned, 2)
        item["net_purchase_usd"] = round(item["purchase_usd"] - item["sale_usd"], 2)
        for key in ("purchase_usd", "planned_purchase_usd", "sale_usd", "planned_sale_usd"):
            item[key] = round(item[key], 2)
        item["historical_tickers"] = sorted(item["historical_tickers"])
        item["latest_filing_date"] = item["latest_filing_date"].isoformat()
        item["review"] = unplanned >= min_buy_usd
        item["verified_current_us_listed_common_stock"] = False
        item["note"] = (
            "Research only. Historical symbols may be stale/OTC/ADR/other classes; "
            "validate SEC filings, current listing, trade type and issuer identity."
        )
        output.append(item)
    return sorted(
        output,
        key=lambda x: (-x["purchase_not_marked_planned_usd"], x["issuer_cik"])
    )
