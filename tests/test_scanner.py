import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from insider.scanner import (
    ENDPOINT, UNIVERSE, check_url, fetch_pages, summarize
)


def sample(ticker="TSLA", cik="0001318605", transaction="2026-11-02",
           filing="2026-11-04", buy=500000.0, planned=0.0, purchasers=2,
           sell=0.0, planned_sell=0.0):
    return {
        "tickers": [ticker], "issuer_cik": cik,
        "transaction_date": transaction, "last_filing_date": filing,
        "purchase_value": buy, "planned_purchase_value": planned,
        "purchaser_count": purchasers,
        "sale_value": sell, "planned_sale_value": planned_sell,
    }


class ScanTests(unittest.TestCase):
    def test_universe_is_30_unique_equities(self):
        self.assertEqual(len(UNIVERSE), 30)
        self.assertEqual(len(set(UNIVERSE)), 30)
        self.assertNotIn("SPY", UNIVERSE)

    def test_high_unplanned_buy_is_review_candidate(self):
        res = summarize([sample()], "2026-11-10")
        self.assertTrue(res[0]["review"])
        self.assertEqual(res[0]["purchase_not_marked_planned_usd"], 500000)
        self.assertTrue(res[0]["same_day_multiple_purchasers"])

    def test_planned_purchase_is_not_unplanned_signal(self):
        res = summarize([sample(buy=500000, planned=500000)], "2026-11-10")
        self.assertFalse(res[0]["review"])
        self.assertEqual(res[0]["purchase_not_marked_planned_usd"], 0)

    def test_mixed_purchase_only_unplanned_component_counts(self):
        res = summarize([sample(buy=500000, planned=300000)], "2026-11-10")
        self.assertFalse(res[0]["review"])
        self.assertEqual(res[0]["purchase_not_marked_planned_usd"], 200000)

    def test_future_filing_excluded_from_asof(self):
        self.assertEqual(summarize(
            [sample(filing="2026-11-11")], "2026-11-10"), [])

    def test_stale_filing_excluded_from_window(self):
        self.assertEqual(summarize(
            [sample(transaction="2026-09-01", filing="2026-09-02")],
            "2026-11-10"), [])

    def test_later_filing_is_conservatively_used(self):
        self.assertEqual(summarize(
            [sample(transaction="2026-11-02", filing="2026-11-04")],
            "2026-11-03"), [])

    def test_cannot_sum_distinct_purchasers_across_days(self):
        rows = [sample(purchasers=1),
                sample(transaction="2026-11-03", filing="2026-11-05",
                       buy=10000, purchasers=1)]
        res = summarize(rows, "2026-11-10")
        self.assertFalse(res[0]["same_day_multiple_purchasers"])
        self.assertEqual(res[0]["max_distinct_purchasers_on_one_day"], 1)

    def test_aliases_not_double_counted(self):
        row = sample(ticker="NBIS", cik="0001513845")
        row["tickers"] = ["NBIS", "YNDX"]
        self.assertEqual(len(summarize([row], "2026-11-10")), 1)

    def test_duplicate_issuer_date_refused(self):
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            summarize([sample(), sample()], "2026-11-10")

    def test_planned_gt_total_refused(self):
        with self.assertRaisesRegex(ValueError, "Planned"):
            summarize([sample(buy=1, planned=5)], "2026-11-10")

    def test_nonfinite_values_refused(self):
        with self.assertRaisesRegex(ValueError, "Non-finite"):
            summarize([sample(buy=float("nan"))], "2026-11-10")

    def test_future_transaction_relative_to_filing_refused(self):
        with self.assertRaisesRegex(ValueError, "after filing"):
            summarize([sample(transaction="2026-11-05", filing="2026-11-04")],
                      "2026-11-10")

    def test_unsafe_host_or_apikey_refused(self):
        for url in (
            "http://api.massive.com/stocks/insiders/vX/activity",
            "https://evil.example/stocks/insiders/vX/activity",
            ENDPOINT + "?apiKey=EXPOSED",
            "https://api.massive.com@evil.example/stocks/insiders/vX/activity",
            "https://api.massive.com/v2/orders",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                check_url(url)

    def test_fetch_complete_two_pages(self):
        calls = []
        sleeps = []
        next_url = ENDPOINT + "?cursor=abc"
        def fake_get(url, token):
            calls.append((url, token))
            return {"status": "OK", "results": [sample()],
                    "next_url": next_url} if len(calls) == 1 else {
                        "status": "OK",
                        "results": [sample(transaction="2026-11-03", filing="2026-11-05")]}
        rows = fetch_pages("2026-11-01", "2026-11-10", token="mock",
                           request=fake_get, pause=sleeps.append)
        self.assertEqual(len(rows), 2)
        self.assertEqual(sleeps, [13.1])
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(token == "mock" for _, token in calls))

    def test_bad_pagination_host_refused(self):
        def fake_get(url, token):
            return {"status": "OK", "results": [], "next_url": "https://evil.example"}
        with self.assertRaises(ValueError):
            fetch_pages("2026-11-01", "2026-11-10", token="mock",
                        request=fake_get, pause=lambda _: None)

    def test_pagination_loop_refused(self):
        def fake_get(url, token):
            return {"status": "OK", "results": [], "next_url": ENDPOINT + "?cursor=abc"}
        with self.assertRaisesRegex(ValueError, "Repeated"):
            fetch_pages("2026-11-01", "2026-11-10", token="mock",
                        request=fake_get, pause=lambda _: None)

    def test_bad_status_refused(self):
        with self.assertRaisesRegex(ValueError, "Unexpected Massive"):
            fetch_pages("2026-11-01", "2026-11-10", token="mock",
                        request=lambda url, token: {"status": "ERROR", "results": []})

    def test_unknown_or_duplicate_symbol_refused(self):
        for symbols in (("TSLA", "TSLA"), ("BTC",), ("SPY",)):
            with self.subTest(symbols=symbols), self.assertRaises(ValueError):
                fetch_pages("2026-11-01", "2026-11-10",
                            symbols=symbols, token="mock",
                            request=lambda url, token: {"status": "OK", "results": []})

    def test_offline_cli_writes_research_json_only(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            inp, out = root / "sample.json", root / "signals.json"
            inp.write_text(json.dumps({"status": "OK", "results": [sample()],
                                       "next_url": ENDPOINT + "?cursor=2"}))
            cmd = [sys.executable, "-m", "insider", "scan",
                   "--input", str(inp), "--as-of", "2026-11-10",
                   "--output", str(out)]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            doc = json.loads(out.read_text())
            self.assertFalse(doc["pagination_complete"])
            self.assertEqual(doc["mode"], "READ_ONLY_RESEARCH_NO_ORDERS")
            self.assertEqual(len(doc["signals"]), 1)
            self.assertIn("NO ORDERS", result.stdout)


if __name__ == "__main__":
    unittest.main()
