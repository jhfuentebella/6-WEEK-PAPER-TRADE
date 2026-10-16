import math
from datetime import date, timedelta

import numpy as np
import pandas as pd
import streamlit as st
import yfinance as yf
import plotly.graph_objects as go

st.set_page_config(page_title="DBA4014 Portfolio Dashboard", layout="wide")

STARTING_CAPITAL = 10_000_000_000  # KRW
TRACKING_START = pd.Timestamp("2026-10-09")
CASH_WEEKLY_RATE = 0.00035
FX_TICKER = "KRW=X"  # KRW per USD on Yahoo Finance

BENCHMARK_WEIGHTS = {
    "KOSPI200": 0.40,
    "S&P500 Hedged": 0.30,
    "S&P500 Unhedged": 0.30,
}
BENCHMARK_TICKERS = {
    "KOSPI200": "^KS200",
    "S&P500": "^GSPC",
    "USD/KRW": FX_TICKER,
}


@st.cache_data(ttl=900)
def get_close(ticker: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
    """Download daily close prices from Yahoo Finance."""
    # yfinance end date is exclusive, so add a few days.
    df = yf.download(
        ticker,
        start=start.strftime("%Y-%m-%d"),
        end=(end + pd.Timedelta(days=3)).strftime("%Y-%m-%d"),
        auto_adjust=False,
        progress=False,
        threads=False,
    )
    if df.empty:
        return pd.Series(dtype=float, name=ticker)

    close = df["Close"]
    # Recent yfinance versions can return a 1-column DataFrame.
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]
    close.index = pd.to_datetime(close.index).tz_localize(None)
    close = close.astype(float).dropna()
    close.name = ticker
    return close


def asof_value(series: pd.Series, dt: pd.Timestamp):
    s = series.loc[:dt]
    if s.empty:
        return np.nan
    return float(s.iloc[-1])


def price_on_date(series: pd.Series, dt: pd.Timestamp):
    s = series.loc[series.index == dt]
    if not s.empty:
        return float(s.iloc[-1])
    return asof_value(series, dt)


def load_transactions():
    tx = pd.read_csv("transactions.csv")
    tx["date"] = pd.to_datetime(tx["date"])
    tx["manual_price"] = pd.to_numeric(tx["manual_price"], errors="coerce")
    tx["manual_fx"] = pd.to_numeric(tx["manual_fx"], errors="coerce")
    tx["quantity"] = pd.to_numeric(tx["quantity"], errors="coerce")
    return tx.sort_values(["date", "ticker"]).reset_index(drop=True)


def krw_unit_value(ticker, local_price, fx):
    if ticker.endswith(".KS") or ticker.endswith(".KQ"):
        return local_price
    return local_price * fx


