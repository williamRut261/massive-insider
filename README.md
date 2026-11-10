# Massive Insider v0.2 — Market-wide SEC issuer activity research

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
