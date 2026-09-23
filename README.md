# StockPilot AI — V8 Mobile

A mobile-first NSE pre-breakout/breakout scanner built with **Streamlit + GitHub + Upstox API V3**.

This version intentionally keeps the deployment to **three files**:

```text
stockpilot-ai/
├── app.py
├── requirements.txt
└── README.md
```

## What this version does

The scanner is designed around the StockPilot AI plan shown in the project:

- NSE equity universe from Upstox instrument master
- Live Upstox V3 full-market quotes
- Completed daily candles for the technical signal
- 500-day history for long moving averages
- Pre-breakout detection
- VCP / tight compression
- NR7
- Inside bar
- 5D / 7D range contraction
- ATR contraction
- Volume contraction
- 20-day resistance pressure
- 52-week high proximity
- Confirmed breakout = completed daily close above prior structure + volume >= 1.5x 20D average
- Transparent 0–100 setup score
- Live price, breakout level, stop, T1 and T2
- Stock detail checklist
- SMC-style price chart
- Risk calculator
- Mobile-friendly cards, buttons and navigation
- No automatic order placement

## Important architecture

The app does **not** download 500-day history for every NSE stock.

The scan is two-stage:

1. **Live pre-filter**
   - Requests Upstox full quotes in batches of up to 500 instruments.
   - Removes very weak candidates.
   - Prioritises stocks closest to their 52-week high and with live volume.

2. **Technical validation**
   - Downloads daily history only for the selected candidate set.
   - Calculates the strategy indicators.
   - Ranks the results.

This keeps the app much more practical on a mobile browser and Streamlit Cloud.

## Data freshness

During market hours:

- `Live` price comes from the Upstox V3 quote endpoint.
- Technical confirmation is based on the **last completed daily candle**.
- Today's unfinished daily candle is deliberately not used for a confirmed close-breakout signal.

This prevents an intraday candle from being incorrectly treated as a completed daily breakout.

Upstox V3 supports full market quotes for up to 500 instruments per request and provides `last_price`, `prev_close_price`, `year_high`, `year_low`, volume and OHLC fields. Its V3 historical candle API supports daily data and long historical ranges. See the official documentation:
- https://upstox.com/developer/api-documentation/get-full-market-quote-v3/
- https://upstox.com/developer/api-documentation/v3/get-historical-candle-data/
- https://upstox.com/developer/api-documentation/get-market-quote-ohlc-v3/

## 1. Create the GitHub repository

Create a repository such as:

```text
stockpilot-ai
```

Upload:

```text
app.py
requirements.txt
README.md
```

Do **not** upload your Upstox access token.

Recommended `.gitignore` entry if you later add one:

```text
.streamlit/secrets.toml
__pycache__/
.venv/
```

## 2. Run locally

Use Python 3.11 or another Python version supported by the Streamlit deployment environment.

```bash
python -m venv .venv
```

Windows:

```powershell
.\.venv\Scripts\Activate.ps1
```

Install:

```bash
pip install -r requirements.txt
```

Create:

```text
.streamlit/secrets.toml
```

Add:

```toml
UPSTOX_ACCESS_TOKEN = "YOUR_UPSTOX_ACCESS_TOKEN"
```

Run:

```bash
streamlit run app.py
```

## 3. Deploy from GitHub to Streamlit Community Cloud

1. Push the three files to GitHub.
2. Open Streamlit Community Cloud.
3. Create a new app.
4. Select your GitHub repository.
5. Select `app.py` as the main file.
6. Deploy.
7. Open the app's **Settings → Secrets**.
8. Add:

```toml
UPSTOX_ACCESS_TOKEN = "YOUR_UPSTOX_ACCESS_TOKEN"
```

9. Save/reboot the app.

Do not put the token in `app.py`, GitHub, README, screenshots or chat messages.

Streamlit recommends using its Secrets management rather than committing credentials to a repository.

## 4. Mobile usage

