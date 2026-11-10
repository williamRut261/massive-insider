"""v0.6 PAPER-only target-weight rebalance planner and one-order executor.

Target: conviction-weighted allocation of 99.5% of spendable *strategy* equity.
No per-company concentration cap; one eligible company can receive 100% of
the strategy allocation. The original GME pilot/unknown manual holdings
are deliberately locked and excluded from the manager's investable budget.

Only one strictly guarded simulated order per invocation, sell before buy,
with a deterministic per-symbol/per-side/day client ID to avoid repeat orders
on ephemeral scheduled GitHub Actions runners.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from pathlib import Path

from .paper import usd, validate_symbol
from .portfolio import (
    AUTOMATION_OPT_IN, EASTERN, MAX_STOCKS, account_snapshot,
    money, trading_window, validate_snapshot,
)

BUFFER_RATE = Decimal(".005")
MIN_TRADE = Decimal("15")
REBALANCE_BAND = Decimal(".025")  # Avoid frequent churn for small price moves.
MAX_AUTOMATED_ORDERS_DAILY = 10
V06_ORDER_RE = re.compile(r"^mi-v06-[0-9a-f]{24}$")
MANAGER_ORDER_RE = re.compile(r"^mi-(?:auto|v06)-[0-9a-f]{24}$")
MAX_QTY_DP = Decimal(".000000001")


def _client_id(account, symbol, side, ny_date):
    payload = "|".join((account, symbol, side, ny_date, "v0.6"))
    return "mi-v06-" + hashlib.sha256(payload.encode()).hexdigest()[:24]


def reconcile_holdings(x):
    """Only touch shares fully explained by previous *manager* broker fills.

    This intentionally leaves a manually opened GME lot untouched, even when
    it later has a valid fresh insider signal. Missing or mixed history means
    legacy/protected, not an excuse to sell shares of uncertain origin.
    """
    owned, legacy = {}, {}
    for p in x["holdings"]:
        sym = p["symbol"]
        rows = [o for o in x["recent_orders"] if o.get("symbol") == sym]
        if not rows or any(
            not MANAGER_ORDER_RE.fullmatch(str(o.get("client_order_id") or ""))
            for o in rows
        ):
            legacy[sym] = p
            continue
        qty = usd(p["qty"])
        net = Decimal("0")
        valid = True
        for o in rows:
            side = o.get("side")
            try:
                filled = usd(o.get("filled_qty"))
            except ValueError:
                valid = False
                break
            if side == "buy":
                net += filled
            elif side == "sell":
                net -= filled
            else:
                valid = False
                break
        if not valid or net <= 0 or abs(net - qty) > Decimal(".00000001"):
            legacy[sym] = p
        else:
            owned[sym] = p
    return owned, legacy


def _asset_allowed(asset, sym):
    return (
        isinstance(asset, dict)
        and asset.get("symbol") == sym
        and asset.get("class") == "us_equity"
        and asset.get("status") == "active"
        and asset.get("tradable") is True
        and asset.get("fractionable") is True
        and asset.get("exchange") in {"NYSE", "NASDAQ", "AMEX"}
    )


def build_targets(scores, equity, legacy, *, max_stocks=MAX_STOCKS):
    """Distribute the entire strategy investable budget among positive signals.

    A 0.5%-of-total-equity reserve is intentional, to avoid buying power debt.
    More than one candidate does not mean equal weights: scores normalize.
    """
    if not isinstance(scores, list):
        raise ValueError("Missing completed conviction scores")
    locked = sum((money(p["market_value_usd"]) for p in legacy.values()),
                 Decimal("0"))
    if locked > equity:
        raise ValueError("Protected position values exceed paper equity")
    budget = max(Decimal("0"), equity - locked - equity * BUFFER_RATE)
    budget = budget.quantize(Decimal(".01"), rounding=ROUND_DOWN)
    slots = max_stocks - len(legacy)
    if slots < 0:
        raise ValueError("Too many protected positions")
    selected = []
    seen = set()
    for item in scores:
        if not isinstance(item, dict):
            raise ValueError("Malformed conviction signal")
        symbol = validate_symbol(item.get("symbol"))
        if symbol in seen:
            raise ValueError("Repeated ticker in conviction scores")
        seen.add(symbol)
        if symbol in legacy:
            continue
        try:
            score = Decimal(str(item["score"]))
        except (KeyError, ValueError, TypeError):
            raise ValueError("Invalid conviction score") from None
        if not score.is_finite() or score <= 0 or score > 1:
            raise ValueError("Score outside (0,1]")
        if len(selected) < slots:
            selected.append((symbol, score))
    total = sum((v for s, v in selected), Decimal("0"))
    if total == 0:
        return {}, budget
    # Floor every target to cents; assign rounding residual to top-ranked
    # signal so weights sum to the investment budget exactly.
    targets = {symbol: (budget * score / total).quantize(
        Decimal(".01"), rounding=ROUND_DOWN) for symbol, score in selected}
    residual = budget - sum(targets.values())
    targets[selected[0][0]] += residual
    return targets, budget


def _daily_count(orders, now):
    nyday = now.astimezone(EASTERN).date()
    n = 0
    for order in orders:
        cid = str(order.get("client_order_id") or "")
        if not MANAGER_ORDER_RE.fullmatch(cid):
            continue
        submitted = order.get("submitted_at")
        if not submitted:
            continue
        try:
            dt = datetime.fromisoformat(str(submitted).replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("Malformed manager broker order date") from None
        if dt.tzinfo is None:
            raise ValueError("Manager broker timestamps need timezone")
        if dt.astimezone(EASTERN).date() == nyday:
            n += 1
    return n


def plan_rebalance(client, snapshot, scores, *, expected_account, now=None):
    """Read-only decision. Trades NEVER occur inside this function."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("Timezone-aware time required")
    x = validate_snapshot(snapshot, expected_account)
    managed, legacy = reconcile_holdings(x)
    targets, strategy_budget = build_targets(scores, x["equity"], legacy)
    day = now.astimezone(EASTERN).date().isoformat()
    daily = _daily_count(x["recent_orders"], now)
    plan = {
        "version": "0.6.0",
        "mode": "PAPER_ONLY_AGGRESSIVE_CONVICTION_REBALANCING",
        "as_of": day, "account_id": expected_account,
        "cash_usd": str(x["cash"]), "equity_usd": str(x["equity"]),
        "equity_change_from_initial_1000_usd": str(
            (x["equity"] - Decimal("1000")).quantize(Decimal(".01"))),
        "position_count": len(x["holdings"]),
        "held_positions": x["holdings"],
        "protected_positions": sorted(legacy),
        "manager_positions": sorted(managed),
        "strategy_budget_usd": str(strategy_budget),
        "reserve_pct_equity": "0.5",
        "target_weights": [
            {"symbol": sym, "target_usd": str(target),
             "target_pct_strategy_budget": str(
                 (100 * target / strategy_budget).quantize(Decimal(".01"))
                 if strategy_budget else Decimal("0"))}
            for sym, target in targets.items()],
        "daily_automation_orders": daily,
        "pending_broker_orders": len(x["open_orders"]),
        "proposed_order": None,
        "note": ("100% conviction allocation within investable strategy capital; "
                 "legacy positions including pilot GME remain protected. "
                 "One PAPER broker order per cycle; no margin or LIVE endpoint."),
    }
    if not x["can_buy"]:
        plan["block_reason"] = "Existing PAPER order open; await fill/cancel"
        return plan
    if not trading_window(now, x["clock"]):
        plan["block_reason"] = "Outside regular-market portfolio trading window"
        return plan
    if daily >= MAX_AUTOMATED_ORDERS_DAILY:
        plan["block_reason"] = "Maximum 10 manager orders today"
        return plan

    outstanding_orders = {
        o.get("client_order_id") for o in x["recent_orders"]
        if o.get("client_order_id") is not None
    }
    # Never rebalance protected/manual holdings. Candidate exposure also
    # cannot add to a protected ticker without changing ownership attribution.
    band = max(MIN_TRADE, x["equity"] * REBALANCE_BAND)
    sell_ops, buy_ops = [], []
    for sym, pos in managed.items():
        held_value = money(pos["market_value_usd"])
        target = targets.get(sym, Decimal("0"))
        excess = held_value - target
        if excess < band and target > 0:
            continue
        # Full exits can clean up small fractional PAPER remnants; partial
        # rebalances retain the normal minimum-dollar churn guard.
        if excess < (Decimal("1") if target <= 0 else MIN_TRADE):
            continue
        qty = usd(pos["qty"])
        if qty <= 0 or held_value <= 0 or qty.as_tuple().exponent < -9:
            continue  # Alpaca PAPER maximum fractional quantity precision
        sell_qty = (qty * excess / held_value).quantize(MAX_QTY_DP,
                                                        rounding=ROUND_DOWN)
        if target <= 0:
            sell_qty = qty  # exact full exit
        if not Decimal("0") < sell_qty <= qty:
            continue
        cid = _client_id(expected_account, sym, "sell", day)
        if cid in outstanding_orders:
            continue
        sell_ops.append((excess, {
            "symbol": sym, "side": "sell", "qty": str(sell_qty),
            "client_order_id": cid, "target_usd": str(target),
            "delta_usd": str(excess),
        }))
    for sym, target in targets.items():
        if target <= 0:
            continue
        pos = managed.get(sym)
        current = money(pos["market_value_usd"]) if pos else Decimal("0")
        delta = target - current
        if delta < band and current > 0:
            continue
        if delta < MIN_TRADE:
            continue
        if len(x["holdings"]) >= MAX_STOCKS and pos is None:
            continue  # A sell and resulting fill must free a stock slot first.
        amount = min(delta, x["cash"] - x["equity"] * BUFFER_RATE)
        amount = amount.quantize(Decimal(".01"), rounding=ROUND_DOWN)
        if amount < MIN_TRADE:
            continue
        cid = _client_id(expected_account, sym, "buy", day)
        if cid in outstanding_orders:
            continue
        buy_ops.append((delta, {
            "symbol": sym, "side": "buy", "notional_usd": str(amount),
            "client_order_id": cid, "target_usd": str(target),
            "delta_usd": str(delta),
        }))
    # De-risk and release cash first; spend on next scheduled broker cycle.
    ordered = [order for _, order in sorted(
        sell_ops, key=lambda pair: (-pair[0], pair[1]["symbol"]))]
    if not ordered:
        ordered = [order for _, order in sorted(
            buy_ops, key=lambda pair: (-pair[0], pair[1]["symbol"]))]
    for order in ordered:
        asset = client.request("GET", "/v2/assets/" + order["symbol"])
        if _asset_allowed(asset, order["symbol"]):
            plan["proposed_order"] = order
            return plan
        # A malformed/untradable asset is not permission to submit anything.
        # An irrelevant candidate is skipped only when the broker explicitly
        # says not tradable; network/API errors propagate and fail closed.
    plan["block_reason"] = ("No rebalance exceeds the 2.5%/$15 threshold "
                            "or no eligible tradable PAPER asset")
    return plan


