# DBA4014 Paper Trade Dashboard v3

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
