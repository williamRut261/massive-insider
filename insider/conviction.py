"""v0.6 provisional conviction model from dated Massive individual Form 4 rows.

Numerical weights are hypotheses (40/30/20/10), NOT backtested alpha.
Only considers disclosed direct, nonscheduled officer/director common-stock
purchase candidates prepared by auto_signals.rank_current_purchases.
Ticker identity and shares outstanding must be cross-checked via Massive.
"""
from __future__ import annotations

import math
import time
from collections import defaultdict
from datetime import date, timedelta

from .auto_signals import CIK, _money
from .identity import ticker_reference, verified_current_common_stock

SCORING_WEIGHTS = {
    "insider_relative_increase": .40,
    "distinct_reporting_owners_proxy": .30,
    "purchases_relative_to_outstanding": .20,
    "recency_and_sales": .10,
}
MAX_RESEARCH_CANDIDATES = 20
REFERENCE_PACING_SECONDS = 13.1  # Conservative Massive plan rate-limit pacing.


def _safe_positive(v):
    try:
        x = float(v)
    except (ValueError, TypeError, OverflowError):
        return None
    return x if math.isfinite(x) and x > 0 else None


def _is_discretionary_sale(row, today, freshness_days):
    """Conservative sale-pressure proxy, not evidence of bearish motive."""
    if not isinstance(row, dict):
        raise ValueError("Malformed insider sale")
    try:
        filing = date.fromisoformat(row["filing_date"])
        transaction = date.fromisoformat(row["transaction_date"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("Malformed Form 4 sale dates") from None
    if transaction > filing or filing > today:
        raise ValueError("Sale dates require correction")
    if not today - timedelta(days=freshness_days) <= filing < today:
        return False
    title = str(row.get("security_title") or "").lower().replace("  ", " ")
    return (
        row.get("transaction_code") == "S"
        and row.get("form_type") == "4"
        and row.get("record_type") == "transaction"
        and str(row.get("security_type", "")).lower().replace("-", "_")
            == "non_derivative"
        and row.get("transaction_acquired_disposed") == "D"
        and row.get("direct_or_indirect") == "D"
        and row.get("aff_10b5_one") is False
        and (row.get("is_director") is True or row.get("is_officer") is True)
        and "common stock" in title
        and not any(t in title for t in
                    ("option", "warrant", "preferred", "restricted", "unit"))
        and CIK.fullmatch(str(row.get("issuer_cik") or "")) is not None
        and _safe_positive(row.get("transaction_value")) is not None
    )


def sale_pressure(sales, as_of, *, freshness_days=7):
    if not isinstance(sales, list):
        raise ValueError("Complete sale feed is required")
    today = date.fromisoformat(as_of)
    out = defaultdict(float)
    seen = set()
    for row in sales:
        if not _is_discretionary_sale(row, today, freshness_days):
            continue
        shares = _safe_positive(row.get("transaction_shares"))
        price = _safe_positive(row.get("transaction_price_per_share"))
        value = _safe_positive(row.get("transaction_value"))
        if shares is None or price is None or value is None:
            continue
        if abs(shares * price - value) > max(1., value * .02):
            continue
        # Conservative duplicate related-owner groups.
        key = (row["issuer_cik"], row["transaction_date"],
               shares, price, str(row.get("security_title")).lower())
        if key in seen:
            continue
        seen.add(key)
        out[row["issuer_cik"]] += value
    return out


def _personal_conviction(transactions):
    by_owner = defaultdict(list)
    for t in transactions:
        owner = t.get("owner_cik")
        shares = _safe_positive(t.get("shares"))
        after = _safe_positive(t.get("shares_owned_following_transaction"))
        if not owner or shares is None or after is None:
            return None
        by_owner[owner].append((shares, after))
    if not by_owner:
        return None
    relative = []
    for rows in by_owner.values():
        total = sum(shares for shares, after in rows)
        following = max(after for shares, after in rows)
        if total <= 0 or following < total:
            return None
        before = following - total
        # A newly established position has no meaningful ratio. Capped score.
        gain = total / before if before > 0 else float("inf")
        relative.append(min(1., gain / .50))  # 50% increase saturates
    return sum(relative) / len(relative)


def score_candidates(purchases, sales, as_of, *, reference_lookup=ticker_reference,
                     pause=time.sleep, research_limit=MAX_RESEARCH_CANDIDATES):
    """Return ranked current US common-stock opportunities with auditable factors.

    Only the top limited preliminary research candidates receive expensive
    ticker-identity lookups; the output is NOT a full market ranking.
    Raises on API/identity errors rather than silently liquidate a portfolio.
    """
    if not isinstance(purchases, list) or not 1 <= research_limit <= 40:
        raise ValueError("Invalid candidate list or research limit")
    pressure = sale_pressure(sales, as_of)
    today = date.fromisoformat(as_of)
    out = []
    # Preliminary order is owner count, dollars, and recency; all four-factor
    # scoring below is applied consistently to this bounded universe.
    for index, c in enumerate(purchases[:research_limit]):
        if not isinstance(c, dict):
            raise ValueError("Malformed Massive purchase candidate")
        if index:
            pause(REFERENCE_PACING_SECONDS)
        symbol = c["symbol"]
        details = reference_lookup(symbol)
        if not verified_current_common_stock(c, details):
            continue
        outstanding = (_safe_positive(details.get("share_class_shares_outstanding"))
                       or _safe_positive(details.get("weighted_shares_outstanding")))
        # No ticker share count => cannot compute all requested factors.
        if outstanding is None:
            continue
        transactions = c.get("transactions")
        if not isinstance(transactions, list) or not transactions:
            continue
        personal = _personal_conviction(transactions)
        if personal is None:
            continue
        owner_ids = {t.get("owner_cik") for t in transactions}
        if None in owner_ids or not owner_ids:
            continue
        cluster = min(1., len(owner_ids) / 3.)
        acquired_shares = sum(_safe_positive(t.get("shares")) or 0. for t in transactions)
        if acquired_shares <= 0:
            continue
        company = min(1., (acquired_shares / outstanding) / .01)
        last_filing = date.fromisoformat(c["latest_filing_date"])
        elapsed = (today - last_filing).days
        if not 1 <= elapsed <= 7:
            continue
        recency = max(0., 1. - elapsed / 10.)
        bought = _safe_positive(c.get("purchase_usd"))
        if bought is None:
            continue
        sold = pressure.get(c["issuer_cik"], 0.)
        net_buy_factor = max(0., 1. - sold / bought)
        # When discretionary insider sales meet/exceed qualified buys,
        # exclude a positive conviction signal entirely.
        if net_buy_factor <= 0:
            continue
        freshness_sales = (recency + net_buy_factor) / 2.
        raw = (.40 * personal + .30 * cluster +
               .20 * company + .10 * freshness_sales)
        score = round(raw * net_buy_factor, 8)
        if not 0 < score <= 1:
            continue
        out.append({
            "symbol": symbol, "issuer_cik": c["issuer_cik"],
            "latest_filing_date": c["latest_filing_date"],
            "reference_accessions": c["reference_accessions"],
            "score": score,
            "purchase_usd": round(bought, 2),
            "discretionary_direct_sales_usd": round(sold, 2),
            "components": {
                "insider_relative_increase": round(personal, 6),
                "distinct_owner_count_proxy": len(owner_ids),
                "distinct_reporting_owners_proxy": round(cluster, 6),
                "purchases_relative_to_outstanding": round(company, 6),
                "purchased_shares_pct_outstanding": round(
                    100. * acquired_shares / outstanding, 6),
                "filing_age_days": elapsed,
                "recency_and_sales": round(freshness_sales, 6),
                "discretionary_sale_to_purchase_ratio": round(sold / bought, 6),
                "sale_penalty_multiplier": round(net_buy_factor, 6),
            },
        })
    return sorted(out, key=lambda r: (-r["score"], r["symbol"]))