Open the deployed Streamlit URL on your phone.

The UI is designed for mobile:

- collapsed sidebar
- large touch buttons
- compact KPI cards
- mobile setup cards
- segmented navigation
- horizontal scanner table
- stock detail screen
- risk calculator

You do not need a separate Android application.

## 5. Daily workflow

### Before market / morning

1. Open the Streamlit URL.
2. Confirm NIFTY 50 / BANK NIFTY / INDIA VIX load.
3. Tap **LIVE FULL SCAN**.
4. Review **A+ PRE-BREAKOUT** and **PRE-BREAKOUT**.
5. Open **Stock Detail** for candidates.
6. Check the strategy checklist and chart.

### During market

Run **LIVE FULL SCAN** again when you want a new live snapshot.

The live price is refreshed from Upstox. The completed daily signal candle remains unchanged until a new session is completed.

### After market

The next session will treat the newly completed daily candle as part of the technical history.

## 6. Scanner score

The score is a transparent weighted checklist, not a probability.

Main components include:

- Close > EMA20
- EMA20 > SMA50
- SMA50 > SMA150
- SMA150 > EMA220
- 5D range contraction
- 7D range contraction
- ATR contraction
- volume contraction
- resistance proximity
- upper-range position
- NR7
- inside bar
- 52-week-high proximity
- breakout/volume confirmation bonus

A high score means more checklist conditions are satisfied. It does **not** guarantee a successful trade.

## 7. Breakout definition

A confirmed breakout requires:

```text
Last completed daily close
    >
Highest high of the preceding 252 completed sessions

AND

Last completed daily volume
    >=
1.5 × 20-day average volume
```

The live price can be above the breakout level intraday, but the app does not call that a confirmed daily-close breakout until the daily candle is complete.

## 8. Stop and targets

The displayed stop is derived from the lower of:

- recent 10-session swing low with a small buffer
- 5% below live price

If that produces an invalid stop, the app falls back to 5% below live price.

Targets are displayed as:

```text
T1 = Entry/Live + 2 × Risk
T2 = Entry/Live + 3 × Risk
```

These are scanner reference levels, not trade instructions.

## 9. Performance / API considerations

The scanner deliberately limits historical calls to the top live candidates.

Default candidate count:

```text
180
```

Available:

```text
100 / 150 / 180 / 250
```

The app uses a small thread pool for historical requests.

If your Upstox API limits or Streamlit resource limits are reached, reduce the candidate count.

## 10. Security

Never commit:

```text
.streamlit/secrets.toml
```

Never hard-code:

```python
UPSTOX_ACCESS_TOKEN = "..."
```

The app does not display the token and does not place orders.

## 11. Troubleshooting

### "Upstox token is not configured"

Add this to Streamlit Secrets:

```toml
UPSTOX_ACCESS_TOKEN = "YOUR_TOKEN"
```

Then reboot the app.

### "401 Unauthorized"

The token is invalid, expired or not accepted by Upstox. Generate a valid Upstox access token and update the Streamlit Secret.

### Scan is slow

Reduce:

```text
Candidates → 100 or 150
```

The expensive operation is historical data retrieval, so fewer candidates means fewer API calls.

### No stocks appear

This can be a legitimate result. The scanner does not manufacture candidates.

Check:

- market state
- Upstox connection
- candidate count
- available daily history
- whether the strategy conditions are currently satisfied

### Today's breakout is not marked confirmed

That is intentional. The app requires a completed daily candle for confirmation.

## 12. Git commands

```bash
git init
git add app.py requirements.txt README.md
git commit -m "StockPilot AI V8 Mobile"
git branch -M main
git remote add origin YOUR_GITHUB_REPOSITORY
git push -u origin main
```

## Disclaimer

StockPilot AI is a research/scanning tool. It does not guarantee returns, does not predict market outcomes and does not automatically execute trades. Verify prices, liquidity, corporate actions and market conditions before making any investment decision.
