from datetime import date, datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yfinance as yf

st.set_page_config(page_title="DBA4014 Portfolio Dashboard v3", layout="wide")

STARTING_CAPITAL = 10_000_000_000
TRACKING_START = pd.Timestamp("2026-10-09")
CASH_WEEKLY_RATE = 0.00035
FX_TICKER = "KRW=X"
SEOUL = ZoneInfo("Asia/Seoul")

BENCHMARK_WEIGHTS = {
    "KOSPI200": 0.40,
    "S&P500 Hedged": 0.30,
    "S&P500 Unhedged": 0.30,
}


# -----------------------------
# Data download / alignment
# -----------------------------

@st.cache_data(ttl=900)
def get_close(ticker: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
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
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]

    close.index = pd.to_datetime(close.index).tz_localize(None)
    close = close.astype(float).dropna().sort_index()
    close.name = ticker
    return close


def align_with_prior_close(series: pd.Series, idx: pd.DatetimeIndex) -> pd.Series:
    if series.empty:
        return pd.Series(index=idx, dtype=float)
    expanded = series.reindex(series.index.union(idx)).sort_index().ffill()
    return expanded.reindex(idx)


def exact_value(series: pd.Series, dt: pd.Timestamp):
    if series.empty:
        return np.nan
    row = series.loc[series.index == dt]
    return np.nan if row.empty else float(row.iloc[-1])


def asof_value(series: pd.Series, dt: pd.Timestamp):
    if series.empty:
        return np.nan
    s = series.loc[:dt]
    return np.nan if s.empty else float(s.iloc[-1])


def latest_close_info(series: pd.Series, end_date: pd.Timestamp):
    s = series.loc[:end_date].dropna()
    if s.empty:
        return None, np.nan, np.nan, np.nan
    latest_date = s.index[-1]
    latest_price = float(s.iloc[-1])
    prev_price = float(s.iloc[-2]) if len(s) >= 2 else np.nan
    change = (
        latest_price / prev_price - 1
        if pd.notna(prev_price) and prev_price != 0
        else np.nan
    )
    return latest_date, latest_price, prev_price, change


