# DBA4014 Paper Trade Dashboard v9

## Weekly workflow

You no longer edit CSV files.

### Friday
Upload the latest **Orders workbook** directly in the Streamlit sidebar.

### Monday
Upload the latest **Valuation workbook** directly in the Streamlit sidebar.

The course workbook already contains earlier weekly date tabs, so only the newest copy of
each workbook is needed.

The dashboard parses:
- current / outstanding positions
- quantities
- cash balance
- official valuation snapshots
- completed trades
- first acquisition dates
- latest completed adjustments
- latest submitted Friday orders

## Market data

Current monitoring uses Yahoo Finance online prices.

- The market-sensitive dashboard section automatically reruns every **1 hour** while the page is open.
- Price downloads are cached for 55 minutes.
- Hourly data are used when Yahoo provides them.
- Daily close is used as a fallback.
- Yahoo is not an exchange-grade real-time feed, so prices can be delayed.

One hour is intentional: for a 6-week weekly-trading project, shorter intervals add little
decision value and create more API traffic.

## Portfolio logic

The latest Monday valuation is authoritative for:
- holdings
- cash
- official NAV snapshot

If a newer Friday order workbook exists but the following Monday valuation has not yet been
uploaded, the dashboard applies the submitted order quantities as an **estimate** until the
next valuation becomes available.

## Benchmark

The professor benchmark remains:
- 40% KOSPI200
- 30% S&P500 hedged
- 30% S&P500 unhedged
- weekly reset to 40/30/30

For stable automatic monitoring, the KOSPI200 sleeve always uses **KODEX 200 returns as a fixed
proxy**. This is deliberate. Earlier versions switched between unreliable KOSPI200 web sources,
which caused the zero/incorrect benchmark readings.

## Streamlit Cloud persistence

When you upload a new workbook, the app tries to keep a copy in `runtime_uploads/`.
This normally survives ordinary Streamlit reruns, but Streamlit Community Cloud local storage
is not guaranteed to survive a container restart/redeploy.

Because each latest course workbook already contains the earlier tabs, recovery is simple:
just re-upload the newest Orders workbook and newest Valuation workbook.
