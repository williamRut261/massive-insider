"""SEC Form 4/4-A XML verification of reporting owner identities and transactions.

Fail closed on untrusted URLs and malformed filings. Reporting owner CIKs identify
SEC filers, NOT independent economic decision-makers. This module neither trades
nor connects to a broker.
"""
from __future__ import annotations

import json
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import date

ARCHIVE_RE = re.compile(
    r"^/Archives/edgar/data/[0-9]{1,10}/([0-9]{18})/[A-Za-z0-9][A-Za-z0-9_.-]*[.]xml$"
)
MAX_XML_BYTES = 4_000_000


def check_sec_xml_url(url: str) -> str:
    p = urllib.parse.urlsplit(url)
    if (p.scheme != "https" or p.hostname != "www.sec.gov" or
            p.port is not None or p.username or p.password or
            p.query or p.fragment or not ARCHIVE_RE.fullmatch(p.path) or
            ".." in p.path or "%" in p.path):
        raise ValueError("Only an exact SEC EDGAR archive Form 4 XML URL is allowed")
    return ARCHIVE_RE.fullmatch(p.path).group(1)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("SEC redirect refused")


def fetch_sec_xml(url: str, *, user_agent: str | None = None) -> str:
    """GET an explicitly referenced XML document, with declared SEC user agent."""
    check_sec_xml_url(url)
    agent = user_agent if user_agent is not None else os.getenv("SEC_USER_AGENT", "")
    if not agent or "@" not in agent or len(agent) > 200:
        raise ValueError("Set SEC_USER_AGENT to your project and contact email")
    req = urllib.request.Request(
        url, method="GET",
        headers={"User-Agent": agent, "Accept": "application/xml,text/xml"})
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=30) as res:
            data = res.read(MAX_XML_BYTES + 1)
    except urllib.error.HTTPError as e:
        raise RuntimeError("SEC returned HTTP " + str(e.code)) from None
    except urllib.error.URLError:
        raise RuntimeError("SEC filing retrieval failed") from None
    if len(data) > MAX_XML_BYTES:
        raise ValueError("SEC XML exceeds size limit")
    return data.decode("utf-8-sig")


def _text(node, xpath, default=""):
    val = node.findtext(xpath)
    return val.strip() if val is not None else default


def _cik(value):
    if not value or not value.isdigit() or len(value) > 10:
        raise ValueError("Malformed SEC CIK")
    return value.zfill(10)


def _bool(value):
    v = value.strip().lower()
    if v in ("true", "1"):
        return True
    if v in ("false", "0"):
        return False
    return None


def _positive(value, field):
    try:
        x = float(value)
    except (TypeError, ValueError):
        raise ValueError("Malformed " + field) from None
    if not math.isfinite(x) or x < 0:
        raise ValueError("Invalid " + field)
    return x


