"""Automated PAPER allocation: offline broker mocks and restart-safety tests."""
import copy
import tempfile
import unittest
from datetime import datetime, timezone

from insider.portfolio import (
    AUTOMATION_OPT_IN, account_snapshot, order_id, proposed_order,
    run_one_cycle, validate_snapshot,
)

NOW = datetime(2026, 11, 10, 15, 5, tzinfo=timezone.utc)  # 10:05 NY, market open.
ACCOUNT = "5a5d5e5f-19fc-4aa3-9873-7b8cce1b298f"


def candidate(symbol="GPI", cik="0001031203", amount=90000):
    return {
        "symbol": symbol, "issuer_cik": cik,
        "latest_filing_date": "2026-11-09",
        "reference_accessions": ["0000905148-26-004561"],
        "purchase_usd": amount,
    }


def reference(symbol):
    ci = {"GPI": "0001031203", "GME": "0001326380", "AAA": "0000000100"}
    return {
        "ticker": symbol, "cik": ci.get(symbol),
        "type": "CS", "active": True, "market": "stocks",
        "locale": "us", "primary_exchange": "XNYS",
    }


def state():
    return {
        "account": {
            "id": ACCOUNT, "status": "ACTIVE", "equity": "1000",
            "cash": "975", "trading_blocked": False,
            "account_blocked": False, "trade_suspended_by_user": False,
        },
        "positions": [
            {"symbol":"GME", "asset_class":"us_equity",
             "side":"long", "market_value":"25", "qty":"0.89"}
        ],
        "open_orders": [],
        "recent_orders": [
            {"symbol":"GME", "client_order_id":"mi-paper-c97905989ddd94b770b569b9",
             "submitted_at":"2026-11-10T14:32:51Z", "status":"filled"}
        ],
        "clock": {"is_open": True},
    }


class FakeClient:
    def __init__(self, s=None):
        self.s = copy.deepcopy(s if s is not None else state())
        self.posts = []
        self.calls = []

    def request(self, method, path, data=None):
        self.calls.append((method, path))
        if method != "GET":
            raise AssertionError("unexpected request")
        if path == "/v2/account": return copy.deepcopy(self.s["account"])
        if path == "/v2/positions": return copy.deepcopy(self.s["positions"])
        if path == "/v2/orders?status=open&limit=500":
            return copy.deepcopy(self.s["open_orders"])
        if path == "/v2/orders?status=all&limit=500":
            return copy.deepcopy(self.s["recent_orders"])
        if path == "/v2/clock": return copy.deepcopy(self.s["clock"])
        if path.startswith("/v2/assets/"):
            sym = path.split("/")[-1]
            return {
                "symbol":sym,"class":"us_equity","status":"active",
                "tradable":True,"fractionable":True,"exchange":"NYSE",
            }
        raise AssertionError("unexpected PAPER path: " + path)

    def submit(self, order):
        self.posts.append(copy.deepcopy(order))
        self.s["recent_orders"].append({
            "symbol": order["symbol"], "client_order_id": order["client_order_id"],
            "submitted_at": "2026-11-10T15:05:00Z", "status": "pending_new",
        })
        return {
            "id": "paper-order-test", "symbol": order["symbol"],
            "client_order_id": order["client_order_id"],
            "status": "pending_new", "filled_qty": "0",
        }


def plan(candidates=None, s=None, now=NOW, client=None, ref=reference):
    client = client if client is not None else FakeClient(s)
    candidates = candidates if candidates is not None else [candidate()]
    return proposed_order(client, client.s, candidates,
                          expected_account=ACCOUNT, now=now, reference_lookup=ref)


