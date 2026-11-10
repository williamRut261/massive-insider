"""PAPER brokerage pilot tests. All offline; HTTP requests are never made."""
import copy
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from insider.paper import (
    ACCOUNT_RE, PAPER_API, PaperClient, check_state, make_preview, paper_submit,
)

NOW = datetime(2026, 11, 10, 15, 0, tzinfo=timezone.utc)
ACCOUNT_ID = "550e8400-e29b-41d4-a716-446655440000"


def report():
    return {
        "version": "0.3.0",
        "all_candidates": [{
            "historical_tickers": ["GME", "GME.WS"],
            "issuer_cik": "0001326380",
            "sec_verification_status": "SEC_FORM4_PURCHASE_RESEARCH",
            "sec_confirmed_qualifying_purchase_usd": 10563796.10,
            "sec_form4_documents_reviewed": 1,
            "sec_xml_sources": [
                "https://www.sec.gov/Archives/edgar/data/1326380/000092189526002670/form4.xml"
            ],
        }],
    }


def state():
    return {
        "account": {
            "id": ACCOUNT_ID, "status": "ACTIVE",
            "cash": "1000.00", "equity": "1000.00",
            "trading_blocked": False, "account_blocked": False,
        },
        "positions": [], "open_orders": [],
        "clock": {"is_open": True},
        "asset": {
            "symbol": "GME", "asset_class": "us_equity",
            "status": "active", "tradable": True,
            "fractionable": True, "exchange": "NYSE",
        },
    }


class FakeClient:
    def __init__(self, snapshot=None, existing=None):
        self.snapshot = snapshot if snapshot is not None else state()
        self.existing = existing
        self.calls = []
        self.posted = []

    def read_state(self, symbol):
        self.calls.append(("GET_STATE", symbol))
        return self.snapshot

    def find_order(self, client_id):
        self.calls.append(("GET_BY_CLIENT_ID", client_id))
        return self.existing

    def submit(self, order):
        self.calls.append(("POST_PAPER_ONLY", "/v2/orders"))
        self.posted.append(order)
        return {
            "client_order_id": order["client_order_id"],
            "symbol": order["symbol"], "status": "accepted",
            "id": "paper-order-test-id",
        }