def load_csv(path):
    df = pd.read_csv(path)
    df["date"] = pd.to_datetime(df["date"])
    for c in ["quantity", "manual_price", "manual_fx"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.sort_values(["date", "ticker"]).reset_index(drop=True)


def is_krw_ticker(ticker: str):
    return ticker.endswith(".KS") or ticker.endswith(".KQ")


def krw_unit_value(ticker, local_price, fx):
    return local_price if is_krw_ticker(ticker) else local_price * fx


# -----------------------------
# Trade-state helpers
# -----------------------------

def quantity_before(tx: pd.DataFrame, ticker: str, dt: pd.Timestamp):
    subset = tx[(tx["ticker"] == ticker) & (tx["date"] < dt)]
    qty = 0.0
    for _, row in subset.iterrows():
        if str(row["action"]).upper() == "BUY":
            qty += float(row["quantity"])
        elif str(row["action"]).upper() == "SELL":
            qty -= float(row["quantity"])
    return max(qty, 0.0)


def current_quantities(tx: pd.DataFrame):
    q = {}
    for _, row in tx.iterrows():
        ticker = row["ticker"]
        q.setdefault(ticker, 0.0)
        if str(row["action"]).upper() == "BUY":
            q[ticker] += float(row["quantity"])
        elif str(row["action"]).upper() == "SELL":
            q[ticker] -= float(row["quantity"])
    return {k: max(v, 0.0) for k, v in q.items()}


def first_acquisition_date(all_orders: pd.DataFrame, ticker: str):
    buys = all_orders[
        (all_orders["ticker"] == ticker)
        & (all_orders["action"].str.upper() == "BUY")
    ]
    if buys.empty:
        return None
    return buys["date"].min()


def adjustment_description(executed, pending, ticker, latest_submission_date):
    """
    Human-friendly latest portfolio decision.
    Pending orders take priority because they represent the latest submitted decision.
    If no pending order exists on the latest rebalance date and the position is held,
    display Retain.
    """
    p = pending[pending["ticker"] == ticker].sort_values("date")
    if not p.empty:
        row = p.iloc[-1]
        dt = row["date"]
        action = str(row["action"]).upper()
        qty = float(row["quantity"])
        pre = quantity_before(executed, ticker, dt)

        if action == "SELL":
            pct = qty / pre if pre > 0 else np.nan
            if pd.notna(pct):
                return f"{dt:%Y-%m-%d} • Sell {pct:.1%} (MOC submitted)"
            return f"{dt:%Y-%m-%d} • Sell {qty:,.0f} (MOC submitted)"

        if action == "BUY":
            if pre <= 0:
                return f"{dt:%Y-%m-%d} • New buy {qty:,.0f} (MOC submitted)"
            pct = qty / pre
            return f"{dt:%Y-%m-%d} • Buy additional +{pct:.1%} (MOC submitted)"

    e = executed[executed["ticker"] == ticker].sort_values("date")
    if not e.empty:
        last = e.iloc[-1]
        dt = last["date"]
        action = str(last["action"]).upper()
        qty = float(last["quantity"])
        pre = quantity_before(executed, ticker, dt)

        # If latest team rebalance date is later and there was no trade, call it retained.
        current_qty = current_quantities(executed).get(ticker, 0.0)
        if latest_submission_date is not None and latest_submission_date > dt and current_qty > 0:
            return f"{latest_submission_date:%Y-%m-%d} • Retain (no change)"

        if action == "SELL":
            pct = qty / pre if pre > 0 else np.nan
            if pd.notna(pct):
                return f"{dt:%Y-%m-%d} • Sold {pct:.1%}"
            return f"{dt:%Y-%m-%d} • Sold {qty:,.0f}"

        if action == "BUY":
            if pre <= 0:
                return f"{dt:%Y-%m-%d} • First buy"
            pct = qty / pre
            return f"{dt:%Y-%m-%d} • Bought additional +{pct:.1%}"

    return "-"


def build_effective_transactions(executed, pending, price_map, end_date, auto_execute_pending):
    effective = executed.copy()
    pending_display = pending.copy()
    pending_display["dashboard_status"] = "PENDING"

    if not auto_execute_pending:
        return effective, pending_display

    auto_rows = []
    for i, row in pending.iterrows():
        dt = row["date"]
        if dt > end_date:
            continue

        px = (
            row["manual_price"]
            if pd.notna(row["manual_price"])
            else exact_value(price_map[row["ticker"]], dt)
        )
        fx = (
            1.0
            if is_krw_ticker(row["ticker"])
            else (
                row["manual_fx"]
                if pd.notna(row["manual_fx"])
                else exact_value(price_map[FX_TICKER], dt)
            )
        )

        if pd.notna(px) and pd.notna(fx):
            new_row = row.copy()
            new_row["manual_price"] = px
            new_row["manual_fx"] = fx
            auto_rows.append(new_row)
            pending_display.loc[i, "dashboard_status"] = "EXECUTED IN DASHBOARD"

    if auto_rows:
        effective = pd.concat([effective, pd.DataFrame(auto_rows)], ignore_index=True)

    return effective.sort_values(["date", "ticker"]).reset_index(drop=True), pending_display


# -----------------------------
# Portfolio / benchmark engine
# -----------------------------

def build_portfolio(tx, price_map, end_date):
    tickers = sorted(set(tx["ticker"].tolist()))
    idx = pd.date_range(TRACKING_START, end_date, freq="D")

    fx_aligned = align_with_prior_close(price_map[FX_TICKER], idx)
    daily_price = {t: align_with_prior_close(price_map[t], idx) for t in tickers}

    cash = STARTING_CAPITAL
    holdings = {t: 0.0 for t in tickers}
    avg_cost_krw = {t: 0.0 for t in tickers}
    realized_pnl = 0.0
    nav_rows = []

    tx_by_date = {d: g for d, g in tx.groupby("date")}

    for dt in idx:
        if dt in tx_by_date:
            for _, row in tx_by_date[dt].iterrows():
                ticker = row["ticker"]
                action = str(row["action"]).upper()
                qty = float(row["quantity"])

                local_price = (
                    float(row["manual_price"])
                    if pd.notna(row["manual_price"])
                    else exact_value(price_map[ticker], dt)
                )
                fx = (
                    1.0
                    if is_krw_ticker(ticker)
                    else (
                        float(row["manual_fx"])
                        if pd.notna(row["manual_fx"])
                        else exact_value(price_map[FX_TICKER], dt)
                    )
                )

                if pd.isna(local_price) or pd.isna(fx):
                    continue

                unit_krw = krw_unit_value(ticker, local_price, fx)
                trade_value = qty * unit_krw

                if action == "BUY":
                    old_qty = holdings.get(ticker, 0.0)
                    old_cost_total = old_qty * avg_cost_krw.get(ticker, 0.0)
                    new_qty = old_qty + qty
                    avg_cost_krw[ticker] = (
                        (old_cost_total + trade_value) / new_qty
                        if new_qty else 0.0
                    )
                    holdings[ticker] = new_qty
                    cash -= trade_value

                elif action == "SELL":
                    sell_qty = min(qty, holdings.get(ticker, 0.0))
                    realized_pnl += sell_qty * (unit_krw - avg_cost_krw.get(ticker, 0.0))
                    holdings[ticker] = holdings.get(ticker, 0.0) - sell_qty
                    cash += sell_qty * unit_krw
                    if abs(holdings[ticker]) < 1e-9:
                        holdings[ticker] = 0.0
                        avg_cost_krw[ticker] = 0.0

        if dt.weekday() == 4 and cash > 0:
            cash *= (1 + CASH_WEEKLY_RATE)

        fx = asof_value(fx_aligned, dt)
        market_value = 0.0

        for ticker, qty in holdings.items():
            if qty == 0:
                continue
            p = asof_value(daily_price[ticker], dt)
            if pd.isna(p):
                continue
            unit = p if is_krw_ticker(ticker) else p * fx
            market_value += qty * unit

        nav_rows.append({
            "date": dt,
            "cash": cash,
            "market_value": market_value,
            "nav": cash + market_value,
        })

    return (
        daily_price,
        fx_aligned,
        holdings,
        avg_cost_krw,
        realized_pnl,
        pd.DataFrame(nav_rows).set_index("date"),
    )


def build_benchmark(price_map, end_date):
    idx = pd.date_range(TRACKING_START, end_date, freq="D")

    k200 = align_with_prior_close(price_map["^KS200"], idx)
    sp = align_with_prior_close(price_map["^GSPC"], idx)
    fx = align_with_prior_close(price_map[FX_TICKER], idx)

    k_ret = k200.pct_change().fillna(0)
    sp_ret = sp.pct_change().fillna(0)
    fx_ret = fx.pct_change().fillna(0)

    hedged_ret = sp_ret
    unhedged_ret = (1 + sp_ret) * (1 + fx_ret) - 1

    component_rets = pd.DataFrame({
        "KOSPI200": k_ret,
        "S&P500 Hedged": hedged_ret,
        "S&P500 Unhedged": unhedged_ret,
    }, index=idx)

    total_nav = 100.0
    sleeves = {k: total_nav * w for k, w in BENCHMARK_WEIGHTS.items()}
    rows = []

    for dt, row in component_rets.iterrows():
        for name in sleeves:
            sleeves[name] *= 1 + float(row[name])

        total_nav = sum(sleeves.values())
        rows.append({"date": dt, "Benchmark": total_nav, **sleeves})

        if dt.weekday() == 4:
            sleeves = {k: total_nav * w for k, w in BENCHMARK_WEIGHTS.items()}

    component_index = (1 + component_rets).cumprod() * 100

    return (
        component_rets,
        pd.DataFrame(rows).set_index("date"),
        component_index,
        {"KOSPI200": k200, "S&P500": sp, "USD/KRW": fx},
    )


# -----------------------------
# Formatting helpers
# -----------------------------

def fmt_pct(x):
    return "-" if pd.isna(x) else f"{x*100:,.2f}%"


def fmt_krw(x):
    return "-" if pd.isna(x) else f"₩{x:,.0f}"


def signed_color(v):
    if pd.isna(v):
        return ""
    if v > 0:
        return "color: #16a34a; font-weight: 700;"
    if v < 0:
        return "color: #dc2626; font-weight: 700;"
    return "color: #6b7280;"


# -----------------------------
# UI
# -----------------------------

st.title("DBA4014 Paper Trade Dashboard")
st.caption(
    "Designed for intuitive personal monitoring: current holdings, latest team decisions, "
    "ETF histories, and the professor benchmark."
)

with st.sidebar:
    st.header("Dashboard controls")
    end_date = pd.Timestamp(st.date_input("As-of date", value=date.today()))
    auto_execute_pending = st.checkbox(
        "Auto-activate MOC orders once that exact close is available",
        value=True,
    )

    if st.button("Refresh market data now", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    st.divider()
    st.write("Project start:", TRACKING_START.date())
    st.write("Starting capital:", f"₩{STARTING_CAPITAL:,.0f}")
    st.write("Cash interest:", f"{CASH_WEEKLY_RATE*100:.3f}% / week")
    st.caption("Yahoo downloads are cached for 15 minutes.")

executed = load_csv("transactions.csv")
pending = load_csv("pending_orders.csv")
latest_submission_date = pending["date"].max() if not pending.empty else None

all_orders = pd.concat([executed, pending], ignore_index=True)
all_tickers = sorted(set(all_orders["ticker"]))
needed = all_tickers + ["^KS200", "^GSPC", FX_TICKER]
fetch_start = TRACKING_START - pd.Timedelta(days=10)

price_map = {
    t: get_close(t, fetch_start, end_date)
    for t in sorted(set(needed))
}

effective_tx, pending_display = build_effective_transactions(
    executed, pending, price_map, end_date, auto_execute_pending
)

daily_price, fx_series, holdings, avg_cost_krw, realized_pnl, nav = build_portfolio(
    effective_tx, price_map, end_date
)
component_rets, benchmark, benchmark_component_index, benchmark_series = build_benchmark(
    price_map, end_date
)

common_idx = nav.index.intersection(benchmark.index)
if len(common_idx) == 0:
    st.error("No market data returned. Check internet connection or ticker symbols.")
    st.stop()

nav = nav.loc[common_idx]
benchmark = benchmark.loc[common_idx]

portfolio_index = nav["nav"] / STARTING_CAPITAL * 100
benchmark_index = benchmark["Benchmark"]
portfolio_return = nav["nav"].iloc[-1] / STARTING_CAPITAL - 1
benchmark_return = benchmark_index.iloc[-1] / 100 - 1
excess = portfolio_return - benchmark_return
current_cash = nav["cash"].iloc[-1]
current_nav = nav["nav"].iloc[-1]

refresh_stamp = datetime.now(SEOUL).strftime("%Y-%m-%d %H:%M KST")
st.caption(
    f"Dashboard refreshed: {refresh_stamp} • Uses completed daily closes, not live intraday quotes. "
    "Each row shows its own price date."
)

# ---- At a glance
st.subheader("1. At a glance")
m1, m2, m3, m4 = st.columns(4)
m1.metric("Portfolio NAV", fmt_krw(current_nav), fmt_pct(portfolio_return))
m2.metric("Portfolio return", fmt_pct(portfolio_return))
m3.metric("Benchmark return", fmt_pct(benchmark_return))
m4.metric("Excess return vs benchmark", fmt_pct(excess))

# ---- Current portfolio status
st.subheader("2. Current portfolio status")
st.caption(
    "Latest adjustment reflects the most recent team decision. Pending MOC orders are labeled as submitted "
    "until the exact closing price becomes available."
)

fx_now = asof_value(fx_series, common_idx[-1])
status_rows = []

for ticker, qty in holdings.items():
    if qty <= 0:
        continue

    latest_date, current_price, _, close_change = latest_close_info(price_map[ticker], end_date)
    if latest_date is None:
        continue

    unit_krw = current_price if is_krw_ticker(ticker) else current_price * fx_now
    mv = qty * unit_krw
    weight = mv / current_nav if current_nav else np.nan
    pnl_pct = (
        unit_krw / avg_cost_krw[ticker] - 1
        if avg_cost_krw.get(ticker, 0) > 0
        else np.nan
    )

    first_dt = first_acquisition_date(all_orders, ticker)
    status_rows.append({
        "자산": ticker,
        "상태": "보유 중",
        "최초 매수일": "-" if first_dt is None else first_dt.strftime("%Y-%m-%d"),
        "최근 조정": adjustment_description(executed, pending, ticker, latest_submission_date),
        "현재 수량": qty,
        "가격 기준일": latest_date.strftime("%Y-%m-%d"),
        "최근 종가": current_price,
        "최근 종가 등락": close_change,
        "매입원가 대비": pnl_pct,
        "원화 평가액": mv,
        "현재 비중": weight,
    })

# Cash as a portfolio row
last_exec_date = effective_tx["date"].max() if not effective_tx.empty else TRACKING_START
status_rows.append({
    "자산": "KRW Cash",
    "상태": "현금",
    "최초 매수일": TRACKING_START.strftime("%Y-%m-%d"),
    "최근 조정": f"{last_exec_date:%Y-%m-%d} • 거래 현금흐름 반영",
    "현재 수량": np.nan,
    "가격 기준일": common_idx[-1].strftime("%Y-%m-%d"),
    "최근 종가": np.nan,
    "최근 종가 등락": np.nan,
    "매입원가 대비": np.nan,
    "원화 평가액": current_cash,
    "현재 비중": current_cash / current_nav if current_nav else np.nan,
})

ptable = pd.DataFrame(status_rows).sort_values(
    ["상태", "현재 비중"], ascending=[True, False]
)

styled = (
    ptable.style
    .format({
        "현재 수량": lambda x: "-" if pd.isna(x) else f"{x:,.0f}",
        "최근 종가": lambda x: "-" if pd.isna(x) else f"{x:,.2f}",
        "최근 종가 등락": lambda x: "-" if pd.isna(x) else f"{x:+.2%}",
        "매입원가 대비": lambda x: "-" if pd.isna(x) else f"{x:+.2%}",
        "원화 평가액": lambda x: f"₩{x:,.0f}",
        "현재 비중": lambda x: "-" if pd.isna(x) else f"{x:.2%}",
    })
    .map(signed_color, subset=["최근 종가 등락", "매입원가 대비"])
)
st.dataframe(styled, use_container_width=True, hide_index=True)

# Exited positions
executed_qty = current_quantities(effective_tx)
exited = [
    t for t in sorted(set(executed["ticker"]))
    if executed_qty.get(t, 0.0) <= 0
]
if exited:
    with st.expander("Exited positions / sold-out history"):
        rows = []
        for ticker in exited:
            first_dt = first_acquisition_date(all_orders, ticker)
            latest_date, current_price, _, close_change = latest_close_info(price_map[ticker], end_date)
            rows.append({
                "ETF": ticker,
                "최초 매수일": "-" if first_dt is None else first_dt.strftime("%Y-%m-%d"),
                "최근 조정": adjustment_description(executed, pending, ticker, latest_submission_date),
                "현재 시장가격 기준일": "-" if latest_date is None else latest_date.strftime("%Y-%m-%d"),
                "최근 시장 등락": close_change,
            })
        exdf = pd.DataFrame(rows)
        exstyle = (
            exdf.style
            .format({"최근 시장 등락": lambda x: "-" if pd.isna(x) else f"{x:+.2%}"})
            .map(signed_color, subset=["최근 시장 등락"])
        )
        st.dataframe(exstyle, use_container_width=True, hide_index=True)

# Pending orders
if not pending_display.empty:
    with st.expander("Submitted / pending MOC adjustments"):
        show_pending = pending_display.copy()
        show_pending["date"] = show_pending["date"].dt.strftime("%Y-%m-%d")
        st.dataframe(
            show_pending[
                ["date", "ticker", "name", "action", "quantity", "dashboard_status", "notes"]
            ],
            use_container_width=True,
            hide_index=True,
        )

# ---- Charts
st.subheader("3. Performance charts")
tab1, tab2, tab3 = st.tabs([
    "Portfolio vs Benchmark",
    "All ETF price trends",
    "Benchmark components",
])

with tab1:
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=portfolio_index.index,
        y=portfolio_index,
        name="Portfolio",
        mode="lines",
        line=dict(width=3),
    ))
    fig.add_trace(go.Scatter(
        x=benchmark_index.index,
        y=benchmark_index,
        name="Professor Benchmark",
        mode="lines",
        line=dict(width=3),
    ))
    fig.add_hline(y=100, line_dash="dot")
    fig.update_layout(
        height=450,
        yaxis_title="Index (start = 100)",
        xaxis_title="Date",
        hovermode="x unified",
        margin=dict(l=10, r=10, t=20, b=10),
        legend=dict(orientation="h"),
    )
    st.plotly_chart(fig, use_container_width=True)

