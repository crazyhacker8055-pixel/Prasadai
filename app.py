from __future__ import annotations

import gzip
import io
import json
import math
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from functools import lru_cache
from urllib.parse import quote

import numpy as np
import pandas as pd
import requests
import streamlit as st

# ============================================================
# StockPilot AI V8 Mobile
# One-file Streamlit app: GitHub + Streamlit Cloud + Upstox V3
# ============================================================

APP_NAME = "StockPilot AI"
APP_VERSION = "V8 Mobile"
UPSTOX_BASE = "https://api.upstox.com/v3"
MASTER_URL = "https://assets.upstox.com/market-quote/instruments/exchange/complete.json.gz"
HISTORY_DAYS = 500
QUOTE_BATCH = 500
DEFAULT_CANDIDATES = 180
MAX_WORKERS = 8

st.set_page_config(
    page_title=APP_NAME,
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ---------- Mobile-first styling ----------
st.markdown(
    """
<style>
.block-container {padding: .75rem .75rem 4.5rem .75rem; max-width: 1500px;}
h1 {font-size: 2rem !important; margin-bottom: .15rem !important;}
h2 {font-size: 1.35rem !important;}
h3 {font-size: 1.05rem !important;}
[data-testid="stMetric"] {padding: .65rem .7rem; border: 1px solid rgba(120,150,190,.18);
    border-radius: 12px; background: rgba(10,25,45,.55);}
[data-testid="stMetricValue"] {font-size: 1.25rem !important;}
div.stButton > button {border-radius: 11px; min-height: 2.7rem; font-weight: 650;}
div[data-testid="stDataFrame"] {border-radius: 12px;}
.mobile-card {padding: .8rem; border: 1px solid rgba(120,150,190,.20);
    border-radius: 14px; background: rgba(10,25,45,.55); margin: .45rem 0;}
.pill {display:inline-block; padding:.18rem .5rem; border-radius:999px;
    font-size:.76rem; font-weight:700; margin:.12rem .12rem .12rem 0;}
.green {background:#0b5d3b;color:#8ff0bf}.blue {background:#123f75;color:#8bc5ff}
.yellow {background:#66520b;color:#ffe78a}.red {background:#6b2028;color:#ff9ca5}
.muted {color:#9aa9bb;font-size:.82rem;}
@media (max-width: 700px) {
  .block-container {padding: .55rem .5rem 5rem .5rem;}
  h1 {font-size: 1.55rem !important;}
  h2 {font-size: 1.2rem !important;}
  [data-testid="stHorizontalBlock"] {gap: .35rem;}
  [data-testid="stMetricValue"] {font-size: 1.05rem !important;}
  .mobile-card {padding:.7rem;}
}
</style>
""",
    unsafe_allow_html=True,
)

# ---------- Secrets / HTTP ----------
def get_token() -> str | None:
    for key in ("UPSTOX_ACCESS_TOKEN", "UPSTOX_TOKEN"):
        try:
            value = st.secrets.get(key)
            if value:
                return str(value).strip()
        except Exception:
            pass
    return os.getenv("UPSTOX_ACCESS_TOKEN") or os.getenv("UPSTOX_TOKEN")


class UpstoxClient:
    def __init__(self, token: str):
        self.token = token
        self.headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        }

    def get(self, path: str, params: dict | None = None) -> dict:
        r = requests.get(
            UPSTOX_BASE + path,
            headers=self.headers,
            params=params,
            timeout=35,
        )
        if r.status_code >= 400:
            raise RuntimeError(f"Upstox HTTP {r.status_code}: {r.text[:300]}")
        payload = r.json()
        if payload.get("status") not in (None, "success"):
            raise RuntimeError(str(payload)[:500])
        return payload

    @staticmethod
    @st.cache_data(ttl=3600, show_spinner=False)
    def instrument_master_cached(_token: str) -> pd.DataFrame:
        r = requests.get(MASTER_URL, timeout=60)
        r.raise_for_status()
        raw = gzip.decompress(r.content)
        df = pd.DataFrame(json.loads(raw.decode("utf-8")))
        return df

    def instrument_master(self) -> pd.DataFrame:
        return self.instrument_master_cached(self.token)

    @lru_cache(maxsize=4096)
    def historical_daily(self, instrument_key: str) -> pd.DataFrame:
        end = date.today()
        start = end - timedelta(days=HISTORY_DAYS)
        key = quote(instrument_key, safe="")
        payload = self.get(
            f"/historical-candle/{key}/days/1/{end:%Y-%m-%d}/{start:%Y-%m-%d}"
        )
        rows = payload.get("data", {}).get("candles", [])
        data = [r[:7] for r in rows if isinstance(r, list) and len(r) >= 6]
        cols = ["timestamp", "open", "high", "low", "close", "volume", "open_interest"]
        df = pd.DataFrame(data, columns=cols)
        if df.empty:
            return df
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
        for c in cols[1:]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        return (
            df.dropna(subset=["timestamp", "close"])
            .sort_values("timestamp")
            .drop_duplicates("timestamp")
            .reset_index(drop=True)
        )

    def full_quotes(self, keys: list[str]) -> dict:
        """Fetch V3 full quotes and index them by every useful identifier.

        Important: Upstox V3 returns the `data` object keyed as
        `NSE_EQ:TRADING_SYMBOL`, while the instrument master uses
        `NSE_EQ|ISIN` as `instrument_key`.  The previous implementation
        looked up only the instrument-master key, so every NSE equity quote
        was missed and the scanner produced zero candidates.
        """
        result = {}
        for i in range(0, len(keys), QUOTE_BATCH):
            batch = keys[i:i + QUOTE_BATCH]
            payload = self.get(
                "/market-quote/quotes",
                {"instrument_key": ",".join(batch)},
            )
            data = payload.get("data", {}) or {}
            for response_key, item in data.items():
                if not isinstance(item, dict):
                    continue
                # Keep the API response key.
                result[str(response_key)] = item
                # Also index by the instrument key returned by Upstox.
                instrument_token = item.get("instrument_token")
                if instrument_token:
                    result[str(instrument_token)] = item
                # And by the normalized exchange:symbol form.
                symbol = item.get("symbol")
                if symbol:
                    result[f"NSE_EQ:{symbol}"] = item
        return result

    def ohlc(self, keys: list[str]) -> dict:
        payload = self.get(
            "/market-quote/ohlc",
            {"instrument_key": ",".join(keys), "interval": "1d"},
        )
        return payload.get("data", {})

    @staticmethod
    def quote_item(quotes: dict, key: str) -> dict:
        return quotes.get(key) or quotes.get(key.replace("|", ":")) or {}

    @staticmethod
    def quote_fields(item: dict):
        ohlc = item.get("live_ohlc") or item.get("ohlc") or {}
        price = item.get("last_price", ohlc.get("close"))
        volume = item.get("volume", ohlc.get("volume"))
        prev = item.get("prev_ohlc") or {}
        prev_close = item.get("prev_close_price", prev.get("close"))
        return (
            float(price) if price is not None else None,
            float(volume) if volume is not None else None,
            float(prev_close) if prev_close is not None else None,
        )


