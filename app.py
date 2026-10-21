from __future__ import annotations

from datetime import datetime
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo
import zipfile

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yfinance as yf

from dashboard_core import (
    current_position_state,
    first_acquisition_dates,
    latest_adjustments,
    parse_order_workbook,
    parse_valuation_workbook,
)

st.set_page_config(page_title="DBA4014 Paper Trade Dashboard", layout="wide")

SEOUL = ZoneInfo("Asia/Seoul")
STARTING_CAPITAL = 10_000_000_000
PROJECT_START = pd.Timestamp("2026-10-09")

BASE = Path(__file__).resolve().parent
SEED_ORDER = BASE / "seed_data" / "current_order.xlsx"
SEED_VALUATION = BASE / "seed_data" / "current_valuation.xlsx"
RUNTIME = BASE / "runtime_uploads"
RUNTIME.mkdir(exist_ok=True)
RUNTIME_ORDER = RUNTIME / "current_order.xlsx"
RUNTIME_VALUATION = RUNTIME / "current_valuation.xlsx"


def read_file_bytes(uploaded, runtime_path: Path, seed_path: Path):
    if uploaded is not None:
        b = uploaded.getvalue()
        try:
            runtime_path.write_bytes(b)
        except Exception:
            pass
        return b, uploaded.name, "Uploaded in dashboard"

    if runtime_path.exists():
        return runtime_path.read_bytes(), runtime_path.name, "Last dashboard upload"

    if seed_path.exists():
        return seed_path.read_bytes(), seed_path.name, "Built-in starter file"

    return None, None, "Missing"


def signed_color(v):
    if pd.isna(v):
        return ""
    if v > 0:
        return "color: #16a34a; font-weight: 700;"
    if v < 0:
        return "color: #dc2626; font-weight: 700;"
    return "color: #6b7280;"


def is_krw_ticker(t):
    return t.endswith(".KS") or t.endswith(".KQ")


@st.cache_data(ttl=3300, show_spinner=False)
def fetch_snapshot(ticker: str):
    """
    Latest online market observation. Uses hourly Yahoo data where available,
    and falls back to daily data.

    Yahoo Finance is convenient but not an exchange-grade real-time feed;
    timestamps/prices can be delayed.
    """
    out = {
        "ticker": ticker,
        "price": np.nan,
        "market_time": None,
        "previous_close": np.nan,
        "change_from_prev_close": np.nan,
        "source_interval": None,
    }

    try:
        t = yf.Ticker(ticker)
        h = t.history(period="5d", interval="60m", auto_adjust=False, prepost=False)
        if h is not None and not h.empty and "Close" in h:
            s = h["Close"].dropna()
            if not s.empty:
                out["price"] = float(s.iloc[-1])
                ts = s.index[-1]
                try:
                    ts = ts.tz_convert(SEOUL)
                except Exception:
                    pass
                out["market_time"] = ts
                out["source_interval"] = "1h"
    except Exception:
        pass

    try:
        d = yf.Ticker(ticker).history(period="10d", interval="1d", auto_adjust=False)
        if d is not None and not d.empty and "Close" in d:
            ds = d["Close"].dropna()
            if pd.isna(out["price"]) and not ds.empty:
                out["price"] = float(ds.iloc[-1])
                ts = ds.index[-1]
                try:
                    ts = ts.tz_convert(SEOUL)
                except Exception:
                    pass
                out["market_time"] = ts
                out["source_interval"] = "1d"

            if len(ds) >= 2:
                # If the latest daily bar is today's same session, previous close is bar -2.
                # Otherwise the latest completed close is the reference.
                current_date = None
                if out["market_time"] is not None:
                    current_date = pd.Timestamp(out["market_time"]).date()
                daily_last_date = pd.Timestamp(ds.index[-1]).date()

                if current_date == daily_last_date and len(ds) >= 2:
                    prev = float(ds.iloc[-2])
                else:
                    prev = float(ds.iloc[-1])

                out["previous_close"] = prev
    except Exception:
        pass

    if pd.notna(out["price"]) and pd.notna(out["previous_close"]) and out["previous_close"] != 0:
        out["change_from_prev_close"] = out["price"] / out["previous_close"] - 1

    return out