with tab2:
    st.caption(
        "Each ETF is normalized to 100 at the project start so different price levels can be compared. "
        "The chart keeps showing ETFs even after they were sold. Click a legend item to hide/show a line."
    )
    fig_etf = go.Figure()
    idx = pd.date_range(TRACKING_START, end_date, freq="D")

    for ticker in all_tickers:
        aligned = align_with_prior_close(price_map[ticker], idx)
        valid = aligned.dropna()
        if valid.empty:
            continue
        base = float(valid.iloc[0])
        normalized = aligned / base * 100
        fig_etf.add_trace(go.Scatter(
            x=normalized.index,
            y=normalized,
            name=ticker,
            mode="lines",
        ))

    fig_etf.add_hline(y=100, line_dash="dot")
    fig_etf.update_layout(
        height=520,
        yaxis_title="ETF price index (project start = 100)",
        xaxis_title="Date",
        hovermode="x unified",
        margin=dict(l=10, r=10, t=20, b=10),
        legend=dict(orientation="h"),
    )
    st.plotly_chart(fig_etf, use_container_width=True)

with tab3:
    st.caption(
        "Shows the three benchmark sleeves separately plus the combined professor benchmark."
    )
    fig_b = go.Figure()
    for col in ["KOSPI200", "S&P500 Hedged", "S&P500 Unhedged"]:
        fig_b.add_trace(go.Scatter(
            x=benchmark_component_index.index,
            y=benchmark_component_index[col],
            name=col,
            mode="lines",
        ))
    fig_b.add_trace(go.Scatter(
        x=benchmark_index.index,
        y=benchmark_index,
        name="Combined Benchmark",
        mode="lines",
        line=dict(width=4),
    ))
    fig_b.add_hline(y=100, line_dash="dot")
    fig_b.update_layout(
        height=500,
        yaxis_title="Index (start = 100)",
        xaxis_title="Date",
        hovermode="x unified",
        margin=dict(l=10, r=10, t=20, b=10),
        legend=dict(orientation="h"),
    )
    st.plotly_chart(fig_b, use_container_width=True)