# ---------- Technical indicators ----------
def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    x = df.copy()
    x["ema20"] = x.close.ewm(span=20, adjust=False).mean()
    x["sma50"] = x.close.rolling(50).mean()
    x["sma150"] = x.close.rolling(150).mean()
    x["ema220"] = x.close.ewm(span=220, adjust=False).mean()
    x["high20"] = x.high.rolling(20).max()
    x["low20"] = x.low.rolling(20).min()
    x["high52"] = x.high.rolling(252, min_periods=200).max()
    x["low52"] = x.low.rolling(252, min_periods=200).min()
    x["avgvol10"] = x.volume.rolling(10).mean()
    x["avgvol20"] = x.volume.rolling(20).mean()

    prev = x.close.shift(1)
    tr = pd.concat(
        [(x.high - x.low), (x.high - prev).abs(), (x.low - prev).abs()],
        axis=1,
    ).max(axis=1)
    x["atr14"] = tr.rolling(14).mean()
    x["atr20"] = tr.rolling(20).mean()
    x["atr_ratio"] = x.atr14 / x.atr20.replace(0, np.nan)

    x["range5_pct"] = (
        (x.high.rolling(5).max() - x.low.rolling(5).min())
        / x.close.replace(0, np.nan) * 100
    )
    x["range7_pct"] = (
        (x.high.rolling(7).max() - x.low.rolling(7).min())
        / x.close.replace(0, np.nan) * 100
    )
    x["bar_range_pct"] = (x.high - x.low) / x.close.replace(0, np.nan) * 100
    x["upper20_pct"] = (
        (x.close - x.low20)
        / (x.high20 - x.low20).replace(0, np.nan) * 100
    )

    delta = x.close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    x["rsi14"] = 100 - (100 / (1 + rs))

    x["nr7"] = x.bar_range_pct <= x.bar_range_pct.rolling(7).min()
    x["inside_bar"] = (x.high < x.high.shift(1)) & (x.low > x.low.shift(1))
    return x