def parse_form4(xml_data: str, *, source_url: str) -> dict:
    """Parse an actual SEC ownershipDocument; no claims about filing-time verification."""
    accession = check_sec_xml_url(source_url)
    if len(xml_data.encode("utf-8")) > MAX_XML_BYTES:
        raise ValueError("Excessively long XML")
    if "<!DOCTYPE" in xml_data.upper() or "<!ENTITY" in xml_data.upper():
        raise ValueError("XML DTD/entities not supported")
    root = ET.fromstring(xml_data)
    if root.tag != "ownershipDocument":
        raise ValueError("Not a Form 4 ownershipDocument")
    form_type = _text(root, "documentType")
    if form_type not in ("4", "4/A"):
        raise ValueError("Only Form 4 and 4/A are supported")
    cik = _cik(_text(root, "issuer/issuerCik"))
    symbol = _text(root, "issuer/issuerTradingSymbol")
    owners = []
    for r in root.findall("reportingOwner"):
        owner_cik = _cik(_text(r, "reportingOwnerId/rptOwnerCik"))
        role = r.find("reportingOwnerRelationship")
        if role is None:
            raise ValueError("Missing reporting owner relationship")
        owners.append({
            "owner_cik": owner_cik,
            "owner_name": _text(r, "reportingOwnerId/rptOwnerName"),
            "director": _bool(_text(role, "isDirector")) is True,
            "officer": _bool(_text(role, "isOfficer")) is True,
            "ten_percent_owner": _bool(_text(role, "isTenPercentOwner")) is True,
            "officer_title": _text(role, "officerTitle"),
        })
    if not owners or len({o["owner_cik"] for o in owners}) != len(owners):
        raise ValueError("Missing or duplicated reporting owners")
    plan_flag = _bool(_text(root, "aff10b5One"))
    transactions = []
    for item in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        code = _text(item, "transactionCoding/transactionCode")
        direction = _text(item, "transactionAmounts/transactionAcquiredDisposedCode/value")
        title = _text(item, "securityTitle/value")
        txn_date = _text(item, "transactionDate/value")
        if txn_date:
            date.fromisoformat(txn_date)
        mode = _text(item, "ownershipNature/directOrIndirectOwnership/value")
        nature = _text(item, "ownershipNature/natureOfOwnership/value")
        shares_s = _text(item, "transactionAmounts/transactionShares/value")
        price_s = _text(item, "transactionAmounts/transactionPricePerShare/value")
        shares = _positive(shares_s, "shares") if shares_s else None
        price = _positive(price_s, "price") if price_s else None
        # Only positive-price, non-derivative P/A transactions explicitly
        # described as common stock/shares qualify for the research subtotal.
        qualifying = (code == "P" and direction == "A" and
                      "common" in title.lower() and shares is not None and shares > 0
                      and price is not None and price > 0)
        transactions.append({
            "date": txn_date,
            "code": code,
            "direction": direction,
            "security_title": title,
            "ownership": mode,
            "nature_of_ownership": nature,
            "shares": shares,
            "price": price,
            "open_market_common_purchase": qualifying,
            "purchase_usd": round(shares * price, 2) if qualifying else 0.0,
        })
    return {
        "source_url": source_url,
        "accession_number": accession,
        "issuer_cik": cik,
        "issuer_symbol_as_filed": symbol,
        "issuer_name_as_filed": _text(root, "issuer/issuerName"),
        "form_type": form_type,
        "owner_count_on_filing": len(owners),
        "reporting_owners": owners,
        "trading_plan_checkbox": plan_flag,
        "non_derivative_transactions": transactions,
        "qualifying_purchase_usd": round(
            sum(x["purchase_usd"] for x in transactions), 2),
        "direct_qualifying_purchase_usd": round(
            sum(x["purchase_usd"] for x in transactions
                if x["ownership"] == "D"), 2),
        "has_indirect_qualifying_purchase": any(
            x["open_market_common_purchase"] and x["ownership"] != "D"
            for x in transactions),
        "warnings": [
            "Filing owners may be related/co-reporters; CIK counts do not prove independence.",
            "Filing may report same economic shares as another Form 4.",
            "Trading-plan checkbox is not conclusive evidence of discretionary trades.",
            "Filing acceptance timestamp was not independently retrieved.",
        ],
    }


def load_verified_filings(manifest: dict, *, fetcher=fetch_sec_xml,
                          sleep=time.sleep) -> list[dict]:
    """Load explicit SEC XML references only; never crawl guessed filings."""
    if not isinstance(manifest, dict) or not isinstance(manifest.get("filings"), list):
        raise ValueError("Manifest must contain a filings list")
    out, seen = [], set()
    for index, entry in enumerate(manifest["filings"]):
        if not isinstance(entry, dict):
            raise ValueError("Invalid filing manifest entry")
        url = entry.get("url")
        if not isinstance(url, str):
            raise ValueError("Every filing requires an exact SEC XML URL")
        accession = check_sec_xml_url(url)
        if accession in seen:
            raise ValueError("Duplicate SEC accession")
        seen.add(accession)
        if index:
            sleep(1.25)
        data = fetcher(url)
        parsed = parse_form4(data, source_url=url)
        if entry.get("issuer_cik") and _cik(str(entry["issuer_cik"])) != parsed["issuer_cik"]:
            raise ValueError("SEC filing issuer CIK disagrees with manifest")
        # Never assert acceptance date/time from a user-supplied field.
        out.append(parsed)
    return out


