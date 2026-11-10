"""Offline tests for market-wide capture and non-trading safety boundaries."""
import json
import subprocess
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path

from insider.market import fetch_marketwide, summarize_marketwide
from insider.scanner import ENDPOINT


def record(ticker="ABCX", cik="0000001234", date="2026-10-25",
           filed="2026-10-27", buy=500000, planned=0, sell=40000,
           planned_sell=10000, purchasers=2):
    return {
        "issuer_cik": cik, "issuer_name": "Example Issuer Corp",
        "tickers": [ticker], "transaction_date": date,
        "last_filing_date": filed,
        "purchase_value": buy, "planned_purchase_value": planned,
        "sale_value": sell, "planned_sale_value": planned_sell,
        "purchaser_count": purchasers,
    }


class MarketWideTests(unittest.TestCase):
    def test_no_ticker_filter_in_request(self):
        urls = []
        def fake_request(url, token):
            urls.append(url)
            self.assertEqual(token, "mock_secret")
            return {"status": "OK", "results": [record()]}
        result = fetch_marketwide("2026-10-01", "2026-11-10", token="mock_secret",
                                  request=fake_request, pause=lambda _: None)
        self.assertEqual(len(result), 1)
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(urls[0]).query)
        self.assertNotIn("tickers.any_of", query)
        self.assertNotIn("apiKey", query)
        self.assertEqual(query["limit"], ["5000"])
        self.assertEqual(query["transaction_date.gte"], ["2026-10-01"])

    def test_responds_to_pagination(self):
        calls, pauses = [], []
        next_url = ENDPOINT + "?cursor=next"
        def request(url, token):
            calls.append(url)
            if len(calls) == 1:
                return {"status": "OK", "results": [record()], "next_url": next_url}
            return {"status": "OK", "results": [record(
                ticker="XYZQ", cik="0000005678")]}
        rows = fetch_marketwide("2026-10-01", "2026-11-10", token="dummy",
                                request=request, pause=pauses.append)
        self.assertEqual(len(rows), 2)
        self.assertEqual(pauses, [13.1])
        self.assertEqual(calls[-1], next_url)

    def test_missing_token_refused(self):
        with self.assertRaises(ValueError):
            fetch_marketwide("2026-10-01", "2026-11-10", token="")

    def test_bad_window_refused(self):
        with self.assertRaises(ValueError):
            fetch_marketwide("2026-11-10", "2026-10-01", token="dummy")

    def test_redirect_or_apikey_next_url_refused(self):
        for url in ("https://attacker.example/data", ENDPOINT + "?apiKey=secret"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                fetch_marketwide(
                    "2026-10-01", "2026-11-10", token="dummy",
                    request=lambda u, t: {
                        "status": "OK", "results": [record()], "next_url": url},
                    pause=lambda _: None)

    def test_repeated_next_url_refused(self):
        with self.assertRaisesRegex(ValueError, "Repeated"):
            fetch_marketwide(
                "2026-10-01", "2026-11-10", token="dummy",
                request=lambda u, t: {
                    "status": "OK", "results": [record()],
                    "next_url": ENDPOINT + "?cursor=abc"},
                pause=lambda _: None)

    def test_page_cap_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, "Pagination incomplete"):
            fetch_marketwide(
                "2026-10-01", "2026-11-10", token="dummy", max_pages=1,
                request=lambda u, t: {
                    "status": "OK", "results": [record()],
                    "next_url": ENDPOINT + "?cursor=abc"})

    def test_both_issuer_types_are_kept_and_not_tradable(self):
        rows = [record(), record(ticker="NVDA", cik="0001045810")]
        result = summarize_marketwide(rows, from_date="2026-10-01",
                                      as_of="2026-11-10", window_days=90)
        self.assertEqual(len(result), 2)
        self.assertTrue(all(not item["verified_current_us_listed_common_stock"]
                            for item in result))
        self.assertTrue(any("ABCX" in item["historical_tickers"] for item in result))

    def test_purchase_flag_is_not_a_trade(self):
        item = summarize_marketwide([record()], from_date="2026-10-01",
                                    as_of="2026-11-10")[0]
        self.assertTrue(item["review"])
        self.assertEqual(item["purchase_not_marked_planned_usd"], 500000)
        self.assertTrue(item["same_day_multiple_purchasers"])
        self.assertIn("Research only", item["note"])

    def test_planned_buy_does_not_trigger(self):
        item = summarize_marketwide([record(planned=500000)],
                                    from_date="2026-10-01",
                                    as_of="2026-11-10")[0]
        self.assertFalse(item["review"])
        self.assertEqual(item["purchase_not_marked_planned_usd"], 0)

    def test_asof_prevents_future_filing(self):
        item = summarize_marketwide(
            [record(date="2026-10-25", filed="2026-11-11")],
            from_date="2026-10-01", as_of="2026-11-10")
        self.assertEqual(item, [])

    def test_preserves_issuer_aliases(self):
        row = record(ticker="ALCC")
        row["tickers"] = ["ALCC", "OKLO"]
        items = summarize_marketwide([row], from_date="2026-10-01",
                                     as_of="2026-11-10")
        self.assertEqual(items[0]["historical_tickers"], ["ALCC", "OKLO"])
        self.assertEqual(len(items), 1)

    def test_issuer_without_ticker_is_research_only(self):
        row = record()
        row["tickers"] = []
        items = summarize_marketwide([row], from_date="2026-10-01",
                                     as_of="2026-11-10")
        self.assertEqual(items[0]["historical_tickers"], [])
        self.assertFalse(items[0]["verified_current_us_listed_common_stock"])

    def test_duplicate_issuer_date_rejected(self):
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            summarize_marketwide(
                [record(), record()], from_date="2026-10-01",
                as_of="2026-11-10")

    def test_bad_planned_amount_rejected(self):
        with self.assertRaisesRegex(ValueError, "Planned"):
            summarize_marketwide(
                [record(buy=100, planned=200)], from_date="2026-10-01",
                as_of="2026-11-10")

    def test_offline_cli_market_wide_does_not_filter_unknown_tickers(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            inp = root / "input.json"
            out = root / "signals.json"
            raw = root / "raw.json"
            inp.write_text(json.dumps({
                "status": "OK", "results": [record(ticker="ABCD")],
                "pagination_complete": True}))
            proc = subprocess.run(
                [sys.executable, "-m", "insider", "scan",
                 "--market-wide", "--input", str(inp),
                 "--from-date", "2026-10-01", "--as-of", "2026-11-10",
                 "--raw-output", str(raw), "--output", str(out)],
                capture_output=True, text=True, check=True)
            data = json.loads(out.read_text())
            self.assertEqual(data["issuer_count"], 1)
            self.assertEqual(data["research_candidate_count"], 1)
            self.assertIn("ALL_MASSIVE_REPORTING_ISSUERS", data["scope"])
            self.assertTrue(data["pagination_complete"])
            self.assertEqual(len(json.loads(raw.read_text())["results"]), 1)
            self.assertIn("NO ORDERS", proc.stdout)

    def test_online_cli_requires_raw_capture(self):
        proc = subprocess.run(
            [sys.executable, "-m", "insider", "scan",
             "--market-wide", "--from-date", "2026-10-01",
             "--as-of", "2026-11-10"],
            capture_output=True, text=True)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("--raw-output", proc.stderr)

    def test_incomplete_offline_source_is_labeled(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            inp, out = root / "in.json", root / "out.json"
            inp.write_text(json.dumps({
                "status": "OK", "results": [record()],
                "next_url": ENDPOINT + "?cursor=more"}))
            subprocess.run([
                sys.executable, "-m", "insider", "scan",
                "--market-wide", "--input", str(inp),
                "--from-date", "2026-10-01", "--as-of", "2026-11-10",
                "--output", str(out)], capture_output=True, check=True)
            self.assertFalse(json.loads(out.read_text())["pagination_complete"])


if __name__ == "__main__":
    unittest.main()
