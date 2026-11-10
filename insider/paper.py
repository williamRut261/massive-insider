"""One-order, PAPER-only Alpaca pilot for SEC-researched symbols.

NO live endpoint, configurable base URL, live account keys, sells, leverage,
shorts, automation, or repeated allocation. Every order requires a recent
read-only preview, exact account-ID confirmation, and an explicit submit flag.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN

PAPER_API = "https://paper-api.alpaca.markets"
VIRTUAL_CAPITAL = Decimal("1000")
MAX_POSITION_USD = Decimal("200")
RESERVE_USD = Decimal("20")
MAX_AGE = timedelta(minutes=5)
SYMBOL_RE = re.compile(r"^[A-Z]{1,5}$")
CLIENT_ID_RE = re.compile(r"^mi-paper-[0-9a-f]{24}$")
ACCOUNT_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")


def usd(value):
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("Invalid monetary amount") from None
    if not d.is_finite() or d < 0:
        raise ValueError("Money must be finite and nonnegative")
    return d


def _money(value):
    return usd(value).quantize(Decimal(".01"), rounding=ROUND_DOWN)


def _time_now():
    return datetime.now(timezone.utc)


def _parse_time(value):
    try:
        d = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        raise ValueError("Invalid preview timestamp") from None
    if d.tzinfo is None:
        raise ValueError("Preview timestamp needs timezone")
    return d.astimezone(timezone.utc)


def validate_symbol(symbol):
    if not isinstance(symbol, str) or not SYMBOL_RE.fullmatch(symbol):
        raise ValueError("Symbol must be plain uppercase common-stock ticker")
    return symbol


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("Redirect refused for paper broker credentials")


class ApiError(RuntimeError):
    def __init__(self, status):
        super().__init__(f"Alpaca PAPER API returned HTTP {status}")
        self.status = status


class PaperClient:
    """Fixed paper-only URL; *only* dedicated Massive Insider key names."""

    def __init__(self, key=None, secret=None):
        self.key = (key if key is not None else os.getenv("MI_ALPACA_PAPER_KEY_ID", ""))
        self.secret = (secret if secret is not None else os.getenv("MI_ALPACA_PAPER_SECRET_KEY", ""))
        if not self.key or not self.secret:
            raise ValueError("Set MI_ALPACA_PAPER_KEY_ID and MI_ALPACA_PAPER_SECRET_KEY (PAPER credentials only)")

    def request(self, method, path, data=None):
        # Strict request allowlist — impossible to route to LIVE API.
        allowed_get = {"/v2/account", "/v2/positions", "/v2/orders?status=open&limit=500", "/v2/clock"}
        asset = re.fullmatch(r"/v2/assets/([A-Z]{1,5})", path)
        by_client = path.startswith("/v2/orders:by_client_order_id?client_order_id=")
        if by_client:
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
            by_client = (set(query) == {"client_order_id"} and
                         len(query["client_order_id"]) == 1 and
                         CLIENT_ID_RE.fullmatch(query["client_order_id"][0]) is not None)
        if method == "GET":
            if path not in allowed_get and asset is None and not by_client:
                raise ValueError("Disallowed PAPER GET path")
        elif method == "POST":
            if path != "/v2/orders" or data is None:
                raise ValueError("Disallowed PAPER POST path")
        else:
            raise ValueError("Only allowed GETs and POST paper order")
        url = PAPER_API + path
        headers = {
            "APCA-API-KEY-ID": self.key,
            "APCA-API-SECRET-KEY": self.secret,
            "Accept": "application/json",
            "User-Agent": "massive-insider-paper-pilot/0.4",
        }
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            url, method=method, headers=headers,
            data=(json.dumps(data, separators=(",", ":")).encode("utf-8")
                  if data is not None else None))
        try:
            with urllib.request.build_opener(NoRedirect()).open(req, timeout=15) as res:
                raw = res.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise ValueError("Broker response too large")
            return json.loads(raw)
        except urllib.error.HTTPError as e:
            raise ApiError(e.code) from None
        except urllib.error.URLError:
            raise RuntimeError(
                "PAPER API request failed; if this was a POST, check by client_order_id "
                "before doing anything else") from None

    def read_state(self, symbol):
        symbol = validate_symbol(symbol)
        return {
            "account": self.request("GET", "/v2/account"),
            "positions": self.request("GET", "/v2/positions"),
            "open_orders": self.request("GET", "/v2/orders?status=open&limit=500"),
            "clock": self.request("GET", "/v2/clock"),
            "asset": self.request("GET", "/v2/assets/" + symbol),
        }

    def find_order(self, client_id):
        if not CLIENT_ID_RE.fullmatch(client_id):
            raise ValueError("Invalid client_order_id")
        path = "/v2/orders:by_client_order_id?client_order_id=" + client_id
        try:
            return self.request("GET", path)
        except ApiError as exc:
            if exc.status == 404:
                return None
            raise

    def submit(self, order):
        return self.request("POST", "/v2/orders", order)


def verified_symbol(report, symbol):
    symbol = validate_symbol(symbol)
    if not isinstance(report, dict) or report.get("version") != "0.3.0":
        raise ValueError("Requires v0.3 SEC research output")
    matches = [
        row for row in report.get("all_candidates", [])
        if symbol in row.get("historical_tickers", [])
    ]
    if len(matches) != 1:
        raise ValueError("Symbol missing/ambiguous in SEC research report")
    candidate = matches[0]
    if (candidate.get("sec_verification_status") != "SEC_FORM4_PURCHASE_RESEARCH"
            or usd(candidate.get("sec_confirmed_qualifying_purchase_usd", 0)) <= 0
            or int(candidate.get("sec_form4_documents_reviewed", 0)) < 1
            or not candidate.get("sec_xml_sources")):
        raise ValueError("Paper pilot requires at least one actual SEC Form 4 purchase")
    return candidate


def check_state(state, symbol, notional, expected_account=None, require_open=False):
    """Reject shared account state, buying power leverage and ineligible shares."""
    acct = state["account"]
    positions = state["positions"]
    orders = state["open_orders"]
    clock = state["clock"]
    asset = state["asset"]
    if not isinstance(acct, dict) or not isinstance(asset, dict) or not isinstance(clock, dict):
        raise ValueError("Malformed broker account, clock or asset")
    account_id = str(acct.get("id", ""))
    if not ACCOUNT_RE.fullmatch(account_id):
        raise ValueError("Invalid account ID")
    if expected_account is not None and account_id != expected_account:
        raise ValueError("PAPER account ID changed; abort")
    if acct.get("status") != "ACTIVE" or any(
        acct.get(k, False) for k in ("trading_blocked", "account_blocked", "trade_suspended_by_user")
    ):
        raise ValueError("PAPER account not active/tradable")
    if (not isinstance(positions, list) or positions or not isinstance(orders, list)
            or orders or len(orders) == 500):
        raise ValueError("PAPER pilot requires an empty account: zero positions and open orders")
    if (asset.get("symbol") != symbol or asset.get("asset_class") != "us_equity"
            or asset.get("status") != "active" or asset.get("tradable") is not True
            or asset.get("fractionable") is not True
            or asset.get("exchange") not in {"NYSE", "NASDAQ", "AMEX"}):
        raise ValueError("Asset not eligible for simple exchange-listed fractional-share pilot")
    if require_open and clock.get("is_open") is not True:
        raise ValueError("Market must be open for PAPER pilot submission")
    equity, cash = usd(acct.get("equity")), usd(acct.get("cash"))
    if equity <= 0 or cash < RESERVE_USD:
        raise ValueError("Insufficient cash/equity for paper pilot")
    amount = _money(notional)
    if amount < Decimal("1"):
        raise ValueError("PAPER pilot requires at least $1")
    if amount > MAX_POSITION_USD or amount > _money(equity * Decimal(".20")):
        raise ValueError("Exceeds 20%/$200 paper per-stock limit")
    if amount > cash - RESERVE_USD:
        raise ValueError("Insufficient cash after $20 virtual capital reserve")
    return {
        "account_id": account_id,
        "cash_usd": str(_money(cash)),
        "equity_usd": str(_money(equity)),
        "notional_usd": str(amount),
        "market_is_open": clock.get("is_open") is True,
        "asset_symbol": symbol,
    }


def make_preview(report, symbol, notional, client, now=None):
    """Read-only preview. Explicitly no POST is issued by this function."""
    verified = verified_symbol(report, symbol)
    state = client.read_state(symbol)
    snapshot = check_state(state, symbol, notional)
    now = now or _time_now()
    if now.tzinfo is None:
        raise ValueError("Time must contain UTC offset")
    return {
        "plan_version": "paper-pilot-v0.4",
        "mode": "ALPACA_PAPER_ONLY_ONE_BUY",
        "created_at": now.astimezone(timezone.utc).isoformat(),
        "expires_at": (now + MAX_AGE).astimezone(timezone.utc).isoformat(),
        "client_order_id": "mi-paper-" + secrets.token_hex(12),
        "symbol": symbol,
        "notional_usd": snapshot["notional_usd"],
        "paper_account_id": snapshot["account_id"],
        "paper_cash_usd_at_preview": snapshot["cash_usd"],
        "paper_equity_usd_at_preview": snapshot["equity_usd"],
        "market_open_at_preview": snapshot["market_is_open"],
        "sec_form4_source_urls": verified["sec_xml_sources"],
        "note": ("ONE manual PAPER market buy; not a validated alpha signal. "
                 "No automated rebalancing, no sells, no LIVE API."),
    }


def paper_submit(preview, client, *, confirm, expected_account, now=None):
    """A single explicitly authorized paper buy; no retry on ambiguous acknowledgement."""
    if confirm != "I-APPROVE-PAPER-ONLY":
        raise ValueError("Explicit PAPER approval string required")
    if preview.get("plan_version") != "paper-pilot-v0.4" or (
        preview.get("mode") != "ALPACA_PAPER_ONLY_ONE_BUY"
    ):
        raise ValueError("Unsupported/unsafe preview")
    symbol = validate_symbol(preview.get("symbol"))
    ident = preview.get("client_order_id")
    if not isinstance(ident, str) or not CLIENT_ID_RE.fullmatch(ident):
        raise ValueError("Invalid paper order ID")
    now = now or _time_now()
    issued, expiry = _parse_time(preview.get("created_at")), _parse_time(preview.get("expires_at"))
    if not (issued <= now <= expiry and expiry - issued <= MAX_AGE):
        raise ValueError("Paper preview expired/not yet valid; generate a fresh preview")
    if preview.get("paper_account_id") != expected_account:
        raise ValueError("Account ID approval differs from plan")
    prior = client.find_order(ident)
    if prior is not None:
        return {"status": "EXISTING_PAPER_ORDER_NO_RESUBMIT", "order": prior}
    state = client.read_state(symbol)
    snapshot = check_state(state, symbol, preview.get("notional_usd"),
                           expected_account=expected_account, require_open=True)
    # The paper notional is <= $200, 20% of equity, and <= cash-$20.
    order = {
        "symbol": symbol,
        "notional": snapshot["notional_usd"],
        "side": "buy",
        "type": "market",
        "time_in_force": "day",
        "extended_hours": False,
        "client_order_id": ident,
    }
    receipt = client.submit(order)  # one POST only, never automatic retry
    if (not isinstance(receipt, dict) or
            receipt.get("client_order_id") != ident or
            receipt.get("symbol") != symbol):
        raise RuntimeError("Paper order acknowledgement unexpected; investigate by client ID")
    return {"status": "SUBMITTED_TO_PAPER_BROKER_NOT_A_FILL", "order": receipt}
