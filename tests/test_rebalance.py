"""v0.6 PAPER-only 100% conviction target allocator tests; no network."""
import copy
import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal

from insider.rebalance import (
    build_targets, reconcile_holdings, plan_rebalance,
    run_rebalance_cycle, BUFFER_RATE,
)
from insider.portfolio import AUTOMATION_OPT_IN

NOW = datetime(2026, 11, 10, 15, 5, tzinfo=timezone.utc)  # 10:05 a.m. ET
ACCOUNT = "5a5d5e5f-19fc-4aa3-9873-7b8cce1b298f"


def signal(symbol, score):
    return {"symbol": symbol, "score": score}


def state():
    return {
        "account": {
            "id": ACCOUNT, "equity": "1000", "cash": "875", "status": "ACTIVE",
            "trading_blocked": False, "account_blocked": False,
            "trade_suspended_by_user": False
        },
        "positions": [
            {"symbol": "GME", "asset_class": "us_equity", "side": "long",
             "market_value": "25", "qty": "1"},
            {"symbol": "UPBD", "asset_class": "us_equity", "side": "long",
             "market_value": "100", "qty": "10"},
        ],
        "open_orders": [],
        "recent_orders": [
            {"symbol": "GME", "side": "buy",
             "client_order_id": "mi-paper-" + "a"*24,
             "filled_qty": "1", "status": "filled",
             "submitted_at": "2026-11-10T14:32:51Z"},
            {"symbol": "UPBD", "side": "buy",
             "client_order_id": "mi-auto-" + "b"*24,
             "filled_qty": "10", "status": "filled",
             "submitted_at": "2026-11-09T15:12:00Z"},
        ],
        "clock": {"is_open": True},
    }


class MockClient:
    def __init__(self, snapshot=None):
        self.snapshot = copy.deepcopy(snapshot or state())
        self.posts = []
        self.requested = []

    def request(self, method, path, data=None):
        self.requested.append((method, path))
        if method != "GET":
            raise AssertionError("Paper client requested illegal method")
        paths = {
            "/v2/account": "account",
            "/v2/positions": "positions",
            "/v2/orders?status=open&limit=500": "open_orders",
            "/v2/orders?status=all&limit=500": "recent_orders",
            "/v2/clock": "clock",
        }
        if path in paths:
            return copy.deepcopy(self.snapshot[paths[path]])
        if path.startswith("/v2/assets/"):
            return {
                "symbol": path.split("/")[-1], "class": "us_equity",
                "status": "active", "tradable": True,
                "fractionable": True, "exchange": "NASDAQ",
            }
        raise AssertionError("Illegal GET path " + path)

    def submit(self, order):
        self.posts.append(copy.deepcopy(order))
        self.snapshot["recent_orders"].append({
            "symbol": order["symbol"], "side": order["side"],
            "client_order_id": order["client_order_id"],
            "filled_qty": "0", "status": "pending_new",
            "submitted_at": "2026-11-10T15:05:00Z",
        })
        return {
            "symbol": order["symbol"],
            "client_order_id": order["client_order_id"],
            "id": "broker-paper-id-test", "status": "pending_new",
            "filled_qty": "0",
        }


def plan(signals=None, client=None, now=NOW):
    c = client or MockClient()
    return plan_rebalance(c, c.snapshot,
                          [signal("UPBD", 1.0)] if signals is None else signals,
                          expected_account=ACCOUNT, now=now)


