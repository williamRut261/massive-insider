# Massive Insider v0.6 — aggressive conviction-weighted Alpaca PAPER portfolio

**Current active workflow:** v0.6 [four-factor insider conviction rebalancing](REBALANCE_V06.md). It invests up to 99.5% of PAPER equity *after subtracting protected legacy holdings*, with no concentration limit, sell-first rebalancing, 10-stock limit, and broker-history reconciliation. Uses the existing enabled GitHub Actions schedule and dedicated PAPER keys. **The original GME manual pilot is never sold by the bot.** Four-factor weights are experimental and have not been backtested.

**v0.5 documentation below is historical**; `portfolio-cycle` remains available for diagnostics but the scheduled workflow now runs `rebalance-cycle --execute`.

---

**New:** [AUTOMATION.md](AUTOMATION.md) describes the opt-in, default-disabled weekday PAPER portfolio allocator with fresh Massive Form 4 purchases, SEC issuer identity validation, exposure and drawdown controls, broker order history, restart protection, and conservative exits only for fully attributable manager-owned lots.

**Your previously filled $25 GME PAPER pilot stays untouched.** The strategy automatically skips any existing position, and it does not sell pilot/manual holdings. There is no LIVE account connection. **Automated orders are not active merely because code was committed**: operator opt-in and separately configured PAPER credentials are required.

---
## Prior v0.4 — isolated single-order PAPER pilot

**New: [Alpaca PAPER-only single-order setup instructions](PAPER_PILOT.md).** This is an explicitly approved, isolated broker-connectivity experiment; no LIVE endpoint or keys can be used and no orders are submitted by default. Automated portfolio trading and a validated historical strategy are not yet implemented.

---

## v0.3 — SEC ownership verification and research ranking

**State: research only; no PAPER or LIVE order placement.** v0.3 builds on the tested v0.2 market-wide scanner. It ranks *all* candidates from your existing 90-day report, recognizes actual reporting-owner names/CIKs and Form 4 transaction codes from **explicit SEC XML documents**, groups joint filers and separately documented related entities conservatively, and maintains an audit trail. This is not a validated returns model.

## 1. Update and run offline tests

```bash
cd ~/massive-insider
git pull
python3 -m unittest discover -s tests -v
```

## 2. Rank all 306 candidates offline — no network or secrets required

```bash
python3 -m insider verify \
  --input reports/marketwide_2026-11-10.json \
  --top 30 \
  --output reports/v03_shortlist_all.json
```

This uses the **saved market-wide results on your Mac**. It evaluates all 306 previously flagged candidates and lists the first 30 in `shortlist` while retaining the entire ranked candidate set in `all_candidates`. With no SEC manifest, every result is explicitly **UNVERIFIED_MASSIVE_AGGREGATE**. The order is a *research triage order*, not a prediction of investment returns.

## 3. Verify example SEC reporting owners and transaction codes

SEC asks automated callers to declare a user agent with contact information. In your private Terminal session, set:

```bash
export SEC_USER_AGENT="MassiveInsiderResearch your-own-contact-email@example.com"
```

Replace the example address with an email address you control. Do **not** put the address in a public repo or paste it into chat.

```bash
python3 -m insider verify \
  --input reports/marketwide_2026-11-10.json \
  --manifest examples/sec_form4_sources_2026.json \
  --top 30 \
  --output reports/v03_sec_sample.json
```

The example manifest contains **three real SEC Form 4 XML URLs**, one for GME and two for GPI. It is NOT a complete filing inventory for either company or for all 306 candidates. SEC filings may be refused (HTTP 403/429) if your client/user-agent is not accepted; no SEC XML connectivity has been exercised by our offline CI tests. The program refuses redirects, non-SEC URLs, non-XML documents, malformed transaction values, duplicate accessions, and incomplete market-wide input.

To inspect actual reporting owners in the sample report:

