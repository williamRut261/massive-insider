# Massive Insider — archived v0.5 PAPER portfolio guide

**The scheduled trading workflow now runs v0.6, not the fixed-$100 v0.5 manager.** Use [REBALANCE_V06.md](REBALANCE_V06.md) for current operating rules. The legacy instructions below describe the still-available `portfolio-cycle` command and should not be mistaken for the live scheduled algorithm.

---

# Massive Insider v0.5 — automatic, tightly limited PAPER portfolio manager

**Implemented in GitHub, but AUTOMATED TRADING IS OFF until you enable it.** The first filled $25 GME PAPER pilot remains a manual/legacy position and will **not** be bought again or liquidated by v0.5. v0.5 uses current broker state on every cycle and never calls an Alpaca LIVE endpoint. All trading is fictional Alpaca PAPER money.

## What v0.5 does

1. Gets newly filed **individual Form 4** records from the [Massive Form 4 endpoint](https://massive.com/docs/rest/stocks/filings/insider-transactions), with up to 14 calendar days of filing history and complete pagination.
2. Only considers a strict, conservative research subset: non-derivative direct purchases (`P`, acquired `A`) of common shares by directors/officers; excludes disclosed Rule 10b5-1 plan flags, indirect beneficial ownership, derivative awards, grants and options; uses a **minimum $25,000 of purchases per issuer**. The public filing date must be **at least one day old and no more than seven days old**. Those are research assumptions, not backtested alpha.
3. Cross-checks a current US-listed **CS** common-stock ticker and matching issuer **CIK** with Massive's [Ticker Overview endpoint](https://massive.com/docs/rest/stocks/tickers/ticker-overview), then checks current tradability/fractionability directly with Alpaca PAPER. Ambiguous share classes or ticker-to-CIK mismatches are skipped.
4. Reads **PAPER account, all current positions, current open orders, last 500 orders, market clock and individual asset eligibility**. Recognizes your existing GME position as held. No buy if a candidate is already held.
5. During regular market hours, makes **at most one PAPER order per cycle**, **two automation orders per trading day**, **$100 per new entry**, **10 held stocks**, **$1,000 invested exposure**, **$20 cash reserve**, **20% per-stock order size/$200 max**. Automatically freezes new buys after the PAPER account loses 20% from the original $1,000 allocation. Prevents purchases while ANY order is open or broker history is truncated.
6. After **30 calendar days**, optionally sells a manager-owned position **only if it can verify the entire current quantity belongs to one fully filled v0.5 buy order and no other trade exists for that symbol in broker history**. If ownership is unclear, it holds; it **never sells the GME pilot position**. Sells use paper-only fractional quantity market orders.
7. Uses deterministic broker client-order IDs, broker history and local write-ahead intent records to reduce duplicate orders and prevent automatic retries after ambiguous network responses. Rechecks broker state before submission. Appends local account-equity snapshots to `reports/portfolio_history.jsonl`.

**This is a conservative automated PAPER experiment, not a proven insider-investing strategy.** Market orders, simulations, filling rules and execution prices can diverge from live trading. Filings are reviewed through Massive structured data; the program does not yet independently validate every Form 4 footnote or SEC acceptance timestamp. Independent economic ownership is not proved from distinct reporting CIKs. Historical backtesting remains undone.

## Step 1: Update your Mac and run all tests

```bash
cd ~/massive-insider
git pull
python3 -m unittest discover -s tests -v
```

Keep your existing locally set credentials:
`MI_ALPACA_PAPER_KEY_ID`, `MI_ALPACA_PAPER_SECRET_KEY`, and `MASSIVE_API_KEY`. If you restarted Terminal, reenter them privately with `getpass` as in [PAPER_PILOT.md](PAPER_PILOT.md). Do **not** paste API credentials in chat, push them to GitHub, or add them to shell history.

Set your dedicated PAPER account ID (a non-secret account identifier, not an API key):

```bash
export MI_PAPER_ACCOUNT_ID="5a5d5e5f-19fc-4aa3-9873-7b8cce1b298f"
```

## Step 2: Run a fully read-only portfolio cycle

```bash
python3 -m insider portfolio-cycle
```

It fetches current Form 4 purchases, shows how many pass the preliminary screen, checks the GME PAPER holding and other account state, and writes `reports/portfolio_latest.json`. **It never places an order without `--execute` plus a separate activation setting.** Empty results mean no new eligible signal, not a malfunction.

Inspect:

```bash
python3 - <<'PY'
import json
d = json.load(open("reports/portfolio_latest.json"))
p = d["outcome"]["plan"]
print("Status:", d["outcome"]["status"])
print("Equity:", p["equity_usd"], "Cash:", p["cash_usd"])
print("Existing holdings:", p["held_positions"])
print("Proposed order:", p["proposed_order"])
print("Why no order:", p.get("block_reason"))
PY
```

## Step 3 (optional): Allow ONE automated PAPER cycle on your Mac

**This submits a simulated buy or an eligible 30-day manager-owned exit.** A PAPER order can be submitted immediately; do not execute unless you want that action.

```bash
export MI_PAPER_AUTO_ENABLED="I-ENABLE-PAPER-AUTO"
python3 -m insider portfolio-cycle --execute
```

You can disable local execution by running `unset MI_PAPER_AUTO_ENABLED`. The action does not configure a recurring schedule on your Mac; run it again manually or use the separately gated GitHub Actions workflow below.

## Step 4 (optional): Enable recurring GitHub Actions PAPER-only scheduling

A default-disabled workflow is committed at [`.github/workflows/paper-portfolio.yml`](.github/workflows/paper-portfolio.yml). It is now scheduled **every 15 minutes on weekdays from 9:52 a.m. to 3:37 p.m. New York time**, using GitHub Actions' `America/New_York` timezone so daylight-saving changes are handled automatically. On every run it checks the *actual Alpaca PAPER market clock and the stricter 9:45 a.m.–3:40 p.m. ET trading window* before any order. A delayed Actions trigger outside that window makes no trade. GitHub schedules are best-effort, not a guarantee of precise timing.

To enable it, open your repository **Settings → Secrets and variables → Actions**, and add:

| Type | Name | Value |
|---|---|---|
| Secret | `MI_MASSIVE_API_KEY` | Your privately rotated Massive API key |
| Secret | `MI_ALPACA_PAPER_KEY_ID` | The dedicated Alpaca PAPER key ID |
| Secret | `MI_ALPACA_PAPER_SECRET_KEY` | The dedicated Alpaca PAPER secret |
| Secret | `MI_PAPER_AUTO_ENABLED` | `I-ENABLE-PAPER-AUTO` |
| Variable | `MI_PAPER_ACCOUNT_ID` | `5a5d5e5f-19fc-4aa3-9873-7b8cce1b298f` |
| Variable | `MI_PAPER_AUTOMATION_ENABLED` | `YES` (enable **last**) |

Do not set those secrets in the repo README, `.env`, or in a public commit. Credentials stay in GitHub encrypted Actions secrets and are exposed only to the opt-in main-branch workflow's running job. **Workflow is disabled by default** because these variables/secrets are not currently known to be configured.

To stop recurring orders at any point, set `MI_PAPER_AUTOMATION_ENABLED` to `NO` in Actions Variables or remove the activation secret; the next workflow run will skip. This does not cancel already submitted PAPER orders or automatically liquidate holdings.

**Hands-off requirements:** Once the four secrets and two variables below are configured, and the repository workflow is enabled, you do not need to keep your Mac on or rerun any Terminal command. You can use the repository's **Actions → Massive Insider - guarded PAPER portfolio → Run workflow** to verify that a run executes and read its logs. It may submit an order if the automation is active and a stock qualifies. Keep the account dedicated to this bot: do not manually trade or add cash without reconciling the strategy's $1,000 virtual-capital assumption.

**GitHub schedule limitations:** GitHub warns that scheduled jobs may be delayed or skipped during high load; this is **periodic unattended scanning, not continuous tick-level execution**. Also, for **public repositories**, scheduled workflows are disabled after **60 days without repository activity**. Review the Actions page periodically or move this job to a persistent, maintained VPS/system service if you need indefinite operation. This workflow does not proactively alert on every skipped run, and repeated failures require investigation.

**Actions runner limitation:** each GitHub Actions run is ephemeral, so `reports/portfolio_history.jsonl` is not persisted across scheduled runs. The broker's positions and order history remain the authoritative state. For a durable returns dashboard, use a persistent runner/storage solution and reconcile deposits/withdrawals. The simple percentage change from the initial $1,000 does not adjust for account transfers.

## Fail-closed behavior

- If the latest Massive Form 4 feed, pagination, ticker reference, Alpaca positions, cash, clock, or order history fails, **no order is submitted**.
- The strategy has no LIVE trading URL, no margin borrowing, no shorts, no options, no crypto, no premarket buys, no unverified OTC/warrants/ADRs.
- If a buy/sell POST acknowledgement is ambiguous, the code **does not retry automatically**. Investigate by client order ID in Alpaca PAPER before allowing another attempt.
- An existing filled GME PILOT position is treated as a legacy holding, not an auto-managed lot; v0.5 neither adds to it nor sells it.

Source documentation: [Alpaca order API](https://docs.alpaca.markets/us/reference/postorder) · [List PAPER orders](https://docs.alpaca.markets/us/reference/getallorders-1) · [Massive Form 4](https://massive.com/docs/rest/stocks/filings/insider-transactions)