class AggressiveRebalanceTests(unittest.TestCase):
    def test_one_stock_gets_full_investable_capital(self):
        legacy = {"GME": {"market_value_usd": "25"}}
        targets, budget = build_targets([signal("UPBD", 1.)],
                                        Decimal("1000"), legacy)
        self.assertEqual(budget, Decimal("970.00"))
        self.assertEqual(targets, {"UPBD": Decimal("970.00")})

    def test_equal_scores_split_evenly(self):
        t, b = build_targets(
            [signal("AAA", 1.), signal("BBB", 1.)],
            Decimal("1000"), {})
        self.assertEqual(b, Decimal("995.00"))
        self.assertEqual(t["AAA"] + t["BBB"], b)
        self.assertEqual(t["AAA"], Decimal("497.50"))

    def test_80_20_conviction_weighting(self):
        t, b = build_targets(
            [signal("AAA", .8), signal("BBB", .2)],
            Decimal("1000"), {})
        self.assertEqual((t["AAA"], t["BBB"]),
                         (Decimal("796.00"), Decimal("199.00")))

    def test_pilot_gme_is_protected(self):
        c = MockClient()
        x = plan(client=c)
        self.assertEqual(x["protected_positions"], ["GME"])
        self.assertEqual(x["manager_positions"], ["UPBD"])
        self.assertNotIn("GME", [t["symbol"] for t in x["target_weights"]])
        self.assertEqual(x["strategy_budget_usd"], "970.00")

    def test_existing_manager_upbd_position_topped_up(self):
        c = MockClient()
        x = plan(client=c)
        self.assertEqual(x["proposed_order"]["symbol"], "UPBD")
        self.assertEqual(x["proposed_order"]["side"], "buy")
        self.assertEqual(x["proposed_order"]["notional_usd"], "870.00")
        self.assertEqual(x["strategy_budget_usd"], "970.00")
        self.assertLessEqual(Decimal(x["proposed_order"]["notional_usd"]),
                             Decimal(x["cash_usd"])-Decimal("5"))

    def test_new_signal_splits_existing_allocation(self):
        c = MockClient()
        x = plan([signal("UPBD", .5), signal("AAA", .5)], client=c)
        self.assertEqual({t["symbol"]: t["target_usd"] for t in x["target_weights"]},
                         {"UPBD": "485.00", "AAA": "485.00"})
        self.assertEqual(x["proposed_order"]["symbol"], "UPBD")

    def test_overweight_manager_stock_sold_first(self):
        s = state()
        s["positions"][1]["market_value"] = "900"
        s["account"]["cash"] = "75"
        c = MockClient(s)
        x = plan([signal("UPBD", .2), signal("AAA", .8)], client=c)
        self.assertEqual(x["proposed_order"]["side"], "sell")
        self.assertEqual(x["proposed_order"]["symbol"], "UPBD")
        self.assertGreater(Decimal(x["proposed_order"]["qty"]), 0)

    def test_no_positive_signals_sell_manager_not_pilot(self):
        c = MockClient()
        x = plan([], client=c)
        self.assertEqual(x["proposed_order"]["symbol"], "UPBD")
        self.assertEqual(x["proposed_order"]["side"], "sell")
        self.assertEqual(x["proposed_order"]["qty"], "10")
        self.assertEqual(x["protected_positions"], ["GME"])

    def test_when_only_pilot_gme_no_auto_sell(self):
        s = state()
        s["positions"] = [s["positions"][0]]
        s["recent_orders"] = [s["recent_orders"][0]]
        s["account"]["cash"] = "975"
        c = MockClient(s)
        x = plan([], client=c)
        self.assertIsNone(x["proposed_order"])

    def test_unknown_external_stock_is_also_protected(self):
        s = state()
        s["positions"][1]["symbol"] = "AAA"
        s["recent_orders"][1]["client_order_id"] = "external"
        s["recent_orders"][1]["symbol"] = "AAA"
        c = MockClient(s)
        x = plan([signal("UPBD", 1.)], client=c)
        self.assertEqual(x["protected_positions"], ["AAA", "GME"])
        self.assertEqual(x["strategy_budget_usd"], "870.00")

    def test_partial_mixed_manual_order_blocks_manager_sell(self):
        s = state()
        s["recent_orders"].append({
            "symbol": "UPBD", "side": "buy",
            "client_order_id": "some-other-bot",
            "filled_qty": "0", "status": "canceled",
        })
        c = MockClient(s)
        x = plan([], client=c)
        self.assertIn("UPBD", x["protected_positions"])
        self.assertIsNone(x["proposed_order"])

    def test_quantity_mismatch_protects_position(self):
        s = state()
        s["positions"][1]["qty"] = "11"
        c = MockClient(s)
        x = plan([], client=c)
        self.assertIn("UPBD", x["protected_positions"])
        self.assertIsNone(x["proposed_order"])

    def test_pending_orders_block_rebalance(self):
        s = state()
        s["open_orders"].append({
            "symbol":"UPBD", "client_order_id":"mi-auto-"+"b"*24,
        })
        x = plan(client=MockClient(s))
        self.assertIsNone(x["proposed_order"])
        self.assertIn("open", x["block_reason"])

    def test_market_closed_no_orders(self):
        s = state()
        s["clock"]["is_open"] = False
        x = plan(client=MockClient(s))
        self.assertIsNone(x["proposed_order"])

    def test_one_order_per_execution_only(self):
        c = MockClient()
        with tempfile.TemporaryDirectory() as tmp:
            result = run_rebalance_cycle(
                c, [signal("UPBD", 1.)], expected_account=ACCOUNT,
                execute=True, activation=AUTOMATION_OPT_IN, now=NOW,
                intent_dir=tmp)
        self.assertEqual(result["status"],
                         "PAPER_REBALANCE_SUBMITTED_NOT_A_FILL")
        self.assertEqual(len(c.posts), 1)
        self.assertEqual(c.posts[0]["side"], "buy")
        self.assertEqual(c.posts[0]["symbol"], "UPBD")
        self.assertEqual(c.posts[0]["notional"], "870.00")
        self.assertNotIn("qty", c.posts[0])

    def test_explicit_activation_is_enforced(self):
        c = MockClient()
        with self.assertRaises(ValueError):
            run_rebalance_cycle(
                c, [signal("UPBD", 1.)], expected_account=ACCOUNT,
                execute=True, activation="", now=NOW)
        self.assertFalse(c.posts)

    def test_default_read_only_never_submits(self):
        c = MockClient()
        result = run_rebalance_cycle(
            c, [signal("UPBD", 1.)], expected_account=ACCOUNT, now=NOW)
        self.assertEqual(result["status"], "READ_ONLY_REBALANCE_PLAN")
        self.assertFalse(c.posts)

    def test_restart_does_not_repeat_same_side_ticker(self):
        c = MockClient()
        with tempfile.TemporaryDirectory() as one, tempfile.TemporaryDirectory() as two:
            a = run_rebalance_cycle(
                c, [signal("UPBD", 1.)], expected_account=ACCOUNT,
                execute=True, activation=AUTOMATION_OPT_IN, now=NOW,
                intent_dir=one)
            b = run_rebalance_cycle(
                c, [signal("UPBD", 1.)], expected_account=ACCOUNT,
                execute=True, activation=AUTOMATION_OPT_IN, now=NOW,
                intent_dir=two)
        self.assertEqual(len(c.posts), 1)
        self.assertEqual(b["status"], "NO_REBALANCE_ORDER")

    def test_local_idempotency_on_stale_broker(self):
        c = MockClient()
        with tempfile.TemporaryDirectory() as tmp:
            a = run_rebalance_cycle(
                c, [signal("UPBD", 1.)], expected_account=ACCOUNT,
                execute=True, activation=AUTOMATION_OPT_IN, now=NOW,
                intent_dir=tmp)
            c.snapshot["recent_orders"] = state()["recent_orders"]  # stale
            b = run_rebalance_cycle(
                c, [signal("UPBD", 1.)], expected_account=ACCOUNT,
                execute=True, activation=AUTOMATION_OPT_IN, now=NOW,
                intent_dir=tmp)
        self.assertEqual(len(c.posts), 1)
        self.assertEqual(b["status"], "ALREADY_ATTEMPTED_NO_RETRY")

    def test_cash_buffer_no_margin(self):
        s = state()
        s["account"]["cash"] = "6"
        c = MockClient(s)
        x = plan(client=c)
        if x["proposed_order"] is not None:
            self.assertLessEqual(Decimal(x["proposed_order"].get("notional_usd", "0")),
                                 Decimal("1"))

    def test_very_low_cash_no_buy(self):
        s = state()
        s["account"]["cash"] = "5"
        c = MockClient(s)
        x = plan(client=c)
        self.assertIsNone(x["proposed_order"])

    def test_account_mismatch_aborts(self):
        s = state()
        s["account"]["id"] = "00000000-0000-0000-0000-000000000000"
        with self.assertRaises(ValueError):
            plan(client=MockClient(s))

    def test_missing_order_history_fails_conservative(self):
        s = state()
        s["recent_orders"] = []
        c = MockClient(s)
        x = plan([], client=c)
        self.assertEqual(x["protected_positions"], ["GME", "UPBD"])
        self.assertIsNone(x["proposed_order"])

    def test_daily_guard(self):
        s = state()
        for i in range(10):
            s["recent_orders"].append({
                "symbol": "AAA", "side": "buy",
                "client_order_id": "mi-auto-"+ format(i, "024x"),
                "filled_qty":"0", "status":"canceled",
                "submitted_at":"2026-11-10T15:00:00Z"
            })
        x = plan(client=MockClient(s))
        self.assertIsNone(x["proposed_order"])
        self.assertIn("Maximum 10", x["block_reason"])

    def test_position_limit_including_gme(self):
        s = state()
        for symbol in ("A","AA","AAA","AAAA","B","BB","BBB","BBBB"):
            s["positions"].append({
                "symbol":symbol,"asset_class":"us_equity","side":"long",
                "market_value":"10","qty":"1"
            })
        c = MockClient(s)
        x = plan([signal("UPBD", .1), signal("XYZ", .9)], client=c)
        self.assertEqual(len(x["protected_positions"]), 9)
        self.assertEqual(len(x["target_weights"]), 1)
        self.assertEqual(x["target_weights"][0]["symbol"], "UPBD")

    def test_2p5pct_band_avoids_tiny_churn(self):
        s = state()
        s["account"]["cash"] = "5"
        s["positions"][1]["market_value"] = "970"
        c = MockClient(s)
        x = plan([signal("UPBD", 1.)], client=c)
        self.assertIsNone(x["proposed_order"])

    def test_paper_only_broker_paths(self):
        c = MockClient()
        with tempfile.TemporaryDirectory() as d:
            run_rebalance_cycle(
                c, [signal("UPBD", 1.)], expected_account=ACCOUNT,
                execute=True, activation=AUTOMATION_OPT_IN, now=NOW,
                intent_dir=d)
        self.assertTrue(all(method == "GET" and path.startswith("/v2/")
                            for method, path in c.requested))
        self.assertTrue(all(x["type"] == "market" and
                            x["extended_hours"] is False
                            for x in c.posts))


if __name__ == "__main__":
    unittest.main()
