# DBA4014 Paper Trade Dashboard

This dashboard was prepared from the submitted Week 1 and Week 2 order workbook.

## What it tracks

- Current ETF holdings and quantities
- Current price and daily move for each ETF
- KRW-converted market value
- Current portfolio weight
- Return versus average KRW acquisition cost
- Portfolio NAV and cumulative return
- Professor benchmark beside the portfolio:
  - 40% KOSPI200
  - 30% S&P500 with KRW/USD hedged
  - 30% S&P500 unhedged
  - benchmark weights reset after each Friday close
- Current cash balance and allocation
- Portfolio vs benchmark chart

## Current Week 2 holdings encoded in the transaction ledger

After the submitted 16-Oct adjustment, the ledger produces these ETF quantities:

- SPY: 2,400
- SMH: 639
- CIBR: 3,452
- BIL: 8,159
- SCHD: 34,286
- XLV: 2,420
- XLP: 3,298
- XLE: 3,221
- KODEX 200: 0 after the 14-Oct loss-cut

Cash is calculated automatically from the KRW 10 billion starting balance and the transaction ledger.

## Important price note

The app uses Yahoo Finance through `yfinance` for convenience. This can differ from the official prices and FX values used by your professor.

For course-accurate tracking, open `transactions.csv` and fill in:

- `manual_price` = professor's official Friday closing price
- `manual_fx` = professor's official Friday closing USD/KRW rate

If those cells are blank, the dashboard fetches Yahoo Finance closing values automatically.

The KODEX 200 Week 1 buy price and 14-Oct loss-cut price are already entered manually from the submitted course data.

---

## Easiest way to run it locally

### 1. Install Python
Install Python 3.11 or newer from python.org.

### 2. Open Terminal / Command Prompt in this folder

### 3. Create a virtual environment (recommended)

macOS / Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Windows:

```bat
python -m venv .venv
.venv\Scripts\activate
```

### 4. Install the packages

```bash
pip install -r requirements.txt
```

### 5. Run the dashboard

```bash
streamlit run app.py
```

A browser window should open automatically.

---

## Free online deployment with Streamlit Community Cloud

1. Create a GitHub account if you do not already have one.
2. Create a repository.
3. Upload `app.py`, `transactions.csv`, and `requirements.txt`.
4. Go to Streamlit Community Cloud.
5. Connect the GitHub repository.
6. Select `app.py` and deploy.

The app will then have a web link that you can open from your phone or laptop.

## Adding next week's trades

Just append new rows to `transactions.csv`.

Example:

```csv
2026-10-23,SPY,SPDR S&P 500 ETF Trust,SELL,100,USD,,,Week 3 rebalance
```

The dashboard will automatically recalculate holdings, cash, NAV, weights, and P/L.

## Caveats

- This is a personal monitoring tool, not a broker or official exchange feed.
- Yahoo Finance may have delayed data or symbol differences.
- The hedged S&P500 benchmark is approximated as the S&P500 local return with FX movement removed.
- The unhedged S&P500 benchmark includes both S&P500 return and USD/KRW movement.
- The weekly reset is implemented after Friday close.
