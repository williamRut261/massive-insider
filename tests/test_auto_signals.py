"""Massive Form 4 event and ticker reference regression tests (offline)."""
import unittest
from datetime import date, timedelta
from urllib.parse import parse_qs, urlsplit

from insider.auto_signals import fetch_recent_form4, rank_current_purchases, API_URL
from insider.identity import verified_current_common_stock, ticker_reference


def row(**kw):
    x = {
        "issuer_cik": "0001326380", "owner_cik": "0001326381",
        "accession_number": "0001326380-26-000123",
        "tickers": ["GME", "GME.WS"], "filing_date": "2026-11-09",
        "transaction_date": "2026-11-06", "transaction_code": "P",
        "transaction_acquired_disposed": "A", "form_type": "4",
        "record_type": "transaction", "security_type": "non_derivative",
        "security_title": "Class A Common Stock", "direct_or_indirect": "D",
        "aff_10b5_one": False, "is_director": True, "is_officer": False,
        "equity_swap_involved": False, "transaction_shares": 1000,
        "transaction_price_per_share": 45,
        "transaction_value": 45000, "owner_name": "Sample Director",
    }
    x.update(kw)
    return x


def rank(rows):
    return rank_current_purchases(rows, "2026-11-10")


class AutomaticSignalsTests(unittest.TestCase):
    def test_recent_direct_officer_purchase(self):
        x = rank([row()])
        self.assertEqual(len(x), 1)
        self.assertEqual(x[0]["symbol"], "GME")
        self.assertEqual(x[0]["purchase_usd"], 45000)
        self.assertEqual(x[0]["reference_accessions"], ["0001326380-26-000123"])

    def test_same_day_duplicate_economic_trade_is_only_once(self):
        a = row()
        b = row(owner_cik="0001326382",
                accession_number="0001326382-26-000001")
        x = rank([a, b])
        self.assertEqual(x[0]["purchase_usd"], 45000)
        self.assertEqual(x[0]["reporting_owner_count_unverified_independence"], 1)

    def test_distinct_buyer_transactions_grouped(self):
        a = row()
        b = row(owner_cik="0001326382", accession_number="0001326382-26-000001",
                transaction_shares=2000, transaction_value=90000)
        x = rank([a, b])
        self.assertEqual(x[0]["reporting_owner_count_unverified_independence"], 2)
        self.assertEqual(x[0]["purchase_usd"], 135000)

    def test_exclude_scheduled_and_indirect_and_institution_only(self):
        for change in (
            {"aff_10b5_one": True},
            {"aff_10b5_one": None},
            {"direct_or_indirect": "I"},
            {"is_director": False},
            {"transaction_code": "M"},
            {"security_type": "derivative"},
            {"record_type": "holding"},
            {"transaction_acquired_disposed": "D"},
            {"form_type": "4/A"},
            {"security_title": "Common Stock Option"},
            {"equity_swap_involved": True},
        ):
            with self.subTest(change=change):
                self.assertEqual(rank([row(**change)]), [])

    def test_filings_from_today_not_eligible(self):
        self.assertEqual(rank([row(filing_date="2026-11-10",
                                   transaction_date="2026-11-10")]), [])

    def test_stale_filing_not_eligible(self):
        self.assertEqual(rank([row(filing_date="2026-10-31",
                                   transaction_date="2026-10-30")]), [])

    def test_ambiguous_listed_share_class_not_eligible(self):
        self.assertEqual(rank([row(tickers=["AAA", "BBB"])]), [])

    def test_malformed_cik_not_eligible(self):
        self.assertEqual(rank([row(issuer_cik="ABC")]), [])

    def test_unsupported_security_not_eligible(self):
        self.assertEqual(rank([row(security_title="Preferred Shares")]), [])

    def test_value_mismatch_not_eligible(self):
        self.assertEqual(rank([row(transaction_value=4000000)]), [])

    def test_future_transaction_date_refused(self):
        with self.assertRaisesRegex(ValueError, "future-date"):
            rank([row(transaction_date="2026-11-10")])

    def test_pagination_and_no_ticker_filter(self):
        today = date.today()
        next_url = API_URL + "?cursor=page2"
        calls = []
        sleeps = []
        def req(url, token):
            calls.append(url)
            if len(calls) == 1:
                return {"status": "OK", "results": [row()],
                        "next_url": next_url}
            return {"status": "OK", "results": [row(owner_cik="0001326382")]}
        received = fetch_recent_form4(today.isoformat(), token="TEST",
                                     request=req, pause=sleeps.append)
        self.assertEqual(len(received), 2)
        self.assertEqual(sleeps, [13.1])
        self.assertEqual(calls[1], next_url)
        q = parse_qs(urlsplit(calls[0]).query)
        self.assertEqual(q["transaction_code"], ["P"])
        self.assertNotIn("tickers", q)

    def test_unsafe_pagination_fails(self):
        with self.assertRaises(ValueError):
            fetch_recent_form4(date.today().isoformat(), token="TEST",
                request=lambda u,t: {"status":"OK", "results":[row()],
                                     "next_url":"https://evil.test/steal"},
                pause=lambda _:None)

    def test_invalid_pagination_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, "Incomplete"):
            fetch_recent_form4(date.today().isoformat(), token="TEST",
                request=lambda u,t: {"status":"OK", "results":[row()],
                                     "next_url":API_URL+"?cursor=a"},
                max_pages=1, pause=lambda _:None)

    def test_massive_reference_validates_cik_and_common_stock(self):
        candidate = {"symbol": "GME", "issuer_cik": "0001326380"}
        good = {"ticker":"GME", "cik":"0001326380", "type":"CS",
                "active":True, "market":"stocks", "locale":"us",
                "primary_exchange":"XNYS"}
        self.assertTrue(verified_current_common_stock(candidate, good))
        for change in ({"type": "ETF"}, {"active": False},
                       {"cik": "0000000001"}, {"market":"otc"},
                       {"locale":"gb"}, {"primary_exchange":"OTCM"}):
            with self.subTest(change=change):
                d = dict(good)
                d.update(change)
                self.assertFalse(verified_current_common_stock(candidate,d))

    def test_reference_parses_real_api_payload(self):
        raw = {"status":"OK", "results": {
            "ticker":"GME", "cik":"0001326380", "type":"CS",
            "active":True, "market":"stocks", "locale":"us",
            "primary_exchange":"XNYS"}}
        details = ticker_reference("GME", token="TEST",
                                   request=lambda url, token: raw)
        self.assertEqual(details["ticker"], "GME")
