# Massive Insider v0.4 — isolated Alpaca PAPER connectivity pilot

This is a **single manually approved paper-only order test**, not a live broker or automated profitable trading strategy. It sends **no orders** until you explicitly run the `paper-submit` command, and it cannot address Alpaca's LIVE host. The pilot uses your existing v0.3 SEC sample to select a research symbol (e.g., GME), but a 2026 historical insider purchase is **not** a current buy signal.

## On your Mac

1. Update and test your existing repository:

```bash
cd ~/massive-insider
git pull
python3 -m unittest discover -s tests -v
```

2. Create or identify an **unused Alpaca PAPER account** in the [Alpaca paper dashboard](https://app.alpaca.markets/). Ideally set its simulated balance to $1,000. **Do not reset or reuse an account with A15, STONKS, or other bot orders/positions.** The pilot refuses an account with any existing positions or open orders. Alpaca provides different PAPER and LIVE API keys.

3. Copy the **PAPER** key ID and secret from that dashboard. Enter them privately in your current Terminal using prompts (input remains hidden):

```bash
export MI_ALPACA_PAPER_KEY_ID="$(python3 -c 'import getpass; print(getpass.getpass("Dedicated Alpaca PAPER key ID: "))')"
export MI_ALPACA_PAPER_SECRET_KEY="$(python3 -c 'import getpass; print(getpass.getpass("Dedicated Alpaca PAPER secret: "))')"
```

**Never put keys in GitHub, issues, a screenshot or chat.** The code ignores generic Alpaca key variables and uses only `MI_ALPACA_PAPER_*`. The network host is fixed at `https://paper-api.alpaca.markets`, with no configurable base URL, no LIVE endpoint and no trading outside this single order path.

4. Verify **read-only** access to the exact PAPER account:

```bash
python3 -m insider paper-status
```

Confirm its account ID, status and simulated cash. Copy the **account ID**, not the key or secret. No orders are submitted by this command.

5. Make a **read-only** $25 PAPER preview for GME, a stock with one of the SEC-verified sample purchase reports:

```bash
python3 -m insider paper-preview \
  --input reports/v03_sec_sample.json \
  --symbol GME \
  --notional 25 \
  --output reports/gme_paper_preview.json
```

The preview inspects the PAPER account, positions, open orders, market clock, and asset status. It refuses shared/occupied accounts and nontradable/nonfractionable/OTC securities. The preview has a **5-minute validity** and will display the PAPER account ID. It can be generated when markets are closed, but then will need to be regenerated shortly before attempting a submission during normal market hours.

6. **Optional: explicitly authorize one simulated buy.** Run only if you actually want that $25 PAPER order, the market is open, and the account ID matches your intended isolated PAPER account. Replace `YOUR-PAPER-ACCOUNT-ID` with the exact ID from step 4 or 5.

```bash
python3 -m insider paper-submit \
  --plan reports/gme_paper_preview.json \
  --input reports/v03_sec_sample.json \
  --expected-account YOUR-PAPER-ACCOUNT-ID \
  --confirm I-APPROVE-PAPER-ONLY \
  --output reports/gme_paper_receipt.json
```

**This command submits a real order to the simulated Alpaca PAPER account only.** It places ONE **BUY**, a `day` time-in-force fractional-share market-notional order with extended-hours disabled. It will not run unless the PAPER market clock says open. A submission acknowledgment is **not** a fill. Check the Alpaca PAPER dashboard and the receipt file for actual status.

## Hard protections

- Paper endpoint is hardcoded; only specific PAPER GET requests and one `POST /v2/orders` are permitted.
- A verified v0.3 SEC research record is required at preview and rechecked at submission. Only plain exchange-listed fractionable U.S. equity symbols allowed; no warrants, crypto, OTC, shorts, sells or margin borrowing.
- **One-order pilot:** the account must have **zero positions and zero open orders**. It is intentionally not a portfolio bot yet.
- Per-order notional is capped at **$200**, **20% of account equity**, and **available cash minus a $20 reserve** (whichever is most restrictive), corresponding to a $1,000 virtual strategy allocation. If your PAPER account defaults to $100,000, the pilot is **still capped at $200**.
- The preview expires in five minutes. The submission checks the intended account ID, account health, cash, market hours, positions, pending orders, instrument eligibility, and previously submitted client-order IDs.
- Uses a client_order_id to prevent duplicate submissions. It does **not** automatically retry an ambiguous HTTP POST acknowledgement; check PAPER broker order status first.
- `data/` and `reports/` remain Git-ignored. Secrets are read from the environment; no browser logins or file credentials stored.
- **NOT IMPLEMENTED:** historical alpha backtesting, automated daily trading, multiple positions or portfolio rebalancing, price limits/slippage protection, automatic exits, order monitoring or LIVE trading.

See [Alpaca official create-order docs](https://docs.alpaca.markets/us/reference/postorder) and [PAPER trading documentation](https://docs.alpaca.markets/docs/paper-trading).

The purpose of this pilot is **testing broker connectivity and safeguards with fictional money**. An old Form 4 signal is not a current alpha signal, and PAPER returns are not reliable forecasts of live profitability.