@st.cache_data(ttl=3300, show_spinner=False)
def fetch_daily(ticker: str, start_date: str):
    try:
        d = yf.download(
            ticker,
            start=start_date,
            auto_adjust=False,
            progress=False,
            threads=False,
        )
        if d is None or d.empty:
            return pd.Series(dtype=float)
        c = d["Close"]
        if isinstance(c, pd.DataFrame):
            c = c.iloc[:, 0]
        c = c.astype(float).dropna()
        c.index = pd.to_datetime(c.index).tz_localize(None)
        return c
    except Exception:
        return pd.Series(dtype=float)


def valuation_snapshot_table(valuation):
    rows = []
    for s in valuation.get("snapshots", []):
        if s["nav"] is None:
            continue
        rows.append({"date": s["date"], "nav": float(s["nav"])})
    return pd.DataFrame(rows).sort_values("date") if rows else pd.DataFrame(columns=["date", "nav"])


def first_price_for_ticker(ticker, first_date, daily_series):
    if daily_series.empty:
        return np.nan
    s = daily_series.loc[daily_series.index >= pd.Timestamp(first_date)]
    if s.empty:
        return np.nan
    return float(s.iloc[0])


def build_benchmark_series(start_date, end_date, snapshots):
    # Stable monitoring proxy for KOSPI200: KODEX 200 returns.
    k = fetch_daily("069500.KS", start_date.strftime("%Y-%m-%d"))
    sp = fetch_daily("^GSPC", start_date.strftime("%Y-%m-%d"))
    fx = fetch_daily("KRW=X", start_date.strftime("%Y-%m-%d"))

    idx = pd.date_range(start_date, end_date, freq="D")

    def align(s):
        if s.empty:
            return pd.Series(index=idx, dtype=float)
        return s.reindex(s.index.union(idx)).sort_index().ffill().reindex(idx)

    k = align(k)
    sp = align(sp)
    fx = align(fx)

    kre = k.pct_change().fillna(0)
    spre = sp.pct_change().fillna(0)
    fxre = fx.pct_change().fillna(0)

    hedged = spre
    unhedged = (1 + spre) * (1 + fxre) - 1

    comp = pd.DataFrame(
        {
            "KOSPI200 proxy": kre,
            "S&P500 Hedged": hedged,
            "S&P500 Unhedged": unhedged,
        },
        index=idx,
    )

    sleeves = {
        "KOSPI200 proxy": 40.0,
        "S&P500 Hedged": 30.0,
        "S&P500 Unhedged": 30.0,
    }
    rows = []
    for dt, r in comp.iterrows():
        for name in sleeves:
            sleeves[name] *= 1 + float(r[name])
        total = sum(sleeves.values())
        rows.append({"date": dt, "Benchmark": total, **sleeves})
        if dt.weekday() == 4:
            sleeves = {
                "KOSPI200 proxy": total * 0.40,
                "S&P500 Hedged": total * 0.30,
                "S&P500 Unhedged": total * 0.30,
            }

    bench = pd.DataFrame(rows).set_index("date")
    comp_index = (1 + comp).cumprod() * 100
    return bench, comp_index


st.title("DBA4014 Paper Trade Dashboard")

with st.sidebar:
    st.header("Weekly workbook uploads")
    st.caption(
        "Friday: upload the latest Orders workbook.\n\n"
        "Monday: upload the latest Valuation workbook.\n\n"
        "Each workbook already contains the earlier weekly tabs, so only the newest copy is needed."
    )

    order_upload = st.file_uploader(
        "Friday Order workbook",
        type=["xlsx"],
        key="order_upload",
    )
    valuation_upload = st.file_uploader(
        "Monday Valuation workbook",
        type=["xlsx"],
        key="valuation_upload",
    )

    st.divider()
    st.write("**Market refresh:** every 1 hour while this page is open")
    st.caption(
        "Hourly is a good compromise for this 6-week project. "
        "More frequent polling adds little value and increases Yahoo request volume."
    )

order_bytes, order_name, order_source = read_file_bytes(
    order_upload, RUNTIME_ORDER, SEED_ORDER
)
valuation_bytes, valuation_name, valuation_source = read_file_bytes(
    valuation_upload, RUNTIME_VALUATION, SEED_VALUATION
)

if order_bytes is None or valuation_bytes is None:
    st.error("Upload both the latest Order workbook and Valuation workbook.")
    st.stop()

try:
    order = parse_order_workbook(order_bytes, order_name or "orders.xlsx")
    valuation = parse_valuation_workbook(
        valuation_bytes, valuation_name or "valuation.xlsx"
    )