def build_data(tx, end_date):
    tickers = sorted(set(tx["ticker"].tolist()))
    all_needed = tickers + list(BENCHMARK_TICKERS.values())
    price_map = {}

    fetch_start = TRACKING_START - pd.Timedelta(days=7)
    for ticker in sorted(set(all_needed)):
        price_map[ticker] = get_close(ticker, fetch_start, end_date)

    # Build shared daily index from tracking start through latest available day.
    idx = pd.date_range(TRACKING_START, end_date, freq="D")
    fx_raw = price_map[FX_TICKER].reindex(idx).ffill()

    # Forward-fill closes across non-trading days for valuation continuity.
    daily_price = {}
    for ticker in tickers:
        daily_price[ticker] = price_map[ticker].reindex(idx).ffill()

    # Portfolio ledger
    cash = STARTING_CAPITAL
    holdings = {t: 0.0 for t in tickers}
    avg_cost_krw = {t: 0.0 for t in tickers}
    realized_pnl = 0.0
    nav_rows = []

    tx_by_date = {d: g for d, g in tx.groupby("date")}

    for dt in idx:
        # Execute trades at close.
        if dt in tx_by_date:
            for _, row in tx_by_date[dt].iterrows():
                ticker = row["ticker"]
                action = str(row["action"]).upper()
                qty = float(row["quantity"])

                local_price = (
                    float(row["manual_price"])
                    if pd.notna(row["manual_price"])
                    else price_on_date(price_map[ticker], dt)
                )
                fx = (
                    1.0
                    if ticker.endswith(".KS") or ticker.endswith(".KQ")
                    else (
                        float(row["manual_fx"])
                        if pd.notna(row["manual_fx"])
                        else price_on_date(price_map[FX_TICKER], dt)
                    )
                )

                if np.isnan(local_price) or np.isnan(fx):
                    continue

                unit_krw = krw_unit_value(ticker, local_price, fx)
                trade_value = qty * unit_krw

                if action == "BUY":
                    old_qty = holdings[ticker]
                    old_cost_total = old_qty * avg_cost_krw[ticker]
                    new_qty = old_qty + qty
                    avg_cost_krw[ticker] = (
                        (old_cost_total + trade_value) / new_qty if new_qty else 0.0
                    )
                    holdings[ticker] = new_qty
                    cash -= trade_value

                elif action == "SELL":
                    sell_qty = min(qty, holdings[ticker])
                    realized_pnl += sell_qty * (unit_krw - avg_cost_krw[ticker])
                    holdings[ticker] -= sell_qty
                    cash += sell_qty * unit_krw
                    if abs(holdings[ticker]) < 1e-9:
                        holdings[ticker] = 0.0
                        avg_cost_krw[ticker] = 0.0

        # Course cash interest: apply on Fridays to positive KRW cash.
        if dt.weekday() == 4 and cash > 0:
            cash *= (1 + CASH_WEEKLY_RATE)

        fx = asof_value(fx_raw, dt)
        market_value = 0.0
        for ticker, qty in holdings.items():
            if qty == 0:
                continue
            p = asof_value(daily_price[ticker], dt)
            if np.isnan(p):
                continue
            unit = p if ticker.endswith(".KS") or ticker.endswith(".KQ") else p * fx
            market_value += qty * unit

        nav_rows.append(
            {
                "date": dt,
                "cash": cash,
                "market_value": market_value,
                "nav": cash + market_value,
            }
        )

    nav = pd.DataFrame(nav_rows).set_index("date")
    return price_map, daily_price, fx_raw, holdings, avg_cost_krw, realized_pnl, nav


def build_benchmark(price_map, end_date):
    idx = pd.date_range(TRACKING_START, end_date, freq="D")
    k200 = price_map["^KS200"].reindex(idx).ffill()
    sp = price_map["^GSPC"].reindex(idx).ffill()
    fx = price_map[FX_TICKER].reindex(idx).ffill()

    k_ret = k200.pct_change().fillna(0)
    sp_ret = sp.pct_change().fillna(0)
    fx_ret = fx.pct_change().fillna(0)

    # Hedged: approximate as S&P500 local USD return.
    hedged_ret = sp_ret
    # Unhedged KRW return includes both S&P500 and USD/KRW.
    unhedged_ret = (1 + sp_ret) * (1 + fx_ret) - 1

    component_rets = pd.DataFrame(
        {
            "KOSPI200": k_ret,
            "S&P500 Hedged": hedged_ret,
            "S&P500 Unhedged": unhedged_ret,
        },
        index=idx,
    )

    # Weekly-reset benchmark:
    # Start each week at 40/30/30. Let sleeves drift through the week,
    # then reset after Friday close (or next available displayed day).
    total_nav = 100.0
    sleeves = {
        k: total_nav * w for k, w in BENCHMARK_WEIGHTS.items()
    }
    rows = []

    for dt, row in component_rets.iterrows():
        for name in sleeves:
            sleeves[name] *= 1 + float(row[name])

        total_nav = sum(sleeves.values())
        rows.append(
            {
                "date": dt,
                "Benchmark": total_nav,
                **{name: sleeves[name] for name in sleeves},
            }
        )

        if dt.weekday() == 4:
            sleeves = {
                k: total_nav * w for k, w in BENCHMARK_WEIGHTS.items()
            }

    b = pd.DataFrame(rows).set_index("date")
    return component_rets, b


