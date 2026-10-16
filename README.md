# DBA4014 Paper Trade Dashboard v7

## Hotfix

v6 referenced `Path("manual_market.csv")` but did not import `Path` from Python's
`pathlib` module. That caused the Streamlit Cloud `NameError`.

v7 adds:

```python
from pathlib import Path
```

No dashboard logic or portfolio calculations were otherwise changed.


## Streamlit Cloud compatibility fix

v5 used `pykrx` for automatic KOSPI200 history. On Streamlit Cloud's current Python
environment, `pykrx` can fail during import because it still imports `pkg_resources`.

v6 removes the `pykrx` dependency entirely.

### KOSPI200 source hierarchy in v6

1. Yahoo `^KS200` if it returns a usable multi-day history.
2. Otherwise, `KODEX 200 (069500.KS)` as an automatic monitoring proxy.
3. Any professor/manual `KOSPI200` close entered in the dashboard overrides the automatic
   value on that date and is used in all benchmark calculations.

This makes the app deploy reliably while still allowing professor-exact benchmark values.


## Manual professor-price workflow

v5 adds an in-app **Professor / manual closing-price input** section.

You can enter:
- date
- ETF / benchmark ticker
- professor's official closing value
- optional note

Once saved, that value overrides the downloaded market close on the matching date for:
- portfolio NAV
- ETF return calculations
- KRW valuation
- pending MOC execution
- benchmark returns
- charts

The app also shows a comparison table:
- professor/manual close
- Yahoo/market close
- percentage deviation

### Saving behavior

**Local Streamlit:** values are written to `manual_market.csv` and persist on your computer.

**Streamlit Community Cloud:** local file writes can disappear after restart/redeploy, so v5 includes:
- Download manual-price backup CSV
- Restore/import manual-price CSV

For fully automatic persistent cloud storage, a later version can connect this same table to
Google Sheets or a free database such as Supabase.


## KOSPI200 benchmark fix

v3 could still show KOSPI200 as 0% because Yahoo/yfinance's `^KS200` feed can return only
a current observation instead of the full requested history. When that happens, the dashboard
has no prior KOSPI200 observations, so percentage changes become 0/None.

v4 changes the benchmark source:

1. **Primary:** KRX KOSPI200 index via `pykrx`, index code `1028`.
2. **Fallback:** Yahoo `^KS200` only when it returns a usable multi-day history.
3. **Last-resort proxy:** KODEX 200 (`069500.KS`), clearly labeled in the dashboard.

The Professor Benchmark table now shows the data source being used.
This is separate from portfolio pricing and does not change your ETF transactions.


This version focuses on intuitive monitoring.

## New in v3

### Current portfolio table now shows
- when each ETF was first acquired
- the latest team adjustment and date
- retain / sell % / additional buy information
- cash as a portfolio row
- latest price date
- latest completed-close move
- return versus average KRW acquisition cost
- KRW market value and current weight

### Charts
1. Portfolio vs professor benchmark
2. Every ETF's price trend, including ETFs already sold
3. Separate benchmark component trends:
   - KOSPI200
   - S&P500 hedged
   - S&P500 unhedged
   - combined benchmark

All price-trend charts normalize the starting point to 100 so ETFs with very different
share prices can be compared directly.

### Positive / negative colors
Positive percentage numbers are green and negative numbers are red in the portfolio and
benchmark tables.

## Price timing

The dashboard uses completed daily closes, not a live intraday feed.

Every ETF row includes `가격 기준일`, so you can see exactly which trading day's price is
being used. Yahoo downloads are cached for 15 minutes. Use the Refresh button in the sidebar
to force a new download.

## Pending MOC orders

16-Oct MOC orders stay labeled as submitted/pending until the exact 16-Oct closing price is
available. Once available, the dashboard can activate them automatically for personal tracking.

## Run

```bash
pip install -r requirements.txt
streamlit run app.py
```
