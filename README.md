# Massive Insider — PAPER-only equity research

A separate insider-activity research project for 30 high-growth stocks. **Not A15, not the former Beethoven momentum bot, and not a live trading system.**

**Status: research scanner v0.1.** This repository reads Massive's aggregated **Insider Trading Activity** API, summarizes purchase and sale activity, and writes an offline JSON shortlist. It does **not** have a brokerage connection, backtested investment returns, PAPER execution, automated orders, or a proven trading edge.

## Safety and scope
- Stocks-only 30-symbol watchlist; SPY, ETFs, options, crypto and leverage are not eligible.
- No Alpaca endpoints, order methods, schedule, or automated brokerage actions.
- No stored API keys; never commit secrets. **Rotate the previously exposed Massive key.**
- No X/JoeAlerts, Webull, A15, Coinbase or VPS integration.
- The earlier **Beethoven Massive Growth PAPER v0.1** is a *different momentum research prototype*, not this insider signal strategy. It has intentionally not been mislabeled as the insider engine.

## Run
Requires Python 3.11+; standard library only.

```bash
python3 -m unittest discover -s tests -v

# Offline: analyze a Massive JSON response you have already saved
python3 -m insider scan --input sample_response.json --as-of 2026-11-10 --output reports/signals.json

# Online: fetch complete paginated activity without embedding the key in URLs
export MASSIVE_API_KEY='REPLACE_WITH_ROTATED_KEY'
python3 -m insider scan --from-date 2026-08-01 --as-of 2026-11-10 --output reports/signals.json
```

The online scanner uses a fixed `https://api.massive.com/stocks/insiders/vX/activity` endpoint, token in an Authorization Bearer header, request pacing, pagination and redirect protections. Never paste your API key in GitHub issues or browser URLs.

## Interpretation
- The endpoint aggregates by **issuer and transaction date**; it is **not** a named-insider transaction history.
- `purchase_value - planned_purchase_value` is **purchasing not marked as planned**. That does not prove an informed discretionary purchase.
- `purchaser_count` is distinct *within one issuer/day*. Adding distinct-buyer counts across different dates is invalid; the same person could buy repeatedly. Same-day purchases by multiple insiders are marked **same_day_cluster_candidate**, pending verification in individual SEC filings.
- `last_filing_date` is the **latest** filing reporting a transaction in that aggregate. As-of screening uses this conservative date to avoid look-ahead.
- The `review` result is a **research flag**, not a BUY signal. A purchase threshold of $250,000 is a starting hypothesis, not an optimized or proven threshold.
- The API beta does not itself establish a clean point-in-time historical dataset. Real alpha testing requires underlying SEC Form 4 timing, survivorship-aware history, adjusted market prices, transaction costs, and out-of-sample comparisons.

## Next work before PAPER trading
1. Validate daily aggregate signals against original SEC Form 4 filings and check individual buyers, transaction code, 10b5-1 context and ownership changes.
2. Construct point-in-time historical features; run out-of-sample backtests against a no-signal benchmark including realistic costs.
3. **Only after explicit authorization**, create a *separate* Alpaca PAPER account adapter and prove that it cannot use LIVE credentials or endpoints.

**This repository is capture/analysis only. No real or simulated orders are placed.**

Reference: [Massive Insider Trading Activity docs](https://massive.com/docs/rest/stocks/filings/insider-activity).
