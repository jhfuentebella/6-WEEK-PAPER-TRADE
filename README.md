# DBA4014 Paper Trade Dashboard v8

## What changed

- Removed the on-site manual-price entry section.
- Each week, add a new CSV file named like:
  `professor_prices_20261016.csv`
- The app automatically loads all `professor_prices_*.csv` files.
- The final Transaction History table now shows:
  - Professor price
  - Yahoo close
  - Yahoo price date
  - Percentage difference
- If professor price is not available, the Professor price and Difference cells stay blank.
- Matching professor ETF / S&P500 / FX values override Yahoo values in calculations.

## Weekly professor file format

Use the included `professor_prices_TEMPLATE.csv`.

Required columns:

```csv
date,ticker,professor_price,note
2026-10-16,SPY,780.25,Friday close
2026-10-16,SMH,560.10,Friday close
2026-10-16,KRW=X,1348.50,Professor FX
```

You can add one new file every week. Do not delete the older weekly files if you want
the transaction-history comparison to remain visible for prior weeks.

## KOSPI200 benchmark source

The previous versions tried multiple automatic KOSPI200 sources. Yahoo's `^KS200`
history was inconsistent on Streamlit Cloud, and `pykrx` introduced a Cloud dependency error.

v8 intentionally stops switching among those sources.

For DAILY MONITORING:
- the KOSPI200 sleeve uses KODEX 200 RETURNS as an explicit stable proxy.

This is a deliberate proxy, not an exact KOSPI200 index level.

The professor's exact KOSPI200 snapshots can still be stored in the weekly professor file,
but v8 does not mix KOSPI200 index levels directly into KODEX ETF price levels because they
are different units. This avoids the large artificial jumps that such mixing would create.

## Run

```bash
pip install -r requirements.txt
streamlit run app.py
```
