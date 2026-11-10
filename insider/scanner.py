"""Read-only Massive insider-activity capture and conservative signal triage."""
from __future__ import annotations

import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, timedelta

UNIVERSE = tuple("TSLA NVDA AMD ANET VRT PLTR AVGO APP ISRG CRWD CRWV NBIS ALAB CRDO ASTS RKLB TEM SYM IREN SOFI IONQ RGTI QBTS OKLO SMR ACHR JOBY AUR RXRX VKTX".split())
ENDPOINT = "https://api.massive.com/stocks/insiders/vX/activity"
MAX_BYTES = 8_000_000


def check_url(url: str) -> None:
    u = urllib.parse.urlsplit(url)
    if (u.scheme != "https" or u.hostname != "api.massive.com"
            or u.port is not None or u.username or u.password
            or u.path != "/stocks/insiders/vX/activity"
            or u.fragment or "apikey" in {k.lower() for k in urllib.parse.parse_qs(u.query)}):
        raise ValueError("Refused unsafe or unexpected Massive URL")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Refused redirect to protect API credentials")


def read_api(url: str, token: str) -> dict:
    """Perform GET only; never reveal authentication in URL or exceptions."""
    check_url(url)
    if not token:
        raise ValueError("MASSIVE_API_KEY is required")
    req = urllib.request.Request(
        url, method="GET",
        headers={"Authorization": "Bearer " + token,
                 "Accept": "application/json",
                 "User-Agent": "massive-insider-research/0.1"})
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=30) as res:
            body = res.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES:
            raise ValueError("Massive response exceeded size limit")
        return json.loads(body)
    except urllib.error.HTTPError as exc:
        raise RuntimeError("Massive API HTTP " + str(exc.code)) from None
    except urllib.error.URLError:
        raise RuntimeError("Massive API connection failed") from None


def fetch_pages(start: str, as_of: str, *, symbols=UNIVERSE,
                token: str | None = None, request=read_api,
                pause=time.sleep, max_pages=100) -> list[dict]:
    """Retrieve every response page or fail; conservative 13.1s pacing."""
    first, last = date.fromisoformat(start), date.fromisoformat(as_of)
    if first > last:
        raise ValueError("from-date later than as-of")
    symbols = tuple(symbols)
    if not symbols or len(set(symbols)) != len(symbols) or any(s not in UNIVERSE for s in symbols):
        raise ValueError("Invalid or duplicate ticker in research universe")
    if token is None:
        token = os.getenv("MASSIVE_API_KEY", "").strip()
    if not token:
        raise ValueError("Set a rotated MASSIVE_API_KEY environment variable")
    params = urllib.parse.urlencode({
        "tickers.any_of": ",".join(symbols),
        "transaction_date.gte": start,
        "transaction_date.lte": as_of,
        "sort": "transaction_date.desc",
        "limit": "1000"})
    url = ENDPOINT + "?" + params
    seen = set()
    rows = []
    for page in range(max_pages):
        check_url(url)
        if url in seen:
            raise ValueError("Repeated pagination URL")
        seen.add(url)
        if page:
            pause(13.1)
        payload = request(url, token)
        if not isinstance(payload, dict) or payload.get("status") != "OK":
            raise ValueError("Unexpected Massive response status")
        part = payload.get("results")
        if not isinstance(part, list) or not all(isinstance(r, dict) for r in part):
            raise ValueError("Missing or malformed results")
        rows.extend(part)
        nxt = payload.get("next_url")
        if not nxt:
            return rows
        check_url(nxt)
        url = nxt
    raise RuntimeError("Pagination exceeded maximum; discarded incomplete scan")


def _amount(row: dict, field: str) -> float:
    try:
        value = float(row.get(field, 0))
    except (ValueError, TypeError):
        raise ValueError("Invalid value in " + field) from None
    if not math.isfinite(value) or value < 0:
        raise ValueError("Non-finite or negative " + field)
    return value


def summarize(rows: list[dict], as_of: str, *, window_days=30,
              min_buy_usd=250_000.0) -> list[dict]:
    """Flag potentially interesting purchases; NOT a trading recommendation."""
    cutoff = date.fromisoformat(as_of) - timedelta(days=window_days)
    as_of_day = date.fromisoformat(as_of)
    grouped = defaultdict(list)
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Invalid activity row")
        tickers = row.get("tickers") or []
        if not isinstance(tickers, list):
            raise ValueError("Invalid tickers array")
        matched = [t for t in tickers if t in UNIVERSE]
        if not matched:
            continue
        cik = str(row.get("issuer_cik") or "")
        if not cik.isdigit():
            raise ValueError("Missing or invalid issuer CIK")
        txn = date.fromisoformat(row["transaction_date"])
        filing = date.fromisoformat(row["last_filing_date"])
        if txn > filing:
            raise ValueError("Transaction date after filing date")
        if filing > as_of_day or filing < cutoff:
            continue  # No use of future filings, even if transaction was earlier.
        key = (cik, txn.isoformat())
        if key in seen:
            raise ValueError("Duplicate issuer/day; refusing double-counted totals")
        seen.add(key)
        buy, planned_buy = _amount(row, "purchase_value"), _amount(row, "planned_purchase_value")
        sell, planned_sell = _amount(row, "sale_value"), _amount(row, "planned_sale_value")
        if planned_buy > buy + 0.01 or planned_sell > sell + 0.01:
            raise ValueError("Planned value exceeds reported total")
        purchasers = row.get("purchaser_count", 0)
        if not isinstance(purchasers, int) or purchasers < 0:
            raise ValueError("Invalid purchaser_count")
        grouped[cik].append((matched[0], filing, buy, planned_buy, sell, planned_sell, purchasers))
    output = []
    for cik, group in grouped.items():
        buys = sum(x[2] for x in group)
        planned_buys = sum(x[3] for x in group)
        sales = sum(x[4] for x in group)
        planned_sales = sum(x[5] for x in group)
        unplanned = max(0.0, buys - planned_buys)
        output.append({
            "ticker": group[0][0], "issuer_cik": cik,
            "latest_filing_date": max(x[1] for x in group).isoformat(),
            "records": len(group),
            "purchase_usd": round(buys, 2),
            "planned_purchase_usd": round(planned_buys, 2),
            "purchase_not_marked_planned_usd": round(unplanned, 2),
            "sale_usd": round(sales, 2),
            "planned_sale_usd": round(planned_sales, 2),
            "net_purchase_usd": round(buys - sales, 2),
            "max_distinct_purchasers_on_one_day": max(x[6] for x in group),
            "same_day_multiple_purchasers": any(x[6] >= 2 for x in group),
            "review": unplanned >= min_buy_usd,
            "note": "Research flag only; validate individual SEC filings before interpreting buyers or trading."
        })
    return sorted(output, key=lambda r: (-r["purchase_not_marked_planned_usd"], r["ticker"]))