def fmt_pct(x):
    if pd.isna(x):
        return "-"
    return f"{x*100:,.2f}%"


def fmt_krw(x):
    if pd.isna(x):
        return "-"
    return f"₩{x:,.0f}"


st.title("DBA4014 Paper Trade Dashboard")
st.caption(
    "Personal monitoring dashboard. Yahoo Finance prices can differ from the professor's official "
    "Friday-close/FX data, so use the course valuation sheet as the final source for submissions."
)

with st.sidebar:
    st.header("Settings")
    end_date = pd.Timestamp(st.date_input("As-of date", value=date.today()))
    st.write("Tracking start:", TRACKING_START.date())
    st.write("Starting capital:", f"₩{STARTING_CAPITAL:,.0f}")
    st.write("Cash interest:", f"{CASH_WEEKLY_RATE*100:.3f}% / week")
    st.info(
        "To match the class exactly, edit transactions.csv and enter manual_price / manual_fx "
        "for each official trade."
    )

tx = load_transactions()
price_map, daily_price, fx_series, holdings, avg_cost_krw, realized_pnl, nav = build_data(tx, end_date)
component_rets, benchmark = build_benchmark(price_map, end_date)

common_idx = nav.index.intersection(benchmark.index)
if len(common_idx) == 0:
    st.error("No market data was returned. Check your internet connection or Yahoo Finance symbols.")
    st.stop()

nav = nav.loc[common_idx]
benchmark = benchmark.loc[common_idx]

portfolio_index = nav["nav"] / nav["nav"].iloc[0] * 100
benchmark_index = benchmark["Benchmark"] / benchmark["Benchmark"].iloc[0] * 100

portfolio_return = portfolio_index.iloc[-1] / 100 - 1
benchmark_return = benchmark_index.iloc[-1] / 100 - 1
excess = portfolio_return - benchmark_return
current_cash = nav["cash"].iloc[-1]
current_nav = nav["nav"].iloc[-1]

m1, m2, m3, m4 = st.columns(4)
m1.metric("Portfolio NAV", fmt_krw(current_nav), fmt_pct(portfolio_return))
m2.metric("Benchmark return", fmt_pct(benchmark_return))
m3.metric("Excess return", fmt_pct(excess))
m4.metric("Cash weight", fmt_pct(current_cash / current_nav if current_nav else np.nan))

st.subheader("Portfolio vs. Professor Benchmark")
fig = go.Figure()
fig.add_trace(go.Scatter(x=portfolio_index.index, y=portfolio_index, name="Portfolio", mode="lines"))
fig.add_trace(go.Scatter(x=benchmark_index.index, y=benchmark_index, name="Benchmark", mode="lines"))
fig.update_layout(
    height=410,
    yaxis_title="Index (start = 100)",
    xaxis_title="Date",
    hovermode="x unified",
    margin=dict(l=10, r=10, t=20, b=10),
)
st.plotly_chart(fig, use_container_width=True)

left, right = st.columns([1.45, 1])

