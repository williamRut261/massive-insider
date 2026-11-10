"""Recent individual Massive Form 4 purchase events for PAPER research.

No aggregating all-time buy totals into current signals. Uses first-party
filing dates, not estimated announcement times. At least one calendar day
of disclosure delay; never treats corporate forms as proven future returns.
"""
from __future__ import annotations

import json
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, timedelta

API_URL = "https://api.massive.com/stocks/filings/vX/form-4"
MAX_BYTES = 8_000_000
MAX_ROWS = 100_000
SYMBOL = re.compile(r"^[A-Z]{1,5}$")
ACCESSION = re.compile(r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$")
CIK = re.compile(r"^[0-9]{10}$")
PAGE_DELAY = 13.1


def check_url(url):
    p = urllib.parse.urlsplit(url)
    if (p.scheme != "https" or p.hostname != "api.massive.com"
            or p.port is not None or p.username or p.password
            or p.path != "/stocks/filings/vX/form-4" or p.fragment):
        raise ValueError("Unsafe Massive Form 4 URL")
    q = urllib.parse.parse_qs(p.query, keep_blank_values=True)
    if any("key" in k.lower() or "token" in k.lower() for k in q):
        raise ValueError("Never put API credentials in URLs")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Massive Form 4 redirect refused")


def read_form4(url, token):
    check_url(url)
    if not token:
        raise ValueError("MASSIVE_API_KEY required")
    req = urllib.request.Request(
        url, method="GET", headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/json",
            "User-Agent": "massive-insider-paper-automator/0.5"})
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=30) as res:
            raw = res.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as e:
        raise RuntimeError("Massive Form 4 API HTTP " + str(e.code)) from None
    except urllib.error.URLError:
        raise RuntimeError("Massive Form 4 API failed") from None
    if len(raw) > MAX_BYTES:
        raise ValueError("Massive Form 4 response too large")
    return json.loads(raw)


def fetch_recent_form4(as_of, *, days=14, token=None, request=read_form4,
                       pause=time.sleep, max_pages=50):
    end = date.fromisoformat(as_of)
    if not (1 <= days <= 30) or max_pages < 1:
        raise ValueError("Invalid Form 4 date window or pagination")
    if end > date.today():
        raise ValueError("Future Form 4 scan not supported")
    start = end - timedelta(days=days)
    if token is None:
        token = os.getenv("MASSIVE_API_KEY", "").strip()
    if not token:
        raise ValueError("Set rotated MASSIVE_API_KEY locally / GitHub secret")
    query = urllib.parse.urlencode({
        "filing_date.gte": start.isoformat(),
        "filing_date.lte": end.isoformat(),
        "transaction_code": "P",
        "form_type": "4",
        "sort": "filing_date.desc",
        "limit": "1000",
    })
    url = API_URL + "?" + query
    seen, rows = set(), []
    for i in range(max_pages):
        check_url(url)
        if url in seen:
            raise ValueError("Repeated Form 4 page")
        seen.add(url)
        if i:
            pause(PAGE_DELAY)
        response = request(url, token)
        if not isinstance(response, dict) or response.get("status") != "OK":
            raise ValueError("Unexpected Massive Form 4 response status")
        part = response.get("results")
        if not isinstance(part, list) or not all(isinstance(r, dict) for r in part):
            raise ValueError("Malformed Massive Form 4 results")
        if len(rows) + len(part) > MAX_ROWS:
            raise ValueError("Too many Form 4 records; narrow date window")
        rows.extend(part)
        nxt = response.get("next_url")
        if not nxt:
            return rows
        if not part:
            raise ValueError("Next Form 4 page after empty results")
        check_url(nxt)
        url = nxt
    raise RuntimeError("Incomplete Massive Form 4 pagination; no portfolio action")


def _money(value):
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) and x > 0 else None


