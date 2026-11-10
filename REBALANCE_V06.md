# Massive Insider v0.6 — aggressive conviction-weighted Alpaca PAPER portfolio

**Status:** The repository's existing, already-enabled GitHub Actions trading workflow is switched to `rebalance-cycle --execute`. It remains **PAPER only** and uses the **same** four existing Actions secrets and two Actions variables (no re-entry needed). The next eligible workflow execution will run v0.6 if GitHub scheduling, the signal feed and broker account checks succeed.

## Target allocation

- **100% of eligible *strategy* capital** is allocated proportionally across positive insider signals, with no per-stock concentration cap. One eligible stock can receive 100% of the strategy allocation; two scores of 80:20 yield 80%/20% targets.
- A separate **0.5% of total PAPER account equity cash buffer** is retained to avoid using margin. These are targets, not guaranteed fill percentages.
- **Existing manually entered holdings, including your original $25 GME PAPER pilot, are protected** (no auto sells/averaging). Their value is excluded from strategy capital. As a result, the bot cannot place literally 100% of the *entire account* into a new signal unless the pilot/manual stock is closed separately.
- **Previously v0.5-auto-managed holdings (e.g., UPBD) are eligible** for the new weighted strategy only when the broker's filled order-history quantities reconcile exactly to current position size. If ownership is uncertain, the holding is protected instead of traded.
- At most 10 total stocks, including protected legacy holdings. If signals disappear or sell pressure overwhelms buys, the bot can sell its own fully attributable positions and hold cash. A signal may become ineligible if the filing ages past seven days.

## Four conviction factors — UNVALIDATED starting hypotheses

| Factor | Weight | Implementation |
|---|---:|---|
| Increase in insider personal shares | 40% | Disclosed purchased shares divided by pre-purchase holdings (derived from reported post-trade holdings); capped at a 50% increase |
| Multiple insider buying signals | 30% | Distinct reporting-owner CIKs, saturated at three; not proof of economic independence |
| Purchased shares as percentage of outstanding shares | 20% | Insider purchase shares / currently reported share-class shares outstanding; capped at 1% |
| Recency and direct discretionary sales pressure | 10% | Favors recently *filed* disclosures, reduces positive conviction for direct officer/director sales; large direct sales can eliminate a positive signal |

The **40/30/20/10 weights, 50% and 1% normalization scales, and sales multiplier have NOT been historically optimized**. Insider sales may reflect diversification rather than poor prospects. We use only Form 4 transactions marked as direct, non-derivative, disclosed non-10b5-1 director/officer purchases and separately screened direct sales. We do **not** infer insider intent from dollar totals.

## Filing and reference data

- Complete, paginated individual purchases (`P`) over 14 calendar days and sales (`S`) over 7 days from Massive, with a public filing date of at least one day ago and no more than seven days ago to avoid accidental intraday look-ahead.
- Uses `rank_current_purchases()` to identify preliminary open-market purchases totaling at least $25,000 per issuer.
- For API rate limits, scores **only the top 20 preliminary candidates** ranked by preliminary unique reporting-owner count and dollars; this is **not exhaustive ranking of all qualifying companies**. A separate Massive ticker-identity lookup confirms active US common-stock type, symbol and matching SEC issuer CIK and provides share-class shares outstanding. The lookup pauses approximately 13.1 seconds between tickers to respect a conservative five-request-per-minute plan.
- Sales feed must complete successfully; missing or erroneous data is not silently converted to zero sales. If filings/reference/broker API or history is incomplete, the cycle fails closed.
- SEC filing acceptance timestamps, footnotes, related economic owners, adjusted historical research, actual return predictiveness and corporate-action adjustments need further diligence. Massive's structured Form 4 data is not independent document-by-document SEC XML verification.

## Broker execution and constraints

- Broker URL hardcoded to `https://paper-api.alpaca.markets`. Only the dedicated `MI_ALPACA_PAPER_* ` key pair is read; no LIVE endpoint/credentials.
- Broker account must exactly match `MI_PAPER_ACCOUNT_ID` in your Actions variables, be ACTIVE, and have a complete current account/positions/open orders/recent 500 orders/market clock snapshot.
- One PAPER market order **per 15-minute cycle**: sells overweight manager positions before buying; buys new/underweight companies only with cash (never intentionally borrows). Wait for open orders to resolve. Fractional quantities are limited to nine decimal places.
- A 2.5%-of-equity (or $15) deviation band reduces ordinary turnover; full exits of attributable positions may sell as little as $1. Maximum 10 manager orders daily, including earlier v0.5 orders; daily deterministic per-symbol/side IDs prevent same-day duplicate orders.
- Trades only regular weekdays with broker clock open between 9:45 a.m. and 3:40 p.m. New York time. GitHub schedule checks about every 15 minutes between 9:52 a.m. and 3:37 p.m. New York time. No orders during closed hours. GitHub schedules are best-effort and may be delayed/skipped.
- The old `validate_snapshot()` guard currently rejects a PAPER account whose equity exceeds **$1,500**, even if earned through profitable simulated trades; this is a conservative small-account safeguard, not a signal/position cap. We should revisit it once the pilot results are reviewed.
- The PAPER account is assumed dedicated to Massive Insider. Don't trade manually within it or transfer PAPER capital until the bot's order/position attribution is reconciled.
- Existing broker orders must not be manually canceled/replaced mid-rebalance without reviewing the next state. Partial, rejected and ambiguous submissions require broker inspection. There are no automatic POST retries.
- Each GitHub Actions runner is ephemeral; local `reports/rebalance_history.jsonl` is a per-run audit trail **not persisted across the scheduled runs**. Alpaca order history/positions remains the authoritative cross-run state. A durable historical performance dashboard and cash-flow-aware returns measurement are still outstanding.

## How to inspect without orders (local Mac)

After `git pull`, install/restore your **new** dedicated PAPER credentials locally if needed; GitHub already has the new pair in encrypted Secrets. Do not paste them into chat or files. Then:

```bash
cd ~/massive-insider
python3 -m unittest discover -s tests -q
python3 -m insider rebalance-cycle
```

Read-only produces `reports/rebalance_latest.json`. To run *one* PAPER order cycle on your Mac requires `MI_PAPER_AUTO_ENABLED=I-ENABLE-PAPER-AUTO` in that same Terminal session plus `python3 -m insider rebalance-cycle --execute`. Do **not** run it while the GitHub workflow is executing; that would introduce a concurrent actor against the same account.

## GitHub Actions

The scheduled trading workflow is [`.github/workflows/paper-portfolio.yml`](.github/workflows/paper-portfolio.yml). View [Actions runs](https://github.com/williamRut261/massive-insider/actions). Once GitHub first runs v0.6, verify the workflow job conclusion and the broker PAPER dashboard. A successful GitHub job is **not** proof the broker order filled. Disable immediately by setting the Actions variable `MI_PAPER_AUTOMATION_ENABLED=NO`; this does not cancel existing PAPER orders.

This is an experimental PAPER strategy. No claim of higher or positive CAGR, and not a tested replacement for a backtest or historical replay.