with left:
    st.subheader("Current portfolio status")

    fx_now = asof_value(fx_series, common_idx[-1])
    rows = []

    for ticker, qty in holdings.items():
        if qty <= 0:
            continue

        s = price_map[ticker]
        if s.empty:
            continue
        s = s.loc[:end_date]
        if len(s) < 1:
            continue

        current_price = float(s.iloc[-1])
        prev_price = float(s.iloc[-2]) if len(s) >= 2 else current_price
        local_daily = current_price / prev_price - 1 if prev_price else np.nan

        unit_krw = (
            current_price
            if ticker.endswith(".KS") or ticker.endswith(".KQ")
            else current_price * fx_now
        )
        mv = qty * unit_krw
        weight = mv / current_nav if current_nav else np.nan
        pnl_pct = (
            unit_krw / avg_cost_krw[ticker] - 1
            if avg_cost_krw[ticker] > 0
            else np.nan
        )

        rows.append(
            {
                "ETF": ticker,
                "수량": int(round(qty)),
                "현재가": current_price,
                "오늘 등락": local_daily,
                "원화 평가액": mv,
                "현재 비중": weight,
                "평균 원화원가 대비": pnl_pct,
            }
        )

    ptable = pd.DataFrame(rows)
    if not ptable.empty:
        ptable = ptable.sort_values("현재 비중", ascending=False)
        st.dataframe(
            ptable.style.format(
                {
                    "수량": "{:,.0f}",
                    "현재가": "{:,.2f}",
                    "오늘 등락": "{:+.2%}",
                    "원화 평가액": "₩{:,.0f}",
                    "현재 비중": "{:.2%}",
                    "평균 원화원가 대비": "{:+.2%}",
                }
            ),
            use_container_width=True,
            hide_index=True,
        )

    st.caption(f"Realized P/L tracked by ledger: {fmt_krw(realized_pnl)}")

with right:
    st.subheader("Professor benchmark")

    latest = component_rets.loc[common_idx[-1]]
    # Component cumulative return since start (not weekly-reset aggregate).
    comp_cum = (1 + component_rets.loc[common_idx]).prod() - 1

    btable = pd.DataFrame(
        {
            "구성": ["KOSPI200", "S&P500 Hedged", "S&P500 Unhedged"],
            "교수님 비중": [0.40, 0.30, 0.30],
            "오늘 등락": [
                latest["KOSPI200"],
                latest["S&P500 Hedged"],
                latest["S&P500 Unhedged"],
            ],
            "시작 이후 등락": [
                comp_cum["KOSPI200"],
                comp_cum["S&P500 Hedged"],
                comp_cum["S&P500 Unhedged"],
            ],
        }
    )
    st.dataframe(
        btable.style.format(
            {
                "교수님 비중": "{:.0%}",
                "오늘 등락": "{:+.2%}",
                "시작 이후 등락": "{:+.2%}",
            }
        ),
        use_container_width=True,
        hide_index=True,
    )

    st.markdown(
        """
**Benchmark calculation**
- 40% KOSPI200
- 30% S&P500, KRW/USD hedged
- 30% S&P500, KRW/USD unhedged
- Weights reset to 40/30/30 after each Friday close
        """
    )

st.subheader("Current allocation")
alloc_rows = []
for ticker, qty in holdings.items():
    if qty <= 0:
        continue
    s = price_map[ticker].loc[:end_date]
    if s.empty:
        continue
    p = float(s.iloc[-1])
    unit = p if ticker.endswith(".KS") or ticker.endswith(".KQ") else p * fx_now
    alloc_rows.append({"Asset": ticker, "KRW Value": qty * unit})
alloc_rows.append({"Asset": "KRW Cash", "KRW Value": current_cash})
alloc = pd.DataFrame(alloc_rows)
alloc["Weight"] = alloc["KRW Value"] / alloc["KRW Value"].sum()

fig2 = go.Figure(
    data=[go.Pie(labels=alloc["Asset"], values=alloc["Weight"], hole=0.45)]
)
fig2.update_layout(height=420, margin=dict(l=10, r=10, t=20, b=10))
st.plotly_chart(fig2, use_container_width=True)

with st.expander("Transaction ledger"):
    st.dataframe(tx, use_container_width=True, hide_index=True)

st.caption(
    "Data source: Yahoo Finance via yfinance. This is for personal monitoring, not an official "
    "exchange feed. Enter the professor's official Friday closing prices/FX in transactions.csv "
    "if you want the dashboard to match the course valuation workbook."
)