except Exception as e:
    st.error(
        "I could not read the uploaded course workbook structure. "
        "Please make sure you uploaded the original DBA4014 Excel template."
    )
    with st.expander("Technical detail"):
        st.exception(e)
    st.stop()

if valuation.get("latest") is None:
    st.error("No populated valuation sheet was detected.")
    st.stop()

current_state, base_cash, state_mode = current_position_state(valuation, order)
first_dates = first_acquisition_dates(valuation, order)
adjustments = latest_adjustments(valuation, order)

latest_val = valuation["latest"]
latest_order_date = order.get("latest_order_date")

u1, u2, u3 = st.columns(3)
u1.metric("Latest valuation", f"{latest_val['date']:%Y-%m-%d}")
u2.metric(
    "Latest order",
    "-" if latest_order_date is None else f"{latest_order_date:%Y-%m-%d}",
)
u3.metric("Loaded positions", len([x for x in current_state.values() if x["signed_quantity"] != 0]))

st.caption(
    f"Order file: **{order_name}** ({order_source}) • "
    f"Valuation file: **{valuation_name}** ({valuation_source})"
)
st.info(state_mode)

# Market-sensitive area refreshes automatically every hour.
@st.fragment(run_every="1h")
def market_dashboard():
    refresh_time = datetime.now(SEOUL)
    st.caption(
        f"Online market snapshot refreshed: {refresh_time:%Y-%m-%d %H:%M KST}. "
        "Yahoo Finance data may be delayed; this is a monitoring feed, not an official exchange feed."
    )

    # Pull current prices for all current positions + benchmark/FX.
    current_tickers = [
        t for t, p in current_state.items() if p["signed_quantity"] != 0
    ]
    required = sorted(set(current_tickers + ["KRW=X", "069500.KS", "^GSPC"]))
    market = {t: fetch_snapshot(t) for t in required}

    fx = market.get("KRW=X", {}).get("price", np.nan)
    if pd.isna(fx):
        # fall back to latest valuation FX from a US holding
        fx_candidates = [
            p.get("valuation_fx")
            for p in current_state.values()
            if not is_krw_ticker(p["ticker"]) and pd.notna(p.get("valuation_fx"))
        ]
        fx = float(fx_candidates[0]) if fx_candidates else 1.0

    # If a newer order exists after the latest valuation, estimate trade cashflow using current market prices.
    cash = float(base_cash or 0.0)
    orders_df = order.get("orders", pd.DataFrame())
    if not orders_df.empty:
        pending = orders_df[orders_df["date"] > latest_val["date"]]
        for _, r in pending.iterrows():
            snap = market.get(r["ticker"], {})
            px = snap.get("price", np.nan)
            if pd.isna(px):
                continue
            unit = px if is_krw_ticker(r["ticker"]) else px * fx
            value = float(r["quantity"]) * unit
            cash += value if r["action"] == "SELL" else -value

    # Current position table + NAV
    rows = []
    total_mv = 0.0

    for ticker, pos in sorted(current_state.items()):
        q = float(pos["signed_quantity"])
        if q == 0:
            continue

        snap = market.get(ticker, {})
        px = snap.get("price", np.nan)
        if pd.isna(px):
            continue

        unit_krw = px if is_krw_ticker(ticker) else px * fx
        mv = q * unit_krw
        total_mv += mv

        val_px = pd.to_numeric(pos.get("valuation_price"), errors="coerce")
        since_val = px / val_px - 1 if pd.notna(val_px) and val_px != 0 else np.nan

        ts = snap.get("market_time")
        if ts is not None:
            try:
                ts_txt = pd.Timestamp(ts).strftime("%Y-%m-%d %H:%M")
            except Exception:
                ts_txt = str(ts)
        else:
            ts_txt = "-"

        rows.append(
            {
                "자산": ticker,
                "포지션": "Short" if q < 0 else "Long",
                "최초 매수일": (
                    "-"
                    if ticker not in first_dates
                    else f"{pd.Timestamp(first_dates[ticker]):%Y-%m-%d}"
                ),
                "최근 조정": adjustments.get(ticker, "-"),
                "수량": abs(q),
                "온라인 가격": px,
                "가격 시각": ts_txt,
                "이전 종가 대비": snap.get("change_from_prev_close", np.nan),
                "최근 Valuation 가격 대비": since_val,
                "원화 평가액": mv,
            }
        )

    current_nav = cash + total_mv

    # Current benchmark
    today = pd.Timestamp(refresh_time.date())
    bench, comp_idx = build_benchmark_series(PROJECT_START, today, valuation["snapshots"])
    bench_now = float(bench["Benchmark"].iloc[-1]) if not bench.empty else np.nan
    bench_return = bench_now / 100 - 1 if pd.notna(bench_now) else np.nan
    port_return = current_nav / STARTING_CAPITAL - 1
    excess = port_return - bench_return if pd.notna(bench_return) else np.nan

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Current estimated NAV", f"₩{current_nav:,.0f}", f"{port_return:+.2%}")
    c2.metric("Professor benchmark", f"{bench_return:+.2%}" if pd.notna(bench_return) else "-")
    c3.metric("Excess return", f"{excess:+.2%}" if pd.notna(excess) else "-")
    c4.metric("KRW cash", f"₩{cash:,.0f}", f"{cash/current_nav:.2%}" if current_nav else "-")
    c5.metric("USD/KRW online", f"{fx:,.2f}")

    st.subheader("1. Current portfolio status")
    ptable = pd.DataFrame(rows)
    if not ptable.empty:
        ptable["현재 비중"] = ptable["원화 평가액"] / current_nav

        # Cash belongs in the same table.
        cash_row = pd.DataFrame(
            [
                {
                    "자산": "KRW Cash",
                    "포지션": "Cash",
                    "최초 매수일": f"{PROJECT_START:%Y-%m-%d}",
                    "최근 조정": f"{latest_val['date']:%Y-%m-%d} • Valuation cash balance",
                    "수량": np.nan,
                    "온라인 가격": np.nan,
                    "가격 시각": f"{refresh_time:%Y-%m-%d %H:%M}",
                    "이전 종가 대비": np.nan,
                    "최근 Valuation 가격 대비": np.nan,
                    "원화 평가액": cash,
                    "현재 비중": cash / current_nav if current_nav else np.nan,
                }
            ]
        )
        ptable = pd.concat([ptable, cash_row], ignore_index=True)

        style = (
            ptable.style
            .format(
                {
                    "수량": lambda x: "" if pd.isna(x) else f"{x:,.0f}",
                    "온라인 가격": lambda x: "" if pd.isna(x) else f"{x:,.2f}",
                    "이전 종가 대비": lambda x: "" if pd.isna(x) else f"{x:+.2%}",
                    "최근 Valuation 가격 대비": lambda x: "" if pd.isna(x) else f"{x:+.2%}",
                    "원화 평가액": "₩{:,.0f}",
                    "현재 비중": "{:.2%}",
                }
            )
            .map(signed_color, subset=["이전 종가 대비", "최근 Valuation 가격 대비"])
        )
        st.dataframe(style, use_container_width=True, hide_index=True)

    st.subheader("2. Portfolio trend vs professor benchmark")
    val_series = valuation_snapshot_table(valuation)

    fig = go.Figure()
    if not val_series.empty:
        pidx = val_series.copy()
        pidx["index"] = pidx["nav"] / STARTING_CAPITAL * 100
        fig.add_trace(
            go.Scatter(
                x=pidx["date"],
                y=pidx["index"],
                mode="lines+markers",
                name="Official valuation snapshots",
                line=dict(width=3),
            )
        )

        # Add the current online NAV as the most recent point.
        fig.add_trace(
            go.Scatter(
                x=[refresh_time],
                y=[current_nav / STARTING_CAPITAL * 100],
                mode="markers",
                name="Current online estimate",
                marker=dict(size=11),
            )
        )

    if not bench.empty:
        fig.add_trace(
            go.Scatter(
                x=bench.index,
                y=bench["Benchmark"],
                mode="lines",
                name="Professor benchmark",
                line=dict(width=3),
            )
        )

    fig.add_hline(y=100, line_dash="dot")
    fig.update_layout(
        height=460,
        yaxis_title="Index (start = 100)",
        xaxis_title="Date",
        hovermode="x unified",
        legend=dict(orientation="h"),
        margin=dict(l=10, r=10, t=20, b=10),
    )
    st.plotly_chart(fig, use_container_width=True)

    st.subheader("3. ETF trends — including exited positions")
    all_tickers = set()
    for snap in valuation["snapshots"]:
        all_tickers.update(p["ticker"] for p in snap["positions"])
    trades_df = valuation.get("trades", pd.DataFrame())
    if not trades_df.empty:
        all_tickers.update(trades_df["ticker"])
    orders_all = order.get("orders", pd.DataFrame())
    if not orders_all.empty:
        all_tickers.update(orders_all["ticker"])

    fig_etf = go.Figure()
    for ticker in sorted(all_tickers):
        first_dt = first_dates.get(ticker, PROJECT_START)
        s = fetch_daily(ticker, pd.Timestamp(first_dt).strftime("%Y-%m-%d"))
        if s.empty:
            continue
        s = s.loc[s.index >= pd.Timestamp(first_dt)]
        if s.empty:
            continue
        normalized = s / float(s.iloc[0]) * 100

        # Append latest hourly point if newer/different.
        snap = market.get(ticker) or fetch_snapshot(ticker)
        lp = snap.get("price", np.nan)
        lts = snap.get("market_time")
        if pd.notna(lp) and lts is not None:
            try:
                normalized.loc[pd.Timestamp(lts).tz_localize(None)] = lp / float(s.iloc[0]) * 100
            except Exception:
                pass

        fig_etf.add_trace(
            go.Scatter(x=normalized.index, y=normalized, mode="lines", name=ticker)
        )

    fig_etf.add_hline(y=100, line_dash="dot")
    fig_etf.update_layout(
        height=540,
        yaxis_title="Price index (first acquisition = 100)",
        xaxis_title="Date",
        hovermode="x unified",
        legend=dict(orientation="h"),
        margin=dict(l=10, r=10, t=20, b=10),
    )
    st.plotly_chart(fig_etf, use_container_width=True)

    st.subheader("4. Professor benchmark components")
    fig_b = go.Figure()
    if not comp_idx.empty:
        for col in comp_idx.columns:
            fig_b.add_trace(
                go.Scatter(x=comp_idx.index, y=comp_idx[col], mode="lines", name=col)
            )
    if not bench.empty:
        fig_b.add_trace(
            go.Scatter(
                x=bench.index,
                y=bench["Benchmark"],
                mode="lines",
                name="Combined benchmark",
                line=dict(width=4),
            )
        )
    fig_b.add_hline(y=100, line_dash="dot")
    fig_b.update_layout(
        height=480,
        yaxis_title="Index (start = 100)",
        xaxis_title="Date",
        hovermode="x unified",
        legend=dict(orientation="h"),
        margin=dict(l=10, r=10, t=20, b=10),
    )
    st.plotly_chart(fig_b, use_container_width=True)

    st.caption(
        "Benchmark monitoring uses KODEX 200 returns as a fixed proxy for the 40% KOSPI200 sleeve, "
        "plus S&P500 hedged 30% and S&P500 unhedged 30%, reset to 40/30/30 after Friday closes. "
        "Using one fixed KOSPI proxy avoids the source-switching errors from earlier versions."
    )

    st.subheader("5. Transaction history")
    trades = valuation.get("trades", pd.DataFrame()).copy()
    if trades.empty:
        st.info("No completed trades found in the uploaded valuation workbook.")
    else:
        shown = trades[
            ["date", "ticker", "name", "action", "quantity", "recorded_price", "recorded_fx"]
        ].copy()
        shown["date"] = pd.to_datetime(shown["date"]).dt.strftime("%Y-%m-%d")
        shown = shown.rename(
            columns={
                "date": "거래일",
                "ticker": "ETF",
                "name": "종목명",
                "action": "매수/매도",
                "quantity": "수량",
                "recorded_price": "Valuation 기록가격",
                "recorded_fx": "Valuation FX",
            }
        )
        st.dataframe(
            shown.style.format(
                {
                    "수량": "{:,.0f}",
                    "Valuation 기록가격": lambda x: "" if pd.isna(x) else f"{x:,.2f}",
                    "Valuation FX": lambda x: "" if pd.isna(x) else f"{x:,.2f}",
                }
            ),
            use_container_width=True,
            hide_index=True,
        )


market_dashboard()

st.caption(
    "Hourly refresh occurs while the page is open. If the app is sleeping or nobody has it open, "
    "Streamlit will not continuously run in the background. Reopening the page immediately fetches "
    "a fresh snapshot (subject to the hourly cache)."
)