def _related_group_map(filings: list[dict], declared_groups: list[dict]) -> dict:
    """Conservatively merge joint filers and *explicitly documented* related owners."""
    parent = {}

    def root(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def join(items):
        ids = list(items)
        if ids:
            first = root(ids[0])
            for other in ids[1:]:
                parent[root(other)] = root(first)

    for filing in filings:
        join([o["owner_cik"] for o in filing["reporting_owners"]])
    if not isinstance(declared_groups, list):
        raise ValueError("related_owner_groups must be a list")
    for group in declared_groups:
        if not isinstance(group, dict) or not group.get("evidence_url"):
            raise ValueError("Owner relationship merges need an evidence URL")
        evidence = urllib.parse.urlsplit(group["evidence_url"])
        if (evidence.scheme != "https" or evidence.hostname != "www.sec.gov"
                or evidence.username or evidence.password):
            raise ValueError("Related-group evidence must use SEC HTTPS")
        ids = [_cik(str(c)) for c in group.get("owner_ciks", [])]
        if len(ids) < 2:
            raise ValueError("Owner group needs at least two CIKs")
        join(ids)
    return {key: root(key) for key in parent}


def rank_candidates(scan: dict, filings: list[dict], *,
                    related_owner_groups=None, top_n=50) -> dict:
    """Rank *all* market-wide review candidates without inventing SEC verification."""
    if scan.get("pagination_complete") is not True:
        raise ValueError("Refuse incomplete market-wide scan")
    if "ALL_MASSIVE_REPORTING_ISSUERS" not in str(scan.get("scope")):
        raise ValueError("Requires a market-wide scan, not the old 30-stock scan")
    signals = scan.get("signals")
    start_day = date.fromisoformat(scan["from_date"])
    end_day = date.fromisoformat(scan["as_of"])
    if start_day > end_day:
        raise ValueError("Invalid scan date interval")
    if not isinstance(signals, list) or top_n < 1:
        raise ValueError("Missing market-wide signals or invalid shortlist size")
    matches = defaultdict(list)
    for filing in filings:
        if not isinstance(filing, dict) or "issuer_cik" not in filing:
            raise ValueError("Invalid parsed SEC filing")
        matches[filing["issuer_cik"]].append(filing)
    related = _related_group_map(filings, related_owner_groups or [])

    all_rows = []
    for item in signals:
        if not isinstance(item, dict) or "issuer_cik" not in item:
            raise ValueError("Malformed market-wide candidate")
        if not item.get("review"):
            continue
        cik = _cik(str(item["issuer_cik"]))
        f = matches.get(cik, [])
        # Amendments must be reviewed separately to avoid re-counting original data.
        original = [x for x in f if x["form_type"] == "4"]
        seen_events = set()
        verified = 0.0
        direct = 0.0
        verified_event_count = 0
        qualifying_owner_ids = set()
        sec_links = []
        for doc in original:
            sec_links.append(doc["source_url"])
            txns = [x for x in doc["non_derivative_transactions"]
                    if x["open_market_common_purchase"] and x["date"]
                    and start_day <= date.fromisoformat(x["date"]) <= end_day]
            for txn in txns:
                # Identical reported trade attributes across filings may be the
                # same jointly held shares. Count once, but disclose ambiguity.
                key = (txn["date"], txn["security_title"].lower(),
                       txn["shares"], txn["price"], txn["ownership"])
                if key in seen_events:
                    continue
                seen_events.add(key)
                verified += txn["purchase_usd"]
                verified_event_count += 1
                if txn["ownership"] == "D":
                    direct += txn["purchase_usd"]
                for owner in doc["reporting_owners"]:
                    qualifying_owner_ids.add(owner["owner_cik"])
        for doc in f:
            if doc["form_type"] == "4/A":
                sec_links.append(doc["source_url"])
        owner_groups = {related.get(c, c) for c in qualifying_owner_ids}
        observed_owners = {}
        filing_audit = []
        for doc in f:
            for owner in doc["reporting_owners"]:
                observed_owners[owner["owner_cik"]] = owner
            filing_audit.append({
                "accession_number": doc["accession_number"],
                "source_url": doc["source_url"],
                "form_type": doc["form_type"],
                "reported_owner_ciks": sorted(
                    owner["owner_cik"] for owner in doc["reporting_owners"]),
                "trading_plan_checkbox": doc["trading_plan_checkbox"],
                "qualifying_purchase_usd_in_document":
                    doc["qualifying_purchase_usd"],
                "transactions": doc["non_derivative_transactions"],
            })
        related_groups = defaultdict(list)
        for owner_cik in qualifying_owner_ids:
            related_groups[related.get(owner_cik, owner_cik)].append(owner_cik)
        status = ("SEC_FORM4_PURCHASE_RESEARCH"
                  if verified_event_count else
                  "SEC_FILES_REVIEWED_NO_QUALIFYING_P"
                  if f else "UNVERIFIED_MASSIVE_AGGREGATE")
        all_rows.append({
            "issuer_cik": cik,
            "historical_tickers": item.get("historical_tickers", []),
            "issuer_name": item.get("issuer_name", ""),
            "massive_unplanned_purchase_usd": item.get(
                "purchase_not_marked_planned_usd", 0),
            "massive_sale_usd": item.get("sale_usd", 0),
            "massive_same_day_multi_buyer_hint": item.get(
                "same_day_multiple_purchasers", False),
            "sec_verification_status": status,
            "sec_form4_documents_reviewed": len(f),
            "sec_confirmed_qualifying_purchase_usd": round(verified, 2),
            "sec_confirmed_direct_purchase_usd": round(direct, 2),
            "sec_qualifying_transaction_count": verified_event_count,
            "distinct_reporting_owner_ciks": len(qualifying_owner_ids),
            "reporting_owners_from_sec": [
                observed_owners[owner_id]
                for owner_id in sorted(observed_owners)],
            "conservative_related_owner_group_count": len(owner_groups),
            "related_owner_group_members": [
                sorted(members) for _, members in sorted(related_groups.items())],
            "form4_audit": filing_audit,
            "sec_xml_sources": sorted(set(sec_links)),
            "listed_common_stock_eligibility_verified": False,
            "independent_economic_buyers_verified": False,
            "investment_signal_validated": False,
            "note": ("Joint filer/related group counts are conservative research "
                     "proxies, not independently verified investment decisions."),
        })
    all_rows.sort(key=lambda x: (
        -int(x["sec_verification_status"] == "SEC_FORM4_PURCHASE_RESEARCH"),
        -x["conservative_related_owner_group_count"],
        -x["sec_confirmed_direct_purchase_usd"],
        -x["sec_confirmed_qualifying_purchase_usd"],
        -x["massive_unplanned_purchase_usd"],
        x["issuer_cik"],
    ))
    return {
        "project": "Massive Insider",
        "version": "0.3.0",
        "mode": "READ_ONLY_RESEARCH_NO_ORDERS",
        "scanned_candidate_count": len(all_rows),
        "sec_filing_count_supplied": len(filings),
        "sec_verified_candidate_count": sum(
            x["sec_verification_status"] == "SEC_FORM4_PURCHASE_RESEARCH"
            for x in all_rows),
        "top_n": top_n,
        "shortlist": all_rows[:top_n],
        "all_candidates": all_rows,
        "warning": ("No current listing, independent beneficial ownership, or trading "
                    "edge has been proven. Only specific supplied SEC Form 4 XMLs "
                    "are verified. No automatic discovery of all SEC filings."),
    }
