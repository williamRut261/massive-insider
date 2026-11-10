"""SEC Form 4 research verification. All tests offline; no secrets or orders."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from insider.filings import (
    check_sec_xml_url, parse_form4, load_verified_filings, rank_candidates,
)

URL1 = "https://www.sec.gov/Archives/edgar/data/12345/000012345626000001/form4.xml"
URL2 = "https://www.sec.gov/Archives/edgar/data/12345/000012345626000002/form4.xml"
URL3 = "https://www.sec.gov/Archives/edgar/data/12345/000012345626000003/form4.xml"


def form(owner_cik="0000000555", code="P", direction="A", shares="100",
         price="50", ownership="D", title="Common Stock",
         plan="0", doc_type="4", second_owner=None,
         txn_date="2026-10-15"):
    def owner(cik):
        return f"""
        <reportingOwner>
          <reportingOwnerId><rptOwnerCik>{cik}</rptOwnerCik>
            <rptOwnerName>Example Reporting Person</rptOwnerName></reportingOwnerId>
          <reportingOwnerRelationship>
            <isDirector>1</isDirector><isOfficer>0</isOfficer>
            <isTenPercentOwner>0</isTenPercentOwner>
          </reportingOwnerRelationship>
        </reportingOwner>"""
    more = owner(second_owner) if second_owner else ""
    return f"""<?xml version="1.0"?>
    <ownershipDocument>
      <documentType>{doc_type}</documentType>
      <issuer><issuerCik>0000012345</issuerCik>
        <issuerTradingSymbol>TEST</issuerTradingSymbol>
        <issuerName>Test Corporation</issuerName></issuer>
      {owner(owner_cik)}{more}
      <aff10b5One>{plan}</aff10b5One>
      <nonDerivativeTable>
        <nonDerivativeTransaction>
          <securityTitle><value>{title}</value></securityTitle>
          <transactionDate><value>{txn_date}</value></transactionDate>
          <transactionCoding><transactionCode>{code}</transactionCode></transactionCoding>
          <transactionAmounts>
            <transactionShares><value>{shares}</value></transactionShares>
            <transactionPricePerShare><value>{price}</value></transactionPricePerShare>
            <transactionAcquiredDisposedCode><value>{direction}</value></transactionAcquiredDisposedCode>
          </transactionAmounts>
          <ownershipNature>
            <directOrIndirectOwnership><value>{ownership}</value></directOrIndirectOwnership>
            <natureOfOwnership><value>Trust</value></natureOfOwnership>
          </ownershipNature>
        </nonDerivativeTransaction>
      </nonDerivativeTable>
    </ownershipDocument>"""


def scan(n=3):
    return {
        "scope": "ALL_MASSIVE_REPORTING_ISSUERS_NOT_VERIFIED_COMMON_STOCKS",
        "pagination_complete": True,
        "from_date": "2026-08-12",
        "as_of": "2026-11-10",
        "signals": [{
            "issuer_cik": str(12345+i).zfill(10),
            "historical_tickers": ["TEST"+str(i)],
            "review": True,
            "purchase_not_marked_planned_usd": 500000.0+i,
            "sale_usd": 0.0,
            "same_day_multiple_purchasers": i==0
        } for i in range(n)]
    }


class SECVerifyTests(unittest.TestCase):
    def test_positive_price_common_stock_purchase(self):
        x = parse_form4(form(), source_url=URL1)
        self.assertEqual(x["issuer_cik"], "0000012345")
        self.assertEqual(x["reporting_owners"][0]["owner_cik"], "0000000555")
        self.assertEqual(x["qualifying_purchase_usd"], 5000)
        self.assertEqual(x["direct_qualifying_purchase_usd"], 5000)
        self.assertTrue(x["reporting_owners"][0]["director"])
        self.assertEqual(x["trading_plan_checkbox"], False)

    def test_non_purchase_codes_do_not_qualify(self):
        for code in ("M", "A", "G", "S", "F"):
            with self.subTest(code=code):
                self.assertEqual(
                    parse_form4(form(code=code), source_url=URL1)["qualifying_purchase_usd"], 0)

    def test_disposal_is_not_purchase(self):
        self.assertEqual(
            parse_form4(form(direction="D"), source_url=URL1)["qualifying_purchase_usd"], 0)

    def test_exclude_ambiguous_security_classes(self):
        self.assertEqual(
            parse_form4(form(title="American Depositary Shares"),
                        source_url=URL1)["qualifying_purchase_usd"], 0)

    def test_indirect_ownership_reported(self):
        parsed = parse_form4(form(ownership="I"), source_url=URL1)
        self.assertEqual(parsed["direct_qualifying_purchase_usd"], 0)
        self.assertTrue(parsed["has_indirect_qualifying_purchase"])
        self.assertEqual(parsed["non_derivative_transactions"][0]["nature_of_ownership"], "Trust")

    def test_form_amendment_separately_labeled(self):
        parsed = parse_form4(form(doc_type="4/A"), source_url=URL1)
        self.assertEqual(parsed["form_type"], "4/A")

    def test_malformed_sec_urls_refused(self):
        for url in (
            "https://evil.example/Archives/edgar/data/12345/000012345626000001/form4.xml",
            URL1 + "?apiKey=secret",
            URL1.replace("https:", "http:"),
            "https://www.sec.gov/cgi-bin/own-disp?CIK=12345",
            "https://www.sec.gov/Archives/edgar/data/12345/../form4.xml",
            URL1.replace("/form4.xml", "/../../etc/passwd"),
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                check_sec_xml_url(url)

    def test_xml_dtd_refused(self):
        with self.assertRaisesRegex(ValueError, "DTD"):
            parse_form4("<!DOCTYPE x SYSTEM 'http://evil'><ownershipDocument/>",
                        source_url=URL1)

    def test_wrong_issuer_cik_in_manifest_refused(self):
        with self.assertRaisesRegex(ValueError, "CIK disagrees"):
            load_verified_filings(
                {"filings": [{"url": URL1, "issuer_cik": "0000099999"}]},
                fetcher=lambda _: form(), sleep=lambda _: None)

    def test_duplicate_accession_refused(self):
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            load_verified_filings(
                {"filings": [{"url": URL1}, {"url": URL1}]},
                fetcher=lambda _: form(), sleep=lambda _: None)

    def test_sec_first_only_when_verified(self):
        parsed = parse_form4(form(), source_url=URL1)
        result = rank_candidates(scan(), [parsed], top_n=2)
        self.assertEqual(result["scanned_candidate_count"], 3)
        self.assertEqual(result["sec_verified_candidate_count"], 1)
        self.assertEqual(result["shortlist"][0]["issuer_cik"], "0000012345")
        self.assertEqual(result["shortlist"][0]["sec_confirmed_qualifying_purchase_usd"], 5000)
        self.assertFalse(result["shortlist"][0]["listed_common_stock_eligibility_verified"])
        self.assertFalse(result["shortlist"][0]["independent_economic_buyers_verified"])

    def test_joint_filing_is_one_owner_group(self):
        x = parse_form4(form(second_owner="0000000777"), source_url=URL1)
        row = rank_candidates(scan(1), [x])["shortlist"][0]
        self.assertEqual(row["distinct_reporting_owner_ciks"], 2)
        self.assertEqual(row["conservative_related_owner_group_count"], 1)

    def test_explicit_evidence_group_merges_filers(self):
        x = parse_form4(form(owner_cik="0000000555"), source_url=URL1)
        y = parse_form4(form(owner_cik="0000000777", shares="200"), source_url=URL2)
        row = rank_candidates(scan(1), [x, y],
            related_owner_groups=[{"owner_ciks": ["0000000555", "0000000777"],
                                   "evidence_url": URL3}])["shortlist"][0]
        self.assertEqual(row["conservative_related_owner_group_count"], 1)

    def test_group_without_evidence_refused(self):
        x = parse_form4(form(), source_url=URL1)
        with self.assertRaisesRegex(ValueError, "evidence"):
            rank_candidates(scan(1), [x],
                related_owner_groups=[{"owner_ciks": ["0000000555", "0000000777"]}])

    def test_prevent_double_count_same_trade_across_filings(self):
        x = parse_form4(form(owner_cik="0000000555"), source_url=URL1)
        y = parse_form4(form(owner_cik="0000000777"), source_url=URL2)
        row = rank_candidates(scan(1), [x,y])["shortlist"][0]
        self.assertEqual(row["sec_confirmed_qualifying_purchase_usd"], 5000)
        self.assertEqual(row["sec_qualifying_transaction_count"], 1)

    def test_amendment_does_not_repeat_purchase(self):
        x = parse_form4(form(doc_type="4/A"), source_url=URL1)
        row = rank_candidates(scan(1), [x])["shortlist"][0]
        self.assertEqual(row["sec_verification_status"], "SEC_FILES_REVIEWED_NO_QUALIFYING_P")
        self.assertEqual(row["sec_confirmed_qualifying_purchase_usd"], 0)

    def test_future_transaction_does_not_qualify(self):
        x = parse_form4(form(txn_date="2026-11-11"), source_url=URL1)
        row = rank_candidates(scan(1), [x])["shortlist"][0]
        self.assertEqual(row["sec_confirmed_qualifying_purchase_usd"], 0)

    def test_incomplete_marketwide_scan_refused(self):
        s = scan()
        s["pagination_complete"] = False
        with self.assertRaisesRegex(ValueError, "incomplete"):
            rank_candidates(s, [])

    def test_offline_cli_ranks_all_candidates_without_claiming_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            inp = Path(tmp) / "scan.json"
            out = Path(tmp) / "verify.json"
            inp.write_text(json.dumps(scan(306)))
            proc = subprocess.run(
                [sys.executable, "-m", "insider", "verify", "--input", str(inp),
                 "--output", str(out), "--top", "20"],
                capture_output=True, text=True, check=True)
            result = json.loads(out.read_text())
            self.assertEqual(len(result["all_candidates"]), 306)
            self.assertEqual(len(result["shortlist"]), 20)
            self.assertEqual(result["sec_verified_candidate_count"], 0)
            self.assertTrue(all(
                x["sec_verification_status"] == "UNVERIFIED_MASSIVE_AGGREGATE"
                for x in result["all_candidates"]))
            self.assertIn("NO ORDERS", proc.stdout)


if __name__ == "__main__":
    unittest.main()