def rank_current_purchases(rows, as_of, *, freshness_days=7,
                           min_purchase_usd=25_000):
    """Strict subset of officer/director *direct* open-market common shares.

    Every returned item is a research candidate, not independent SEC-document
    verification or forecast. Repeated reportings of identical shares get
    conservatively de-duplicated.
    """
    today = date.fromisoformat(as_of)
    if not isinstance(rows, list) or not (1 <= freshness_days <= 30):
        raise ValueError("Bad rows or freshness window")
    if min_purchase_usd <= 0:
        raise ValueError("Minimum insider purchase must be positive")
    grouped = defaultdict(list)
    seen = set()
    for r in rows:
        if not isinstance(r, dict):
            raise ValueError("Malformed Form 4 record")
        try:
            fd = date.fromisoformat(r["filing_date"])
            td = date.fromisoformat(r["transaction_date"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("Malformed filing or transaction date") from None
        if td > fd or fd > today:
            raise ValueError("Form 4 future-date or transaction-date inconsistency")
        # We use only public filings dated before today; no intraday timestamp.
        if not (today - timedelta(days=freshness_days) <= fd < today):
            continue
        if (r.get("transaction_code") != "P" or r.get("form_type") != "4"
                or r.get("record_type") != "transaction"
                or str(r.get("security_type", "")).lower().replace("-", "_") != "non_derivative"
                or r.get("transaction_acquired_disposed") != "A"
                or r.get("direct_or_indirect") != "D"
                or r.get("aff_10b5_one") is not False
                or not (r.get("is_director") is True or r.get("is_officer") is True)
                or r.get("equity_swap_involved") is True
                or "common stock" not in str(r.get("security_title", "")).lower()
                or any(word in str(r.get("security_title", "")).lower() for word in
                       ("option", "warrant", "right", "preferred", "restricted", "unit"))):
            continue
        issuer, owner = str(r.get("issuer_cik", "")), str(r.get("owner_cik", ""))
        acc = str(r.get("accession_number", ""))
        symbols = r.get("tickers")
        if (not CIK.fullmatch(issuer) or not CIK.fullmatch(owner)
                or not ACCESSION.fullmatch(acc) or not isinstance(symbols, list)):
            continue
        symbols = sorted({s for s in symbols if isinstance(s, str) and SYMBOL.fullmatch(s)})
        if len(symbols) != 1:  # ambiguous multi-class symbols are not automatic trades
            continue
        value = _money(r.get("transaction_value"))
        shares = _money(r.get("transaction_shares"))
        price = _money(r.get("transaction_price_per_share"))
        if value is None or shares is None or price is None:
            continue
        if abs(value - shares * price) > max(1.0, value * .02):
            continue
        # For related-owner duplication, same issuer, day, shares, price and
        # security title are one conservative economic trade.
        key = (issuer, td, shares, price, str(r["security_title"]).lower())
        if key in seen:
            continue
        seen.add(key)
        grouped[(issuer, symbols[0])].append({
            "issuer_cik": issuer, "symbol": symbols[0],
            "owner_cik": owner, "owner_name": r.get("owner_name", ""),
            "filing_date": fd.isoformat(), "transaction_date": td.isoformat(),
            "accession_number": acc,
            "value_usd": round(value, 2),
        })
    items = []
    for (issuer, symbol), txns in grouped.items():
        total = sum(x["value_usd"] for x in txns)
        if total < min_purchase_usd:
            continue
        newest = max(x["filing_date"] for x in txns)
        # A single issuer may have more than one listed equity class; this
        # is not proof of identity between the filing and an Alpaca asset.
        items.append({
            "issuer_cik": issuer, "symbol": symbol,
            "latest_filing_date": newest,
            "purchase_usd": round(total, 2),
            "reporting_owner_count_unverified_independence":
                len({x["owner_cik"] for x in txns}),
            "transactions": txns,
            "reference_accessions": sorted({x["accession_number"] for x in txns}),
            "note": ("Massive Form 4 P/A direct common-stock research only; "
                     "owners and current issuer/security eligibility not independently confirmed."),
        })
    # Treat ambiguous issuer-to-symbol mapping as unsafe; no auto orders.
    by_cik = defaultdict(set)
    for x in items:
        by_cik[x["issuer_cik"]].add(x["symbol"])
    items = [x for x in items if len(by_cik[x["issuer_cik"]]) == 1]
    return sorted(items, key=lambda x: (
        -x["reporting_owner_count_unverified_independence"],
        -x["purchase_usd"], -int(x["latest_filing_date"].replace("-", "")),
        x["symbol"]))