```bash
python3 - <<'PY'
import json
d = json.load(open("reports/v03_sec_sample.json"))
print("Candidates reviewed:", d["scanned_candidate_count"])
print("SEC XML filings supplied:", d["sec_filing_count_supplied"])
print("Candidates with SEC-confirmed purchase transactions:", d["sec_verified_candidate_count"])
for c in d["shortlist"]:
    if c["sec_verification_status"] == "SEC_FORM4_PURCHASE_RESEARCH":
        print(c["historical_tickers"], c["sec_confirmed_qualifying_purchase_usd"])
        for owner in c["reporting_owners_from_sec"]:
            print(" ", owner["owner_name"], owner["owner_cik"],
                  "officer:", owner["officer"], "director:", owner["director"],
                  "10% owner:", owner["ten_percent_owner"])
        print("Conservative related-owner groups:", c["related_owner_group_members"])
PY
```

## SEC source and group verification limits

- Only specific official SEC Form 4/4-A XML documents listed in a manifest are fetched and parsed. The system **does not automatically discover or verify every Form 4 across all 306 issuers**. A reviewed issuer may still have other relevant filings.
- A **P + A** (purchase/acquisition) in a non-derivative **common stock** transaction with positive price and shares is a qualifying research transaction. Awards, option exercises, sales, zero-priced transfers, derivatives, and ambiguous securities are excluded from the SEC purchase subtotal. 4/A amendments are listed for review but **not automatically added** to avoid double counting.
- Owner names, owner CIKs, officer/director/10%-holder flags, direct/indirect ownership, filing source URL, transaction date and price, and Rule 10b5-1 checkbox are preserved from the SEC XML. A checkbox or missing checkbox is **not** proof of discretionary intent.
- Identical reported trade details across different filings are counted only once, to reduce duplicated beneficial ownership reports; this may conservatively undercount identical independent trades. A joint filing's multiple reporting CIKs are counted as **one possible owner group**. Cross-filing relationships are merged **only** if a `related_owner_groups` entry supplies explicit SEC evidence.
- Different reporting CIKs or an issuer/day `purchaser_count` do **not** independently prove independent capital decisions. This is not a true ownership-control graph. For any group with significant money, follow footnotes, corporate-control chains and amendments manually.
- **SEC filing acceptance timestamps are not independently retrieved by v0.3.** Historical transaction dates are bounded by the requested scan, but full point-in-time backtests must also use the EDGAR **accepted timestamp** (next tradable session) and account for 1–3-minute dissemination delays.
- A Form 4 `P` can involve indirect ownership, specialist funds, and 10% holders rather than executives spending personal cash. All results remain `listed_common_stock_eligibility_verified: false`, `independent_economic_buyers_verified: false`, and `investment_signal_validated: false`.
- No broker integration, no order code, no production scheduling, no risk of accessing A15, Webull, STONKS, or Coinbase. Input and output are local; `data/` and `reports/` remain ignored by Git.

**Full 306-company SEC verification and the insider-following backtest are further development work.** Do not interpret the presence of 306 Massive research flags as 306 proven open-market trades or a profitable strategy.