def completed_daily(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    # During market hours, today's daily candle is incomplete. We deliberately
    # exclude it so confirmation is based on the last completed session.
    today = pd.Timestamp.now(tz="Asia/Kolkata").date()
    dates = df.timestamp.dt.date
    out = df[dates < today].copy()
    return out if not out.empty else df.iloc[:-1].copy()


# ---------- Universe / market ----------
def build_universe(client: UpstoxClient) -> pd.DataFrame:
    m = client.instrument_master().copy()
    if "segment" in m.columns:
        m = m[m.segment.astype(str).eq("NSE_EQ")]
    if "instrument_type" in m.columns:
        m = m[m.instrument_type.astype(str).eq("EQ")]
    if "trading_symbol" in m.columns:
        m = m[m.trading_symbol.notna()]
    return m.drop_duplicates("instrument_key").reset_index(drop=True)


def market_snapshot(client: UpstoxClient) -> dict:
    specs = {
        "NIFTY 50": "NSE_INDEX|Nifty 50",
        "BANK NIFTY": "NSE_INDEX|Nifty Bank",
        "INDIA VIX": "NSE_INDEX|India VIX",
    }
    raw = client.ohlc(list(specs.values()))
    out = {}
    for label, key in specs.items():
        item = client.quote_item(raw, key)
        p, _, pc = client.quote_fields(item)
        out[label] = {
            "price": p,
            "change_pct": ((p - pc) / pc * 100) if p is not None and pc else None,
        }
    return out


def live_prefilter(client: UpstoxClient, universe: pd.DataFrame, limit: int):
    keys = universe.instrument_key.astype(str).tolist()
    quotes = client.full_quotes(keys)
    candidates = []
    matched_quotes = 0

    for row in universe.itertuples(index=False):
        key = str(row.instrument_key)
        q = client.quote_item(quotes, key)
        # V3 equity quotes are normally keyed by NSE_EQ:SYMBOL, not
        # NSE_EQ|ISIN. Fall back to the trading symbol explicitly.
        if not q:
            symbol = str(getattr(row, "trading_symbol", ""))
            if symbol:
                q = client.quote_item(quotes, f"NSE_EQ:{symbol}")
        if q:
            matched_quotes += 1
        price, volume, _ = client.quote_fields(q)
        if price is None:
            continue

        year_high = q.get("year_high")
        year_low = q.get("year_low")
        if year_low not in (None, 0):
            try:
                if price <= 1.15 * float(year_low):
                    continue
            except Exception:
                pass

        near_high = 0.0
        if year_high not in (None, 0):
            try:
                near_high = price / float(year_high)
            except Exception:
                pass

        candidates.append({
            "row": row,
            "quote": q,
            "near_high": near_high,
            "volume": volume or 0,
        })

    candidates.sort(key=lambda z: (z["near_high"], z["volume"]), reverse=True)
    return candidates[:limit]


# ---------- Scanner ----------
def evaluate_stock(client: UpstoxClient, row, quote: dict) -> dict:
    key = str(row.instrument_key)
    symbol = str(getattr(row, "trading_symbol", key))

    try:
        raw = client.historical_daily(key)
        c = completed_daily(add_indicators(raw))
        if len(c) < 253:
            return {
                "symbol": symbol, "instrument_key": key,
                "status": "INSUFFICIENT DATA", "score": 0,
            }

        last = c.iloc[-1]
        prior = c.iloc[:-1]
        live, live_vol, _ = client.quote_fields(quote)
        if live is None:
            live = float(last.close)
        if live_vol is None:
            live_vol = float(last.volume)

        prior20 = float(prior.tail(20).high.max())
        prior52 = float(prior.tail(252).high.max())
        breakout = max(prior20, prior52)
        distance = (live / breakout - 1) * 100 if breakout else math.nan

        swing_low = float(c.tail(10).low.min())
        stop = min(swing_low * 0.99, live * 0.95)
        if stop <= 0 or stop >= live:
            stop = live * 0.95
        risk = live - stop

        volume_contract = (
            bool(last.avgvol10 <= last.avgvol20 * 0.85)
            if pd.notna(last.avgvol10) and pd.notna(last.avgvol20) and last.avgvol20
            else False
        )

        checks = {
            "Close > EMA20": bool(last.close > last.ema20),
            "EMA20 > SMA50": bool(last.ema20 > last.sma50),
            "SMA50 > SMA150": bool(last.sma50 > last.sma150),
            "SMA150 > EMA220": bool(last.sma150 > last.ema220),
            "5D range <= 4%": bool(last.range5_pct <= 4),
            "7D range <= 5%": bool(last.range7_pct <= 5),
            "ATR14/ATR20 <= 0.80": bool(last.atr_ratio <= 0.80),
            "Volume contraction <= 0.85x": volume_contract,
            "Near 20D resistance": bool(live >= prior20 * 0.95),
            "Upper 20D range >= 70%": bool(last.upper20_pct >= 70),
            "NR7": bool(last.nr7),
            "Inside bar": bool(last.inside_bar),
            "Near 52W high": bool(live >= prior52 * 0.92),
        }

        # Breakout confirmation is based on the last completed daily close,
        # not the current unfinished candle.
        prior_breakout_level = float(prior.iloc[:-1].tail(252).high.max())
        confirmed_close = float(last.close) > prior_breakout_level
        breakout_volume = bool(
            pd.notna(last.avgvol20)
            and last.avgvol20
            and last.volume >= 1.5 * last.avgvol20
        )
        confirmed = confirmed_close and breakout_volume

        weights = {
            "Close > EMA20": 12,
            "EMA20 > SMA50": 10,
            "SMA50 > SMA150": 8,
            "SMA150 > EMA220": 8,
            "5D range <= 4%": 12,
            "7D range <= 5%": 8,
            "ATR14/ATR20 <= 0.80": 10,
            "Volume contraction <= 0.85x": 8,
            "Near 20D resistance": 7,
            "Upper 20D range >= 70%": 5,
            "NR7": 4,
            "Inside bar": 4,
            "Near 52W high": 4,
        }
        score = min(
            100,
            int(sum(weights[k] for k, v in checks.items() if v) + (10 if confirmed else 0)),
        )

        if confirmed and score >= 75:
            status = "BREAKOUT"
        elif score >= 75 and distance <= 3:
            status = "A+ PRE-BREAKOUT"
        elif score >= 65:
            status = "PRE-BREAKOUT"
        elif score >= 55:
            status = "WATCH"
        else:
            status = "NEAR MISS"

        if confirmed:
            setup = "52W / 20D BREAKOUT"
        elif checks["NR7"] and checks["Inside bar"]:
            setup = "NR7 + INSIDE BAR"
        elif (
            checks["5D range <= 4%"]
            and checks["7D range <= 5%"]
            and checks["ATR14/ATR20 <= 0.80"]
        ):
            setup = "VCP / TIGHT COMPRESSION"
        elif checks["Near 20D resistance"]:
            setup = "RESISTANCE PRESSURE"
        else:
            setup = "TREND SETUP"

        return {
            "symbol": symbol,
            "instrument_key": key,
            "status": status,
            "setup": setup,
            "score": score,
            "live_price": float(live),
            "breakout_level": float(breakout),
            "distance_pct": float(distance),
            "range5_pct": float(last.range5_pct),
            "range7_pct": float(last.range7_pct),
            "atr_ratio": float(last.atr_ratio),
            "volume_ratio": float(live_vol / last.avgvol20) if pd.notna(last.avgvol20) and last.avgvol20 else math.nan,
            "rsi14": float(last.rsi14),
            "stop": float(stop),
            "target1": float(live + 2 * risk),
            "target2": float(live + 3 * risk),
            "confirmed_close_breakout": bool(confirmed),
            "checks": checks,
            "signal_date": str(last.timestamp.date()),
        }

    except Exception as exc:
        return {
            "symbol": symbol,
            "instrument_key": key,
            "status": "ERROR",
            "score": 0,
            "reason": str(exc)[:180],
        }


def run_scan(client: UpstoxClient, universe: pd.DataFrame, candidate_limit: int):
    candidates = live_prefilter(client, universe, candidate_limit)
    if not candidates:
        return pd.DataFrame()

    results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [
            pool.submit(evaluate_stock, client, c["row"], c["quote"])
            for c in candidates
        ]
        for f in as_completed(futures):
            results.append(f.result())

    df = pd.DataFrame(results)
    if df.empty:
        return df

    order = {
        "BREAKOUT": 0,
        "A+ PRE-BREAKOUT": 1,
        "PRE-BREAKOUT": 2,
        "WATCH": 3,
        "NEAR MISS": 4,
        "INSUFFICIENT DATA": 5,
        "ERROR": 6,
    }
    df["_order"] = df.status.map(order).fillna(99)
    return (
        df.sort_values(["_order", "score", "distance_pct"], ascending=[True, False, False])
        .drop(columns="_order")
        .reset_index(drop=True)
    )


def display_table(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    cols = [
        "symbol", "status", "setup", "score", "live_price", "breakout_level",
        "distance_pct", "volume_ratio", "rsi14", "stop", "target1", "target2",
    ]
    out = df[[c for c in cols if c in df.columns]].copy()
    return out.rename(columns={
        "symbol": "Stock", "status": "Status", "setup": "Setup",
        "score": "Score", "live_price": "Live ₹", "breakout_level": "Breakout ₹",
        "distance_pct": "Distance %", "volume_ratio": "Vol Ratio",
        "rsi14": "RSI", "stop": "Stop ₹", "target1": "T1 ₹", "target2": "T2 ₹",
    }).round(2)


# ---------- SMC-style chart ----------
def build_chart(client: UpstoxClient, row):
    import plotly.graph_objects as go

    x = add_indicators(client.historical_daily(row.instrument_key).tail(220))
    fig = go.Figure()
    fig.add_trace(
        go.Candlestick(
            x=x.timestamp, open=x.open, high=x.high, low=x.low, close=x.close,
            name="Price",
        )
    )
    for col, name in [
        ("ema20", "EMA20"), ("sma50", "SMA50"),
        ("sma150", "SMA150"), ("ema220", "EMA220")
    ]:
        fig.add_trace(go.Scatter(x=x.timestamp, y=x[col], name=name))

    for label, level in [
        ("Breakout", row.breakout_level),
        ("Stop", row.stop),
        ("T1", row.target1),
        ("T2", row.target2),
    ]:
        fig.add_hline(
            y=float(level), line_dash="dot",
            annotation_text=label,
            annotation_position="top left",
        )
    fig.update_layout(
        template="plotly_dark",
        height=520,
        margin=dict(l=5, r=5, t=35, b=5),
        xaxis_rangeslider_visible=False,
        title=f"{row.symbol} • Price / SMC-style structure",
    )
    return fig


# ---------- UI ----------
TOKEN = get_token()

with st.sidebar:
    st.markdown(f"## 📈 {APP_NAME}")
    st.caption(f"{APP_VERSION} • NSE swing / BTST scanner")
    st.info("Live quotes + completed daily candles. No automatic orders.")
    st.caption("Use Streamlit Secrets for the Upstox token.")

st.title("📈 StockPilot AI")
st.caption("Pre-breakout • VCP • NR7 • breakout confirmation • live Upstox data")

if not TOKEN:
    st.error("Upstox token is not configured.")
    st.markdown(
        """
**For Streamlit Cloud**

1. Open your app → **Settings → Secrets**
2. Add:

```toml
UPSTOX_ACCESS_TOKEN = "YOUR_UPSTOX_ACCESS_TOKEN"
```

3. Save and reload the app.
        """
    )
    st.stop()

client = UpstoxClient(TOKEN)

if "scan_results" not in st.session_state:
    st.session_state.scan_results = pd.DataFrame()
if "scan_time" not in st.session_state:
    st.session_state.scan_time = None
if "scan_completed" not in st.session_state:
    st.session_state.scan_completed = False
if "universe" not in st.session_state:
    st.session_state.universe = None
if "market" not in st.session_state:
    st.session_state.market = {}

# Load universe once per session
try:
    if st.session_state.universe is None:
        with st.spinner("Loading NSE instrument universe..."):
            st.session_state.universe = build_universe(client)
except Exception as exc:
    st.error(f"Could not load Upstox instrument master: {exc}")
    st.stop()

universe = st.session_state.universe

# Market header
try:
    st.session_state.market = market_snapshot(client)
except Exception:
    pass

m1, m2, m3 = st.columns(3)
for col, label in zip((m1, m2, m3), ("NIFTY 50", "BANK NIFTY", "INDIA VIX")):
    item = st.session_state.market.get(label, {})
    p, ch = item.get("price"), item.get("change_pct")
    with col:
        st.metric(
            label,
            f"₹{p:,.2f}" if isinstance(p, (int, float)) else "N/A",
            f"{ch:+.2f}%" if isinstance(ch, (int, float)) else None,
        )

st.divider()

# Controls
c1, c2, c3 = st.columns([1.25, 1, 1])
with c1:
    scan_clicked = st.button("🔄 LIVE FULL SCAN", type="primary", use_container_width=True)
with c2:
    candidate_limit = st.selectbox(
        "Candidates", [100, 150, 180, 250], index=2, label_visibility="collapsed"
    )
with c3:
    refresh_clicked = st.button("♻️ Refresh", use_container_width=True)

if refresh_clicked:
    st.cache_data.clear()
    client.historical_daily.cache_clear()
    st.rerun()

if scan_clicked:
    with st.spinner(f"Scanning live quotes + {candidate_limit} best candidates..."):
        try:
            st.session_state.scan_results = run_scan(
                client, universe, int(candidate_limit)
            )
            st.session_state.scan_time = datetime.now()
            st.session_state.scan_completed = True
        except Exception as exc:
            st.error(f"Scan failed: {exc}")

if st.session_state.scan_time:
    st.caption(
        f"Last scan: {st.session_state.scan_time:%d-%m-%Y %H:%M:%S} • "
        f"Universe: {len(universe):,} NSE EQ • "
        f"Historical signal candle: completed session"
    )

df = st.session_state.scan_results

# Main mobile navigation
page = st.segmented_control(
    "View",
    ["Dashboard", "Pre-Breakout", "Breakouts", "Stock Detail", "Risk"],
    default="Dashboard",
    label_visibility="collapsed",
)

if df.empty:
    if st.session_state.scan_completed:
        st.warning(
            "Scan completed but no qualifying setup was returned. "
            "The next scan will show the live-quote match count and errors if any."
        )
    else:
        st.info("Tap **LIVE FULL SCAN** to populate current setups.")
else:
    if page == "Dashboard":
        counts = {
            "A+ SETUPS": int((df.status == "A+ PRE-BREAKOUT").sum()),
            "BREAKOUTS": int((df.status == "BREAKOUT").sum()),
            "WATCH": int((df.status == "WATCH").sum()),
            "NEAR MISS": int((df.status == "NEAR MISS").sum()),
        }
        k1, k2, k3, k4 = st.columns(4)
        for col, (name, value) in zip((k1, k2, k3, k4), counts.items()):
            col.metric(name, value)

        st.subheader("🔥 Top Setups")
        top = df[df.status.isin(["BREAKOUT", "A+ PRE-BREAKOUT", "PRE-BREAKOUT", "WATCH"])].head(10)
        for _, r in top.iterrows():
            status_class = (
                "green" if r.status in ("BREAKOUT", "A+ PRE-BREAKOUT")
                else "blue" if r.status == "PRE-BREAKOUT"
                else "yellow"
            )
            st.markdown(
                f"""
<div class="mobile-card">
<b style="font-size:1.08rem">{r.symbol}</b>
<span class="pill {status_class}">{r.status}</span>
<span class="pill blue">{r.setup}</span><br>
<span class="muted">Score</span> <b>{int(r.score)}/100</b>
&nbsp; • &nbsp; <span class="muted">Live</span> <b>₹{r.live_price:,.2f}</b>
&nbsp; • &nbsp; <span class="muted">Breakout</span> <b>₹{r.breakout_level:,.2f}</b><br>
<span class="muted">Distance</span> {r.distance_pct:+.2f}% &nbsp;•
<span class="muted">Vol</span> {r.volume_ratio:.2f}x &nbsp;•
<span class="muted">RSI</span> {r.rsi14:.1f}<br>
<span class="muted">SL</span> ₹{r.stop:,.2f} &nbsp;•
<span class="muted">T1</span> ₹{r.target1:,.2f} &nbsp;•
<span class="muted">T2</span> ₹{r.target2:,.2f}
</div>
""",
                unsafe_allow_html=True,
            )

        st.subheader("Ranked Scanner")
        st.dataframe(display_table(df), use_container_width=True, hide_index=True)

    elif page == "Pre-Breakout":
        st.subheader("🎯 Pre-Breakout Candidates")
        pre = df[df.status.isin(["A+ PRE-BREAKOUT", "PRE-BREAKOUT", "WATCH"])]
        if pre.empty:
            st.info("No pre-breakout candidates in this scan.")
        else:
            st.dataframe(display_table(pre), use_container_width=True, hide_index=True)

    elif page == "Breakouts":
        st.subheader("🚀 Confirmed Breakouts")
        st.caption("Confirmation uses the last completed daily close plus volume ≥ 1.5× 20-day average.")
        br = df[df.status == "BREAKOUT"]
        if br.empty:
            st.info("No confirmed breakouts in this scan.")
        else:
            st.dataframe(display_table(br), use_container_width=True, hide_index=True)

    elif page == "Stock Detail":
        symbols = df.symbol.tolist()
        symbol = st.selectbox("Stock", symbols)
        row = df[df.symbol == symbol].iloc[0]

        a, b, c, d = st.columns(4)
        a.metric("Score", f"{int(row.score)}/100")
        b.metric("Live", f"₹{row.live_price:,.2f}")
        c.metric("Stop", f"₹{row.stop:,.2f}")
        d.metric("T1", f"₹{row.target1:,.2f}")

        st.markdown(
            f"**{row.symbol}** · `{row.status}` · `{row.setup}` · "
            f"signal candle `{row.signal_date}`"
        )

        st.subheader("Strategy Checklist")
        checks = row.checks
        cc = st.columns(2)
        for i, (name, ok) in enumerate(checks.items()):
            cc[i % 2].write(("✅ " if ok else "❌ ") + name)

        try:
            st.plotly_chart(
                build_chart(client, row),
                use_container_width=True,
                config={"displayModeBar": False},
            )
        except Exception as exc:
            st.warning(f"Chart unavailable: {exc}")

    elif page == "Risk":
        st.subheader("💰 Risk Calculator")
        r1, r2 = st.columns(2)
        capital = r1.number_input("Capital (₹)", 1000.0, 1e8, 100000.0, 5000.0)
        risk_pct = r2.number_input("Risk / trade (%)", 0.1, 5.0, 1.0, 0.1)
        r3, r4 = st.columns(2)
        entry = r3.number_input("Entry (₹)", 0.05, 1e7, 100.0, 1.0)
        stop = r4.number_input("Stop (₹)", 0.01, 1e7, 95.0, 1.0)

        if stop >= entry:
            st.error("Stop must be below entry.")
        else:
            risk_amount = capital * risk_pct / 100
            per_share = entry - stop
            qty = int(risk_amount // per_share)
            st.metric("Risk Amount", f"₹{risk_amount:,.2f}")
            st.metric("Quantity", f"{qty:,}")
            st.metric("Capital Used", f"₹{qty * entry:,.2f}")

st.divider()
st.caption(
    "StockPilot AI V8 Mobile • Upstox V3 • NSE only • No automatic orders • "
    "Not financial advice"
)