# ---- Benchmark detail table
st.subheader("4. Professor benchmark detail")
comp_cum = (1 + component_rets.loc[common_idx]).prod() - 1
benchmark_rows = []

for name, weight in BENCHMARK_WEIGHTS.items():
    source_name = "KOSPI200" if name == "KOSPI200" else "S&P500"
    source_series = benchmark_series[source_name]
    latest_date, _, _, latest_change = latest_close_info(source_series, end_date)

    if name == "S&P500 Unhedged":
        latest_change = component_rets.loc[common_idx[-1], name]

    benchmark_rows.append({
        "구성": name,
        "교수님 비중": weight,
        "가격 기준일": "-" if latest_date is None else latest_date.strftime("%Y-%m-%d"),
        "최근 종가 등락": latest_change,
        "시작 이후 등락": comp_cum[name],
    })

btable = pd.DataFrame(benchmark_rows)
bstyled = (
    btable.style
    .format({
        "교수님 비중": "{:.0%}",
        "최근 종가 등락": lambda x: "-" if pd.isna(x) else f"{x:+.2%}",
        "시작 이후 등락": lambda x: "-" if pd.isna(x) else f"{x:+.2%}",
    })
    .map(signed_color, subset=["최근 종가 등락", "시작 이후 등락"])
)
st.dataframe(bstyled, use_container_width=True, hide_index=True)

st.caption(
    "Benchmark = 40% KOSPI200 + 30% S&P500 hedged + 30% S&P500 unhedged, "
    "reset to 40/30/30 after each Friday close."
)

with st.expander("Executed transaction ledger"):
    st.dataframe(effective_tx, use_container_width=True, hide_index=True)

st.caption(
    "Personal monitoring only. Market data are Yahoo Finance completed daily closes and can differ "
    "from the professor's official Friday price/FX source. Enter official manual prices/FX in the CSV "
    "files when you want the dashboard to match the course valuation exactly."
)
