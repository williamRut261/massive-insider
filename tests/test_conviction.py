"""Four-factor conviction scores from incomplete Form 4 data (offline)."""
import unittest

from insider.conviction import score_candidates, sale_pressure, SCORING_WEIGHTS


def candidate(symbol="AAA", issuer="0000000001", after=2000, bought=100):
    return {
        "symbol": symbol, "issuer_cik": issuer,
        "latest_filing_date": "2026-11-09",
        "purchase_usd": 100000.,
        "reference_accessions": ["0000000001-26-000001"],
        "transactions": [{
            "owner_cik": "0000000002", "shares": bought,
            "shares_owned_following_transaction": after,
            "value_usd": 100000.,
        }],
    }


def ref(symbol):
    return {
        "ticker": symbol, "cik": "0000000001", "type": "CS",
        "active": True, "market": "stocks", "locale": "us",
        "primary_exchange": "XNYS",
        "share_class_shares_outstanding": 100000,
    }


def sale(value=50000., code="S", direction="D"):
    return {
        "issuer_cik": "0000000001",
        "owner_cik": "0000000002",
        "filing_date": "2026-11-09",
        "transaction_date": "2026-11-06",
        "transaction_code": code, "form_type": "4",
        "record_type": "transaction", "security_type": "non_derivative",
        "security_title": "Common Stock",
        "direct_or_indirect": direction,
        "transaction_acquired_disposed": "D",
        "aff_10b5_one": False, "is_director": True,
        "transaction_shares": value / 10,
        "transaction_price_per_share": 10.,
        "transaction_value": value,
    }


def score(purchases=None, sales=None, reference=ref):
    return score_candidates(
        [candidate()] if purchases is None else purchases,
        [] if sales is None else sales,
        "2026-11-10", reference_lookup=reference, pause=lambda _: None)


class ConvictionTests(unittest.TestCase):
    def test_weight_sum_is_one(self):
        self.assertAlmostEqual(sum(SCORING_WEIGHTS.values()), 1.)

    def test_valid_purchase_emits_four_factor_score(self):
        rows = score()
        self.assertEqual(len(rows), 1)
        self.assertGreater(rows[0]["score"], 0)
        self.assertEqual(rows[0]["symbol"], "AAA")
        self.assertEqual(rows[0]["components"]["distinct_owner_count_proxy"], 1)
        self.assertAlmostEqual(rows[0]["components"]["purchased_shares_pct_outstanding"], .1)

    def test_bigger_personal_increase_gets_higher_score(self):
        a = score([candidate(after=200, bought=100)])[0]
        b = score([candidate(after=2000, bought=100)])[0]
        self.assertGreater(a["score"], b["score"])

    def test_more_owners_increase_score(self):
        a = candidate()
        b = candidate()
        b["transactions"].append({
            "owner_cik": "0000000003", "shares": 100,
            "shares_owned_following_transaction": 2000,
        })
        self.assertGreater(score([b])[0]["score"], score([a])[0]["score"])

    def test_bigger_pct_of_outstanding_scores_higher(self):
        a = score(reference=ref)[0]
        def larger_outstanding(symbol):
            v = ref(symbol)
            v["share_class_shares_outstanding"] = 1000000
            return v
        b = score(reference=larger_outstanding)[0]
        self.assertGreater(a["score"], b["score"])

    def test_sale_penalty_and_full_offset_exclusion(self):
        no_sales = score()[0]["score"]
        half = score(sales=[sale(50000)])[0]["score"]
        wiped = score(sales=[sale(120000)])
        self.assertLess(half, no_sales)
        self.assertEqual(wiped, [])

    def test_indirect_sale_excluded_from_pressure(self):
        baseline = score()[0]["score"]
        other = score(sales=[sale(50000, direction="I")])[0]["score"]
        self.assertEqual(baseline, other)

    def test_unplanned_sales_only(self):
        x = sale()
        x["aff_10b5_one"] = True
        self.assertEqual(score(sales=[x])[0]["components"]["discretionary_direct_sales_usd"], 0.) if False else None
        self.assertEqual(score(sales=[x])[0]["discretionary_direct_sales_usd"], 0.)

    def test_missing_personal_holdings_does_not_guess(self):
        self.assertEqual(score([candidate(after=None)]), [])

    def test_missing_share_count_skipped(self):
        def unknown(symbol):
            d = ref(symbol)
            del d["share_class_shares_outstanding"]
            return d
        self.assertEqual(score(reference=unknown), [])

    def test_wrong_company_identity_skipped(self):
        def mismatch(symbol):
            d = ref(symbol)
            d["cik"] = "0000000022"
            return d
        self.assertEqual(score(reference=mismatch), [])

    def test_independent_issuer_sales_only(self):
        x = sale()
        x["issuer_cik"] = "0000000022"
        self.assertEqual(score(sales=[x])[0]["discretionary_direct_sales_usd"], 0.)

    def test_duplicate_direct_sale_not_double_counted(self):
        self.assertEqual(round(sale_pressure([sale(), sale()], "2026-11-10")["0000000001"]), 50000)

    def test_as_of_time_excludes_today_sale(self):
        x = sale()
        x["transaction_date"] = "2026-11-10"
        x["filing_date"] = "2026-11-10"
        self.assertEqual(dict(sale_pressure([x], "2026-11-10")), {})

    def test_bad_transaction_date_fails_closed(self):
        x = sale()
        x["transaction_date"] = "2026-11-11"
        with self.assertRaises(ValueError):
            sale_pressure([x], "2026-11-10")

    def test_unbounded_research_not_permitted(self):
        with self.assertRaises(ValueError):
            score_candidates([], [], "2026-11-10", research_limit=999)

    def test_efficient_reference_pacing_applied(self):
        a = candidate(symbol="AAA")
        b = candidate(symbol="BBB", issuer="0000000002")
        seen = []
        def reference(symbol):
            d = ref(symbol)
            d["cik"] = {"AAA": "0000000001", "BBB":"0000000002"}[symbol]
            return d
        scores = score_candidates([a,b], [], "2026-11-10",
                                  reference_lookup=reference, pause=seen.append)
        self.assertEqual(len(scores), 2)
        self.assertEqual(len(seen), 1)


if __name__ == "__main__":
    unittest.main()