class PaperPortfolioTests(unittest.TestCase):
    def test_current_gme_preserved_skip_rebuy(self):
        p = plan([candidate("GME", "0001326380")])
        self.assertEqual(p["position_count"], 1)
        self.assertEqual(p["held_positions"][0]["symbol"], "GME")
        self.assertIsNone(p["proposed_order"])
        self.assertIn("No new eligible", p["block_reason"])

    def test_select_second_candidate_when_gme_held(self):
        p = plan([candidate("GME", "0001326380"), candidate()])
        self.assertEqual(p["proposed_order"]["symbol"], "GPI")
        self.assertEqual(p["proposed_order"]["notional_usd"], "100.00")
        self.assertEqual(p["position_count"], 1)

    def test_read_only_plan_does_not_post(self):
        c = FakeClient()
        with tempfile.TemporaryDirectory() as tmp:
            o = run_one_cycle(c, [candidate()], expected_account=ACCOUNT,
                              now=NOW, reference_lookup=reference, intent_dir=tmp)
        self.assertEqual(o["status"], "READ_ONLY_PLAN")
        self.assertFalse(c.posts)

    def test_explicit_activation_required_for_auto_orders(self):
        c = FakeClient()
        with self.assertRaisesRegex(ValueError, "Explicit"):
            run_one_cycle(c, [candidate()], expected_account=ACCOUNT,
                          execute=True, activation="", now=NOW,
                          reference_lookup=reference)
        self.assertFalse(c.posts)

    def test_one_paper_order_when_activated(self):
        c = FakeClient()
        with tempfile.TemporaryDirectory() as tmp:
            o = run_one_cycle(c, [candidate()], expected_account=ACCOUNT,
                              execute=True, activation=AUTOMATION_OPT_IN,
                              now=NOW, reference_lookup=reference,
                              intent_dir=tmp)
        self.assertEqual(o["status"], "PAPER_SUBMITTED_NOT_A_FILL")
        self.assertEqual(len(c.posts), 1)
        self.assertEqual(c.posts[0]["symbol"], "GPI")
        self.assertEqual(c.posts[0]["notional"], "100.00")
        self.assertEqual(c.posts[0]["side"], "buy")
        self.assertEqual(c.posts[0]["extended_hours"], False)
        self.assertEqual(c.posts[0]["type"], "market")

    def test_existing_order_history_prevents_duplicate_after_restart(self):
        c = FakeClient()
        with tempfile.TemporaryDirectory() as one, tempfile.TemporaryDirectory() as two:
            a = run_one_cycle(c, [candidate()], expected_account=ACCOUNT,
                              execute=True, activation=AUTOMATION_OPT_IN,
                              now=NOW, reference_lookup=reference,
                              intent_dir=one)
            b = run_one_cycle(c, [candidate()], expected_account=ACCOUNT,
                              execute=True, activation=AUTOMATION_OPT_IN,
                              now=NOW, reference_lookup=reference,
                              intent_dir=two)
        self.assertEqual(a["status"], "PAPER_SUBMITTED_NOT_A_FILL")
        self.assertEqual(b["status"], "NO_ELIGIBLE_PAPER_ORDER")
        self.assertEqual(len(c.posts), 1)

    def test_intent_blocks_retry_when_broker_order_not_yet_visible(self):
        c = FakeClient()
        with tempfile.TemporaryDirectory() as tmp:
            a = run_one_cycle(c, [candidate()], expected_account=ACCOUNT,
                              execute=True, activation=AUTOMATION_OPT_IN,
                              now=NOW, reference_lookup=reference,
                              intent_dir=tmp)
            c.s["recent_orders"] = state()["recent_orders"]  # stale broker history
            b = run_one_cycle(c, [candidate()], expected_account=ACCOUNT,
                              execute=True, activation=AUTOMATION_OPT_IN,
                              now=NOW, reference_lookup=reference,
                              intent_dir=tmp)
        self.assertEqual(a["status"], "PAPER_SUBMITTED_NOT_A_FILL")
        self.assertEqual(b["status"], "ALREADY_ATTEMPTED_NO_RETRY")
        self.assertEqual(len(c.posts), 1)

    def test_broker_account_id_change_refused(self):
        c = FakeClient()
        c.s["account"]["id"] = "00000000-0000-0000-0000-000000000001"
        with self.assertRaisesRegex(ValueError, "mismatch"):
            proposed_order(c, c.s, [candidate()], expected_account=ACCOUNT,
                           now=NOW, reference_lookup=reference)

    def test_excessive_virtual_equity_refused(self):
        c = FakeClient()
        c.s["account"]["equity"] = "100000"
        with self.assertRaisesRegex(ValueError, "dedicated"):
            plan(client=c)

    def test_existing_open_order_disables_all_new_buys(self):
        c = FakeClient()
        c.s["open_orders"].append({"symbol":"GME", "status":"open"})
        p = plan(client=c)
        self.assertEqual(p["pending_broker_orders"], 1)
        self.assertIsNone(p["proposed_order"])

    def test_max_ten_holdings(self):
        c = FakeClient()
        for i in range(9):
            c.s["positions"].append({
                "symbol": "A"+str(i) if False else ("A"* (i+1 if i<5 else 5-i+1)),
                "asset_class":"us_equity","side":"long",
                "market_value":"25","qty":"1",
            })
        # Because symbol is 1..5 uppercase only, use distinct legitimate names
        c.s["positions"] = state()["positions"] + [
            {"symbol":s,"asset_class":"us_equity","side":"long",
             "market_value":"25","qty":"1"}
            for s in ("A","AA","AAA","AAAA","AAAAA","B","BB","BBB","BBBB")]
        p = plan(client=c)
        self.assertEqual(p["position_count"], 10)
        self.assertIsNone(p["proposed_order"])

    def test_unknown_short_position_refused(self):
        c = FakeClient()
        c.s["positions"][0]["side"] = "short"
        with self.assertRaisesRegex(ValueError, "short"):
            plan(client=c)

    def test_cash_reserve_prevents_new_buy(self):
        c = FakeClient()
        c.s["account"]["cash"] = "30"
        p = plan(client=c)
        self.assertIsNone(p["proposed_order"])
        self.assertIn("minimum", p["block_reason"])

    def test_equity_drawdown_suspends_new_buy(self):
        c = FakeClient()
        c.s["account"]["equity"] = "750"
        p = plan(client=c)
        self.assertIsNone(p["proposed_order"])
        self.assertIn("80%", p["block_reason"])

    def test_filled_order_count_caps_daily_activity(self):
        c = FakeClient()
        c.s["recent_orders"] += [
            {"symbol":"AAA","client_order_id":"mi-auto-"+"a"*24,
             "submitted_at":"2026-11-10T15:01:00Z"},
            {"symbol":"BBB","client_order_id":"mi-auto-"+"b"*24,
             "submitted_at":"2026-11-10T15:02:00Z"},
        ]
        p = plan(client=c)
        self.assertIsNone(p["proposed_order"])
        self.assertEqual(p["daily_automation_orders"], 2)

    def test_no_buys_outside_market_hours(self):
        c = FakeClient()
        c.s["clock"]["is_open"] = False
        self.assertIsNone(plan(client=c)["proposed_order"])

    def test_symbol_cik_mismatch_skipped(self):
        c = FakeClient()
        def wrong(symbol):
            d = reference(symbol)
            d["cik"] = "0000000001"
            return d
        p = plan(client=c, ref=wrong)
        self.assertIsNone(p["proposed_order"])

    def test_etf_or_adr_skipped(self):
        c = FakeClient()
        def wrong(symbol):
            d = reference(symbol)
            d["type"] = "ETF"
            return d
        p = plan(client=c, ref=wrong)
        self.assertIsNone(p["proposed_order"])

    def test_broker_asset_ineligible_skipped(self):
        class BadAsset(FakeClient):
            def request(self, method, path, data=None):
                item = super().request(method,path,data)
                if path.startswith("/v2/assets/"):
                    item["fractionable"] = False
                return item
        p = plan(client=BadAsset())
        self.assertIsNone(p["proposed_order"])

    def test_deterministic_client_order_id(self):
        x = order_id(ACCOUNT, candidate())
        self.assertEqual(x, order_id(ACCOUNT, candidate()))
        self.assertTrue(x.startswith("mi-auto-"))
        self.assertEqual(len(x), 32)

    def test_history_over_500_refused(self):
        c = FakeClient()
        c.s["recent_orders"] *= 500
        with self.assertRaisesRegex(ValueError, "incomplete"):
            plan(client=c)

    def test_managed_allocation_never_sells(self):
        c = FakeClient()
        with tempfile.TemporaryDirectory() as tmp:
            run_one_cycle(c, [candidate()], expected_account=ACCOUNT,
                          execute=True, activation=AUTOMATION_OPT_IN,
                          now=NOW, reference_lookup=reference, intent_dir=tmp)
        self.assertTrue(all(o["side"] == "buy" for o in c.posts))
        self.assertTrue(all(o["notional"] == "100.00" for o in c.posts))

    def test_missing_candidates_holds_current_gme(self):
        p = plan([])
        self.assertEqual(len(p["held_positions"]), 1)
        self.assertIsNone(p["proposed_order"])


if __name__ == "__main__":
    unittest.main()