Primary documentation: [SEC Ownership XML technical specifications](https://www.sec.gov/submit-filings/technical-specifications) · [SEC fair-access requirements](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data) · [Official SEC filing search](https://www.sec.gov/search-filings)

---

## v0.2: Market-wide SEC issuer activity research

**Separate, read-only insider-following research. Not a brokerage bot.** The original 30-stock scanner is preserved; v0.2 adds an opt-in **market-wide issuer activity scan** using Massive's [Insider Trading Activity API](https://massive.com/docs/rest/stocks/filings/insider-activity).

**IMPORTANT SCOPE:** No `tickers` parameter means all issuers returned by this Massive endpoint for the date window. **It does NOT mean the results are a vetted universe of active, U.S.-listed, tradable common stocks.** It may include historical tickers, OTC/non-U.S./other classes, entities with no ticker, or old symbols. The scanner deliberately marks `verified_current_us_listed_common_stock: false` for every issuer. This is an upstream research universe, not a trade eligibility file.

## Safety
- **No Alpaca, no broker connection, no PAPER or LIVE orders, no schedule.**
- Does not change STONKS, A15, Webull, Coinbase, other bots or accounts.
- No API secrets committed. **Rotate any previously exposed Massive API key.**
- Requests only to a fixed Massive API host/path over HTTPS, with credentials in a Bearer header, not URLs; redirects and unsafe pagination URLs are refused.
- API pagination must finish before an online run generates output. If pages are missing, it fails rather than silently claiming a complete market scan.
- Standard-library Python; tested with Python 3.11 in GitHub Actions.

## On your Mac

```bash
cd ~/massive-insider
git pull
python3 -m unittest discover -s tests -v
```

Set your **rotated** key privately in the current Terminal session (nothing typed is echoed):

```bash
export MASSIVE_API_KEY=$(python3 -c 'import getpass; print(getpass.getpass("Massive API key: "))')
```

### Market-wide scan (Aug 12–Nov 10, 2026)

```bash
python3 -m insider scan \
  --market-wide \
  --from-date 2026-08-12 \
  --as-of 2026-11-10 \
  --window-days 90 \
  --raw-output data/marketwide_2026-11-10.json \
  --output reports/marketwide_2026-11-10.json
```

It requests all issuer activity in the 90-day window (no ticker filter), 5,000 records per page, and conservatively pauses **13.1 seconds between requests** for a five-calls-per-minute plan. It validates all pages before output. For very large histories, the explicit 100-page/500,000-record cap can reject the scan; **split date windows** rather than accepting incomplete results. Market-wide volumes and your account's available coverage have not been measured.

Output:
- `data/marketwide_2026-11-10.json`: preserved raw issuer/day records and capture metadata for later independent verification.
- `reports/marketwide_2026-11-10.json`: aggregate research flags. Includes `issuer_count`, `record_count`, `research_candidate_count`, `pagination_complete`, and ranked `signals`.

Data and reports are `.gitignore`d. Do not upload the raw responses or your API key to a public repository.

### Inspect top candidates

```bash
python3 - <<'PY'
import json
d = json.load(open("reports/marketwide_2026-11-10.json"))
print("Records:", d["record_count"], "Issuers:", d["issuer_count"])
print("Review candidates:", d["research_candidate_count"], "Complete:", d["pagination_complete"])
for x in d["signals"]:
    if x["review"]:
        print(x["historical_tickers"], x["issuer_name"],
              "not-marked-planned buys:", x["purchase_not_marked_planned_usd"],
              "same-day multiple buyers:", x["same_day_multiple_purchasers"])
PY
```

### Keep the old 30-stock scan

```bash
python3 -m insider scan --from-date 2026-08-12 --as-of 2026-11-10 --window-days 90 --output reports/signals_90d.json
```

### Offline replay

```bash
python3 -m insider scan --market-wide --input data/marketwide_2026-11-10.json \
  --from-date 2026-08-12 --as-of 2026-11-10 --window-days 90 \
  --output reports/marketwide_replay.json
```

## What a research flag does and does not mean

- Reports aggregate **one issuer/day**, not the identity of each trader; `purchaser_count` refers to distinct insiders for that **day only**. Same-day multiple purchasers require checking original Form 4/5 filings.
- `purchase_not_marked_planned_usd = purchase_value - planned_purchase_value` is **not proof of a discretionary purchase**. Plan designations and ownership must be verified from SEC filings; zero plan values on pre-2023 filings can be misleading.
- `last_filing_date` is conservatively used for as-of filtering; it is not the precise filing acceptance timestamp needed for a reliable backtest. Historical availability and point-in-time dataset completeness are **not proven**.
- `review` flags an issuer with >= $250,000 of purchases not marked planned during the requested period. It is a **research triage threshold**, not a validated signal, recommendation or approved order.
- A large negative net dollar total from planned insider sales **does not** by itself mean a stock is bearish.
- **No backtested alpha or return forecast** is claimed. Actual backtesting requires time-stamped SEC filing validation, delisting/survivorship coverage, next-session prices, costs, and out-of-sample controls before any PAPER execution.

## Development checkpoints
1. Offline market-wide fetch, pagination, raw retention, and conservative issuer signal rules (v0.2).
2. Verify current listed-common-stock eligibility and original SEC Form 4 transaction codes/owner identities.
3. Establish historical event timestamp availability and backtest vs matched random events and SPY, including conservative next-session fills, transaction costs, and survivorship controls.
4. Only with explicit future authorization, investigate **separate Alpaca PAPER-only** order connectivity. No live keys or endpoints are permitted.
