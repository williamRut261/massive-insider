"""Guarded automated allocation for a dedicated $1k Alpaca PAPER account.

This is an *entry-only* portfolio allocator, NOT yet an exit/rebalancing
engine. It preserves the existing GME pilot position, never trades LIVE,
does not borrow or short, and cannot place an order without explicit
persistent operator opt-in and broker account ID verification.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import date, datetime, time, timezone
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from zoneinfo import ZoneInfo

from .paper import PaperClient, usd, validate_symbol
from .identity import ticker_reference, verified_current_common_stock

MAX_STOCKS = 10
STRATEGY_CAP = Decimal("1000")
ORDER_USD = Decimal("100")
MAX_POSITION_USD = Decimal("200")
MIN_ORDER_USD = Decimal("25")
CASH_RESERVE = Decimal("20")
MAX_DAILY_NEW_ORDERS = 2
MAX_EQUITY_ALLOWED = Decimal("1500")
AUTOMATION_OPT_IN = "I-ENABLE-PAPER-AUTO"
AUTO_ID = re.compile(r"^mi-auto-[0-9a-f]{24}$")
ALL_ORDERS_PATH = "/v2/orders?status=all&limit=500"
OPEN_ORDERS_PATH = "/v2/orders?status=open&limit=500"
EASTERN = ZoneInfo("America/New_York")


def money(value):
    return usd(value).quantize(Decimal(".01"), rounding=ROUND_DOWN)


def _now():
    return datetime.now(timezone.utc)


def account_snapshot(client):
    """Broker is authority for current positions, exposure, and all orders."""
    return {
        "account": client.request("GET", "/v2/account"),
        "positions": client.request("GET", "/v2/positions"),
        "open_orders": client.request("GET", OPEN_ORDERS_PATH),
        "recent_orders": client.request("GET", ALL_ORDERS_PATH),
        "clock": client.request("GET", "/v2/clock"),
    }


def validate_snapshot(s, expected_account):
    if not expected_account or not re.fullmatch(r"[A-Za-z0-9-]{8,64}", expected_account):
        raise ValueError("MI_PAPER_ACCOUNT_ID must identify the dedicated PAPER account")
    a = s.get("account")
    pos, pending, history, clock = (
        s.get("positions"), s.get("open_orders"), s.get("recent_orders"), s.get("clock"))
    if not isinstance(a, dict) or a.get("id") != expected_account:
        raise ValueError("PAPER account ID mismatch; fail closed")
    if (a.get("status") != "ACTIVE" or any(
        a.get(key, False) for key in
        ("trading_blocked", "account_blocked", "trade_suspended_by_user")
    )):
        raise ValueError("PAPER account blocked or inactive")
    if (not isinstance(pos, list) or not isinstance(pending, list)
            or not isinstance(history, list) or not isinstance(clock, dict)
            or len(pending) >= 500 or len(history) >= 500):
        raise ValueError("Broker order/position snapshot incomplete")
    cash, equity = money(a.get("cash")), money(a.get("equity"))
    if equity <= 0 or equity > MAX_EQUITY_ALLOWED:
        raise ValueError("Not a dedicated small $1k PAPER account")
    if len(pos) > MAX_STOCKS:
        raise ValueError("Exceeds 10 PAPER position limit; cannot open more")
    occupied = set()
    total_market_value = Decimal("0")
    holdings = []
    for p in pos:
        if not isinstance(p, dict):
            raise ValueError("Malformed broker position")
        symbol = validate_symbol(p.get("symbol"))
        if symbol in occupied:
            raise ValueError("Duplicate stock position in broker state")
        occupied.add(symbol)
        if (p.get("asset_class") != "us_equity"
                or p.get("side") != "long"):
            raise ValueError("Unexpected non-equity/short position: abort automation")
        value = money(p.get("market_value"))
        total_market_value += value
        holdings.append({"symbol": symbol, "market_value_usd": str(value),
                         "qty": str(p.get("qty", ""))})
    if any(not isinstance(o, dict) for o in pending + history):
        raise ValueError("Malformed broker order data")
    if pending:
        # No new orders while ANY paper order is outstanding, including old GME.
        return {
            "account_id": expected_account, "cash": cash, "equity": equity,
            "holdings": holdings, "held": occupied,
            "market_value": total_market_value,
            "open_orders": pending, "recent_orders": history,
            "clock": clock, "can_buy": False,
            "block_reason": "Unresolved PAPER order exists",
        }
    return {
        "account_id": expected_account, "cash": cash, "equity": equity,
        "holdings": holdings, "held": occupied,
        "market_value": total_market_value,
        "open_orders": pending, "recent_orders": history,
        "clock": clock, "can_buy": True, "block_reason": None,
    }


def order_id(account_id, candidate):
    # Same filing event and same account => same broker client order ID.
    # Deterministic across restarts and workflow runners, even without files.
    accessions = candidate.get("reference_accessions") or []
    if not accessions:
        raise ValueError("Cannot buy without SEC reference accession")
    payload = "|".join((account_id, candidate["issuer_cik"],
                        candidate["symbol"], ",".join(sorted(accessions))))
    return "mi-auto-" + hashlib.sha256(payload.encode()).hexdigest()[:24]


def trading_window(now, clock):
    local = now.astimezone(EASTERN)
    return (local.weekday() < 5 and clock.get("is_open") is True
            and time(9, 45) <= local.time().replace(tzinfo=None) <= time(15, 40))


def proposed_order(client, s, candidates, *, expected_account, now=None,
                   reference_lookup=ticker_reference):
    """Make a READ-ONLY proposed order using real positions and ALL broker orders."""
    now = now or _now()
    if now.tzinfo is None:
        raise ValueError("Time must include timezone")
    x = validate_snapshot(s, expected_account)
    today = now.astimezone(EASTERN).date().isoformat()
    recent = x["recent_orders"]
    daily = sum(
        1 for o in recent
        if str(o.get("client_order_id", "")).startswith("mi-auto-")
        and str(o.get("submitted_at", ""))[:10] == now.date().isoformat()
    )
    basic = {
        "version": "0.5.0",
        "mode": "ALPACA_PAPER_MANAGED_READ_ONLY_PLAN",
        "as_of": today,
        "account_id": expected_account,
        "equity_usd": str(x["equity"]),
        "cash_usd": str(x["cash"]),
        "held_positions": x["holdings"],
        "position_count": len(x["held"]),
        "daily_automation_orders": daily,
        "pending_broker_orders": len(x["open_orders"]),
        "proposed_order": None,
        "note": ("Automated PAPER entries only; pre-existing GME is retained. "
                 "No auto sells, alpha guarantee or LIVE access."),
    }
    block = None
    if not x["can_buy"]:
        block = x["block_reason"]
    elif not trading_window(now, x["clock"]):
        block = "Outside eligible regular market hours or broker clock closed"
    elif daily >= MAX_DAILY_NEW_ORDERS:
        block = "Maximum two automation orders already submitted today"
    elif len(x["held"]) >= MAX_STOCKS:
        block = "Maximum ten current positions"
    elif x["equity"] < STRATEGY_CAP * Decimal(".80"):
        block = "Equity below 80% of initial $1k; new buying suspended"
    elif x["cash"] <= CASH_RESERVE:
        block = "Insufficient cash reserve"
    elif x["market_value"] >= STRATEGY_CAP:
        block = "Already at $1k gross stock exposure"
    if block:
        basic["block_reason"] = block
        return basic
    if not isinstance(candidates, list):
        raise ValueError("Malformed latest insider candidates")
    for c in candidates:
        if not isinstance(c, dict) or not isinstance(c.get("symbol"), str):
            raise ValueError("Malformed candidate")
        symbol = validate_symbol(c["symbol"])
        if symbol in x["held"]:
            continue  # The $25 existing GME position is not bought again.
        client_id = order_id(expected_account, c)
        if any(o.get("client_order_id") == client_id for o in recent):
            continue
        # Also refuse any older order for the symbol in recent 500 entries:
        # no averaging up/down of a previously selected paper investment.
        if any(o.get("symbol") == symbol and
               str(o.get("client_order_id", "")).startswith("mi-auto-")
               for o in recent):
            continue
        # A ticker alias is not an issuer identity. Require Massive ticker
        # overview to confirm the SEC issuer CIK and current US common shares.
        details = reference_lookup(symbol)
        if not verified_current_common_stock(c, details):
            continue
        asset = client.request("GET", "/v2/assets/" + symbol)
        if (not isinstance(asset, dict) or asset.get("symbol") != symbol
                or asset.get("class") != "us_equity"
                or asset.get("status") != "active"
                or asset.get("tradable") is not True
                or asset.get("fractionable") is not True
                or asset.get("exchange") not in {"NYSE", "NASDAQ", "AMEX"}):
            continue
        cap = min(
            ORDER_USD, MAX_POSITION_USD, x["equity"] * Decimal(".20"),
            x["cash"] - CASH_RESERVE, STRATEGY_CAP - x["market_value"])
        amount = cap.quantize(Decimal(".01"), rounding=ROUND_DOWN)
        if amount < MIN_ORDER_USD:
            basic["block_reason"] = "Cannot buy minimum $25 within all caps"
            return basic
        basic["proposed_order"] = {
            "symbol": symbol, "issuer_cik": c["issuer_cik"],
            "notional_usd": str(amount), "side": "buy",
            "type": "market", "time_in_force": "day",
            "client_order_id": client_id,
            "reference_accessions": c["reference_accessions"],
            "filing_date": c["latest_filing_date"],
            "source": "Massive individual Form 4 P/A direct officer/director common stock",
        }
        break
    if basic["proposed_order"] is None:
        basic["block_reason"] = "No new eligible fresh Form 4 candidates"
    return basic


def run_one_cycle(client, candidates, *, expected_account, execute=False,
                  activation="", now=None, intent_dir="data/automation_intents",
                  reference_lookup=ticker_reference):
    """Refresh broker state immediately before ANY optional PAPER submission."""
    now = now or _now()
    if execute and activation != AUTOMATION_OPT_IN:
        raise ValueError("Explicit MI_PAPER_AUTO_ENABLED activation required")
    snapshot = account_snapshot(client)
    plan = proposed_order(client, snapshot, candidates,
                          expected_account=expected_account, now=now,
                          reference_lookup=reference_lookup)
    if not execute or not plan["proposed_order"]:
        return {"status": ("READ_ONLY_PLAN" if not execute else
                           "NO_ELIGIBLE_PAPER_ORDER"), "plan": plan}
    # Re-read and re-plan to stop a new/filled order in between the two passes.
    later = proposed_order(client, account_snapshot(client), candidates,
                           expected_account=expected_account,
                           now=(now if now is not None else _now()),
                           reference_lookup=reference_lookup)
    if later.get("proposed_order") != plan["proposed_order"]:
        return {"status": "ABORTED_BROKER_STATE_CHANGED", "plan": later}
    order = plan["proposed_order"]
    cid = order["client_order_id"]
    if not AUTO_ID.fullmatch(cid):
        raise ValueError("Invalid automatic PAPER client order ID")
    # Durable one-attempt marker in a local installation. Do not retry a
    # possibly-accepted POST after timeouts or crashes; inspect broker orders.
    folder = Path(intent_dir)
    folder.mkdir(parents=True, exist_ok=True)
    marker = folder / (cid + ".json")
    try:
        with marker.open("x", encoding="utf-8") as f:
            json.dump({"client_order_id": cid, "paper_account_id": expected_account,
                       "symbol": order["symbol"], "notional": order["notional_usd"],
                       "attempted_at_utc": _now().isoformat()}, f)
            f.flush()
            os.fsync(f.fileno())
    except FileExistsError:
        return {"status": "ALREADY_ATTEMPTED_NO_RETRY", "plan": plan}
    # One PAPER POST, no automatic retry. New order ID is fixed per filing
    # event, so a workflow restart cannot silently create duplicates.
    receipt = client.submit({
        "symbol": order["symbol"],
        "notional": order["notional_usd"],
        "side": "buy", "type": "market", "time_in_force": "day",
        "extended_hours": False,
        "client_order_id": cid,
    })
    if (not isinstance(receipt, dict) or
            receipt.get("client_order_id") != cid or
            receipt.get("symbol") != order["symbol"]):
        raise RuntimeError("Ambiguous PAPER acknowledgment; no retry allowed")
    return {"status": "PAPER_SUBMITTED_NOT_A_FILL",
            "paper_broker_order": {
                "id": receipt.get("id"),
                "client_order_id": cid,
                "symbol": order["symbol"],
                "status": receipt.get("status"),
                "filled_qty": receipt.get("filled_qty"),
            },
            "plan": plan}