class PaperPilotTests(unittest.TestCase):
    def test_broker_host_fixed_to_paper_only(self):
        self.assertEqual(PAPER_API, "https://paper-api.alpaca.markets")
        client = PaperClient(key="test-paper-id", secret="test-paper-secret")
        for path in ("https://api.alpaca.markets/v2/orders", "/v2/positions/close",
                     "/v2/account/configurations", "/v2/orders?status=all"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                client.request("GET", path)

    def test_no_broker_keys_does_not_fallback_to_live_env(self):
        with self.assertRaises(ValueError):
            PaperClient(key="", secret="")

    def test_preview_does_not_submit(self):
        client = FakeClient()
        plan = make_preview(report(), "GME", "25", client, now=NOW)
        self.assertEqual(plan["notional_usd"], "25.00")
        self.assertEqual(plan["paper_account_id"], ACCOUNT_ID)
        self.assertEqual(plan["mode"], "ALPACA_PAPER_ONLY_ONE_BUY")
        self.assertEqual(len(client.posted), 0)
        self.assertEqual(client.calls, [("GET_STATE", "GME")])

    def test_single_explicit_paper_order(self):
        c = FakeClient()
        p = make_preview(report(), "GME", "25", c, now=NOW)
        result = paper_submit(p, c, confirm="I-APPROVE-PAPER-ONLY",
                              expected_account=ACCOUNT_ID,
                              now=NOW + timedelta(seconds=5))
        self.assertEqual(result["status"], "SUBMITTED_TO_PAPER_BROKER_NOT_A_FILL")
        self.assertEqual(len(c.posted), 1)
        self.assertEqual(c.posted[0]["notional"], "25.00")
        self.assertEqual(c.posted[0]["type"], "market")
        self.assertEqual(c.posted[0]["time_in_force"], "day")
        self.assertEqual(c.posted[0]["side"], "buy")
        self.assertFalse(c.posted[0]["extended_hours"])
        self.assertEqual(c.posted[0]["client_order_id"], p["client_order_id"])

    def test_no_approval_no_order(self):
        c = FakeClient()
        p = make_preview(report(), "GME", "25", c, now=NOW)
        with self.assertRaisesRegex(ValueError, "approval"):
            paper_submit(p, c, confirm="yes", expected_account=ACCOUNT_ID, now=NOW)
        self.assertFalse(c.posted)

    def test_wrong_account_id_no_order(self):
        c = FakeClient()
        p = make_preview(report(), "GME", "25", c, now=NOW)
        with self.assertRaisesRegex(ValueError, "Account ID"):
            paper_submit(p, c, confirm="I-APPROVE-PAPER-ONLY",
                         expected_account="00000000-0000-0000-0000-000000000001", now=NOW)
        self.assertFalse(c.posted)

    def test_expired_preview_no_order(self):
        c = FakeClient()
        p = make_preview(report(), "GME", "25", c, now=NOW)
        with self.assertRaisesRegex(ValueError, "expired"):
            paper_submit(p, c, confirm="I-APPROVE-PAPER-ONLY",
                         expected_account=ACCOUNT_ID, now=NOW + timedelta(minutes=6))
        self.assertFalse(c.posted)

    def test_market_closed_no_order(self):
        c = FakeClient()
        p = make_preview(report(), "GME", "25", c, now=NOW)
        c.snapshot["clock"]["is_open"] = False
        with self.assertRaisesRegex(ValueError, "Market must be open"):
            paper_submit(p, c, confirm="I-APPROVE-PAPER-ONLY",
                         expected_account=ACCOUNT_ID, now=NOW)
        self.assertFalse(c.posted)

    def test_existing_order_returns_without_post(self):
        c = FakeClient(existing={"status": "filled", "symbol": "GME"})
        p = make_preview(report(), "GME", "25", c, now=NOW)
        result = paper_submit(p, c, confirm="I-APPROVE-PAPER-ONLY",
                              expected_account=ACCOUNT_ID, now=NOW)
        self.assertEqual(result["status"], "EXISTING_PAPER_ORDER_NO_RESUBMIT")
        self.assertFalse(c.posted)

    def test_existing_positions_refused(self):
        c = FakeClient()
        c.snapshot["positions"] = [{"symbol": "TSLA"}]
        with self.assertRaisesRegex(ValueError, "empty account"):
            make_preview(report(), "GME", "25", c, now=NOW)

    def test_open_orders_refused(self):
        c = FakeClient()
        c.snapshot["open_orders"] = [{"symbol": "NVDA"}]
        with self.assertRaisesRegex(ValueError, "empty account"):
            make_preview(report(), "GME", "25", c, now=NOW)

    def test_fractional_asset_required(self):
        c = FakeClient()
        c.snapshot["asset"]["fractionable"] = False
        with self.assertRaisesRegex(ValueError, "eligible"):
            make_preview(report(), "GME", "25", c, now=NOW)

    def test_otc_or_crypto_refused(self):
        for asset_class, exchange in (("crypto", "NYSE"), ("us_equity", "OTC")):
            c = FakeClient()
            c.snapshot["asset"]["asset_class"] = asset_class
            c.snapshot["asset"]["exchange"] = exchange
            with self.subTest(asset_class=asset_class), self.assertRaises(ValueError):
                make_preview(report(), "GME", "25", c, now=NOW)

    def test_paper_account_blocked(self):
        for field in ("trading_blocked", "account_blocked", "trade_suspended_by_user"):
            c = FakeClient()
            c.snapshot["account"][field] = True
            with self.subTest(field=field), self.assertRaises(ValueError):
                make_preview(report(), "GME", "25", c, now=NOW)

    def test_oversized_paper_order_refused(self):
        for amount in ("200.01", "1000", "Infinity", "NaN", "-1"):
            with self.subTest(amount=amount), self.assertRaises(ValueError):
                make_preview(report(), "GME", amount, FakeClient(), now=NOW)

    def test_20pct_of_equity_limit(self):
        c = FakeClient()
        c.snapshot["account"]["equity"] = "100"
        with self.assertRaisesRegex(ValueError, "20%"):
            make_preview(report(), "GME", "25", c, now=NOW)

    def test_reserve_after_buy(self):
        c = FakeClient()
        c.snapshot["account"]["cash"] = "30"
        with self.assertRaisesRegex(ValueError, "Insufficient cash"):
            make_preview(report(), "GME", "25", c, now=NOW)

    def test_reject_unverified_signal(self):
        r = report()
        r["all_candidates"][0]["sec_verification_status"] = "UNVERIFIED_MASSIVE_AGGREGATE"
        with self.assertRaisesRegex(ValueError, "requires"):
            make_preview(r, "GME", "25", FakeClient(), now=NOW)

    def test_reject_asset_alias_warrant(self):
        with self.assertRaisesRegex(ValueError, "plain uppercase"):
            make_preview(report(), "GME.WS", "25", FakeClient(), now=NOW)

    def test_reject_unrelated_symbol(self):
        with self.assertRaisesRegex(ValueError, "missing"):
            make_preview(report(), "TSLA", "25", FakeClient(), now=NOW)

    def test_broker_account_changed_on_submit(self):
        c = FakeClient()
        p = make_preview(report(), "GME", "25", c, now=NOW)
        c.snapshot["account"]["id"] = "11111111-1111-1111-1111-111111111111"
        with self.assertRaisesRegex(ValueError, "changed"):
            paper_submit(p, c, confirm="I-APPROVE-PAPER-ONLY",
                         expected_account=ACCOUNT_ID, now=NOW)
        self.assertFalse(c.posted)

    def test_plan_mutation_rechecked(self):
        c = FakeClient()
        p = make_preview(report(), "GME", "25", c, now=NOW)
        p["notional_usd"] = "100000"
        with self.assertRaises(ValueError):
            paper_submit(p, c, confirm="I-APPROVE-PAPER-ONLY",
                         expected_account=ACCOUNT_ID, now=NOW)
        self.assertFalse(c.posted)

    def test_order_id_consistent_and_unique(self):
        c = FakeClient()
        x = make_preview(report(), "GME", "25", c, now=NOW)
        y = make_preview(report(), "GME", "25", c, now=NOW)
        self.assertNotEqual(x["client_order_id"], y["client_order_id"])
        self.assertTrue(x["client_order_id"].startswith("mi-paper-"))

    def test_no_live_url_or_keys_allowed_in_source(self):
        src = Path(__file__).parents[1] / "insider" / "paper.py"
        data = src.read_text()
        self.assertNotIn('"https://api.alpaca.markets"', data)
        self.assertNotIn('APCA_API_SECRET_KEY', data)
        self.assertNotIn('os.getenv("APCA_API_KEY_ID")', data)  # no generic credentials