def run_rebalance_cycle(client, scores, *, expected_account, execute=False,
                        activation="", now=None,
                        intent_dir="data/automation_intents"):
    """At most one PAPER POST. No retry on ambiguous broker acknowledgement."""
    if execute and activation != AUTOMATION_OPT_IN:
        raise ValueError("Explicit PAPER automation activation is required")
    now = now or datetime.now(timezone.utc)
    first = plan_rebalance(client, account_snapshot(client), scores,
                           expected_account=expected_account, now=now)
    if not execute or first["proposed_order"] is None:
        return {
            "status": ("READ_ONLY_REBALANCE_PLAN" if not execute
                       else "NO_REBALANCE_ORDER"),
            "plan": first,
        }
    # A second authoritative account/orders/positions/clock read. Require
    # identical action side/symbol. Execute the freshly calculated amount only.
    second = plan_rebalance(
        client, account_snapshot(client), scores,
        expected_account=expected_account, now=now)
    a, b = first["proposed_order"], second["proposed_order"]
    if b is None or (a["side"], a["symbol"], a["client_order_id"]) != (
            b["side"], b["symbol"], b["client_order_id"]):
        return {"status": "ABORTED_BROKER_STATE_CHANGED", "plan": second}
    cid = b["client_order_id"]
    if not V06_ORDER_RE.fullmatch(cid):
        raise ValueError("Invalid v0.6 PAPER broker client-order ID")
    # Local write-ahead audit prevents same machine accidental duplicate.
    # GitHub runners are ephemeral; deterministic IDs + broker order-history
    # checks protect cross-run duplicates. No retry of an uncertain POST.
    folder = Path(intent_dir)
    folder.mkdir(parents=True, exist_ok=True)
    marker = folder / (cid + ".json")
    try:
        with marker.open("x", encoding="utf-8") as f:
            json.dump({"paper_account_id": expected_account,
                       "client_order_id": cid, "order": b,
                       "attempted_at_utc": now.astimezone(timezone.utc).isoformat()},
                      f, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
    except FileExistsError:
        return {"status": "ALREADY_ATTEMPTED_NO_RETRY", "plan": second}
    payload = {
        "symbol": b["symbol"], "side": b["side"], "type": "market",
        "time_in_force": "day", "extended_hours": False,
        "client_order_id": cid,
    }
    if b["side"] == "buy":
        payload["notional"] = b["notional_usd"]
    elif b["side"] == "sell":
        payload["qty"] = b["qty"]
    else:
        raise ValueError("Unsupported order side")
    receipt = client.submit(payload)
    if (not isinstance(receipt, dict)
            or receipt.get("symbol") != b["symbol"]
            or receipt.get("client_order_id") != cid):
        raise RuntimeError("Ambiguous PAPER order acknowledgement; no retry")
    return {
        "status": "PAPER_REBALANCE_SUBMITTED_NOT_A_FILL",
        "plan": second,
        "paper_broker_order": {
            "id": receipt.get("id"), "status": receipt.get("status"),
            "symbol": receipt.get("symbol"), "side": b["side"],
            "client_order_id": cid, "filled_qty": receipt.get("filled_qty"),
        },
    }
