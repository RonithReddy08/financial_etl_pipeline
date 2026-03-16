"""
app.py — Equity Portfolio Analytics Dashboard
===============================================
Domain: S&P 500 equity portfolio — trades, P&L, sector allocation,
        analyst ratings, valuation metrics (P/E, market cap).

Data sources (in priority order):
  1. User uploads their own CSV / Excel file
  2. Alpha Vantage API — live stock quotes + fundamentals (needs free key)
  3. Built-in demo data — realistic simulated portfolio (always works)

Run: streamlit run app.py
"""

import sys, warnings, io, random, time
from pathlib import Path
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

warnings.filterwarnings("ignore")

# ─────────────────────────────────────────────────────────────
# PAGE CONFIG
# ─────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Equity Portfolio Analytics",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─────────────────────────────────────────────────────────────
# CSS
# ─────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@300;400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap');
html, body, [class*="css"]  { font-family: 'IBM Plex Sans', sans-serif; }
.block-container             { padding-top: 1.4rem; padding-bottom: 2rem; }

.kpi-card {
    background: #0d1117;
    border: 1px solid #21262d;
    border-top: 2px solid;
    border-radius: 8px;
    padding: 1.1rem 1.2rem 1rem;
    margin-bottom: 0.4rem;
}
.kpi-card.green  { border-top-color: #3fb950; }
.kpi-card.red    { border-top-color: #f85149; }
.kpi-card.blue   { border-top-color: #58a6ff; }
.kpi-card.amber  { border-top-color: #d29922; }
.kpi-card.purple { border-top-color: #bc8cff; }
.kpi-card.gray   { border-top-color: #484f58; }

.kpi-label { font-size:0.68rem; font-weight:500; letter-spacing:0.1em;
             text-transform:uppercase; color:#484f58; margin-bottom:0.25rem; }
.kpi-value { font-size:1.75rem; font-weight:600; color:#e6edf3;
             font-family:'IBM Plex Mono',monospace; line-height:1.1; }
.kpi-sub   { font-size:0.74rem; color:#6e7681; margin-top:0.25rem;
             font-family:'IBM Plex Mono',monospace; }
.kpi-sub.up   { color:#3fb950; }
.kpi-sub.down { color:#f85149; }

.section-title {
    font-size:0.65rem; font-weight:600; letter-spacing:0.14em;
    text-transform:uppercase; color:#484f58;
    margin:1.4rem 0 0.7rem; padding-bottom:0.35rem;
    border-bottom:1px solid #21262d;
}
.source-badge {
    display:inline-flex; align-items:center; gap:0.4rem;
    padding:0.25rem 0.7rem; border-radius:4px; font-size:0.72rem;
    font-weight:500; font-family:'IBM Plex Mono',monospace;
}
.source-live  { background:#0a2a1a; color:#3fb950; border:1px solid #1a4a2e; }
.source-demo  { background:#0d1a2e; color:#58a6ff; border:1px solid #1a3050; }
.source-csv   { background:#1a1200; color:#d29922; border:1px solid #3a2c00; }

.upload-hint {
    border:1px dashed #30363d; border-radius:8px;
    padding:1.2rem 1rem; text-align:center;
    background:#0d1117; color:#6e7681; font-size:0.82rem;
    line-height:1.7;
}
.trade-row-buy  { border-left:3px solid #3fb950; }
.trade-row-sell { border-left:3px solid #f85149; }
[data-testid="stSidebar"] { background:#0d1117; border-right:1px solid #21262d; }
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────
# PLOTLY DEFAULTS
# ─────────────────────────────────────────────────────────────
PT = dict(
    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="#0d1117",
    font=dict(family="IBM Plex Sans, sans-serif", color="#8b949e", size=11),
    xaxis=dict(gridcolor="#21262d", linecolor="#30363d", zerolinecolor="#30363d"),
    yaxis=dict(gridcolor="#21262d", linecolor="#30363d", zerolinecolor="#30363d"),
    colorway=["#58a6ff","#3fb950","#d29922","#f85149","#bc8cff","#79c0ff","#56d364"],
    margin=dict(l=8, r=8, t=36, b=8),
)
C = dict(
    green="#3fb950", red="#f85149", blue="#58a6ff",
    amber="#d29922", purple="#bc8cff", gray="#484f58",
)

SECTOR_COLORS = {
    "Technology":              "#58a6ff",
    "Financials":              "#3fb950",
    "Healthcare":              "#bc8cff",
    "Consumer Discretionary":  "#d29922",
    "Consumer Staples":        "#79c0ff",
    "Energy":                  "#f85149",
    "Industrials":             "#56d364",
    "Utilities":               "#e3b341",
    "Real Estate":             "#ffa657",
    "Materials":               "#ff7b72",
    "Communication Services":  "#a5d6ff",
}

# ─────────────────────────────────────────────────────────────
# DEMO DATA — S&P 500 equity portfolio
# ─────────────────────────────────────────────────────────────
TICKER_META = {
    "AAPL":  {"sector":"Technology",             "name":"Apple Inc.",          "pe":29.4, "mcap_bn":2850},
    "MSFT":  {"sector":"Technology",             "name":"Microsoft Corp.",      "pe":34.1, "mcap_bn":3100},
    "NVDA":  {"sector":"Technology",             "name":"NVIDIA Corp.",         "pe":65.2, "mcap_bn":2200},
    "GOOGL": {"sector":"Technology",             "name":"Alphabet Inc.",        "pe":22.8, "mcap_bn":2100},
    "META":  {"sector":"Technology",             "name":"Meta Platforms",       "pe":26.3, "mcap_bn":1350},
    "AMZN":  {"sector":"Consumer Discretionary", "name":"Amazon.com Inc.",      "pe":44.7, "mcap_bn":1950},
    "TSLA":  {"sector":"Consumer Discretionary", "name":"Tesla Inc.",           "pe":55.1, "mcap_bn": 780},
    "HD":    {"sector":"Consumer Discretionary", "name":"Home Depot Inc.",      "pe":22.1, "mcap_bn": 360},
    "JPM":   {"sector":"Financials",             "name":"JPMorgan Chase",       "pe":11.8, "mcap_bn": 580},
    "GS":    {"sector":"Financials",             "name":"Goldman Sachs",        "pe":12.4, "mcap_bn": 145},
    "BAC":   {"sector":"Financials",             "name":"Bank of America",      "pe":10.9, "mcap_bn": 320},
    "V":     {"sector":"Financials",             "name":"Visa Inc.",            "pe":30.2, "mcap_bn": 535},
    "JNJ":   {"sector":"Healthcare",             "name":"Johnson & Johnson",    "pe":15.6, "mcap_bn": 390},
    "UNH":   {"sector":"Healthcare",             "name":"UnitedHealth Group",   "pe":19.3, "mcap_bn": 460},
    "LLY":   {"sector":"Healthcare",             "name":"Eli Lilly & Co.",      "pe":58.4, "mcap_bn": 720},
    "XOM":   {"sector":"Energy",                 "name":"Exxon Mobil Corp.",    "pe": 13.2,"mcap_bn": 480},
    "CVX":   {"sector":"Energy",                 "name":"Chevron Corp.",        "pe": 12.7,"mcap_bn": 270},
    "WMT":   {"sector":"Consumer Staples",       "name":"Walmart Inc.",         "pe": 27.8,"mcap_bn": 680},
    "PG":    {"sector":"Consumer Staples",       "name":"Procter & Gamble",     "pe": 24.1,"mcap_bn": 370},
    "MA":    {"sector":"Financials",             "name":"Mastercard Inc.",      "pe": 33.6,"mcap_bn": 440},
}
TICKERS = list(TICKER_META.keys())
RATINGS = ["Strong Buy", "Buy", "Hold", "Underperform", "Sell"]
RATING_WEIGHT = [0.25, 0.35, 0.25, 0.1, 0.05]

def generate_portfolio(n: int = 500, seed: int = 42) -> pd.DataFrame:
    random.seed(seed); np.random.seed(seed)
    base_date = datetime(2024, 1, 1)
    price_anchors = {t: random.uniform(80, 900) for t in TICKERS}
    records = []
    for i in range(n):
        ticker  = random.choice(TICKERS)
        meta    = TICKER_META[ticker]
        anchor  = price_anchors[ticker]
        price   = round(anchor * (1 + np.random.normal(0, 0.18)), 2)
        price   = max(10, price)
        cost    = round(price * (1 + np.random.normal(0, 0.12)), 2)
        cost    = max(5, cost)
        shares  = random.randint(5, 300)
        action  = random.choices(["BUY","SELL"], weights=[0.72, 0.28])[0]
        date    = base_date + timedelta(days=random.randint(0, 364))
        rating  = random.choices(RATINGS, weights=RATING_WEIGHT)[0]
        pe_noise = round(meta["pe"] * (1 + np.random.normal(0, 0.08)), 1)
        mcap_noise = round(meta["mcap_bn"] * (1 + np.random.normal(0, 0.05)), 1)
        records.append({
            "date":               date.strftime("%Y-%m-%d"),
            "ticker":             ticker,
            "company":            meta["name"],
            "sector":             meta["sector"],
            "action":             action,
            "shares":             shares,
            "price_usd":          price,
            "cost_basis_usd":     cost,
            "analyst_rating":     rating,
            "pe_ratio":           pe_noise,
            "market_cap_bn":      mcap_noise,
            "notes":              random.choice(["", None, "earnings beat", "initiated coverage", "  ", "price target raised"]),
        })
    # inject ~3% nulls in price_usd for data quality demo
    null_idx = random.sample(range(n), k=max(1, int(n * 0.03)))
    for idx in null_idx:
        records[idx]["price_usd"] = None
    return pd.DataFrame(records)

# ─────────────────────────────────────────────────────────────
# TRANSFORM — clean + derive metrics
# ─────────────────────────────────────────────────────────────
def run_transform(raw: pd.DataFrame) -> tuple[pd.DataFrame, list[str], list[str]]:
    logs, warns = [], []
    df = raw.copy()

    def log(level, msg):
        ts = datetime.utcnow().strftime("%H:%M:%S")
        color = {"info":"#58a6ff","success":"#3fb950","warning":"#d29922","error":"#f85149"}.get(level,"#8b949e")
        logs.append(f'<span style="color:{color}">[{ts}] {msg}</span>')

    log("info", f"Starting transform — {len(df)} raw rows")

    # Standardise column names
    df.columns = [c.strip().lower().replace(" ","_") for c in df.columns]

    # Map user CSV columns → expected names
    col_map = {
        "symbol":"ticker","stock":"ticker","equity":"ticker",
        "price":"price_usd","close":"price_usd","trade_price":"price_usd",
        "cost":"cost_basis_usd","basis":"cost_basis_usd","avg_cost":"cost_basis_usd",
        "qty":"shares","quantity":"shares","volume":"shares",
        "type":"action","trade_type":"action","transaction_type":"action","side":"action",
        "rating":"analyst_rating","rec":"analyst_rating",
        "pe":"pe_ratio","p_e":"pe_ratio","price_to_earnings":"pe_ratio",
        "mkt_cap":"market_cap_bn","mktcap":"market_cap_bn","cap":"market_cap_bn",
        "industry":"sector","category":"sector",
        "trade_date":"date","transaction_date":"date",
    }
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})

    # Ensure required cols exist
    for col in ["ticker","action","shares","price_usd"]:
        if col not in df.columns:
            df[col] = "UNKNOWN" if col in ["ticker","action"] else 0

    # Date parsing
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        bad = int(df["date"].isna().sum())
        if bad:
            warns.append(f"{bad} rows had unparseable dates — dropped")
            log("warning", f"{bad} rows dropped (bad dates)")
            df = df.dropna(subset=["date"])
    else:
        df["date"] = pd.Timestamp("2024-01-01")

    # Price nulls — fill with median per ticker
    null_prices = int(df["price_usd"].isna().sum()) if "price_usd" in df.columns else 0
    if null_prices:
        medians = df.groupby("ticker")["price_usd"].transform("median")
        df["price_usd"] = df["price_usd"].fillna(medians).fillna(df["price_usd"].median())
        log("info", f"Imputed {null_prices} null price_usd with ticker median")

    # Numeric coercion
    for col in ["price_usd","cost_basis_usd","shares","pe_ratio","market_cap_bn"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    # Negatives → absolute
    for col in ["price_usd","cost_basis_usd","shares"]:
        if col in df.columns:
            neg = int((df[col] < 0).sum())
            if neg:
                df[col] = df[col].abs()
                log("warning", f"{neg} negative values in {col} → abs()")

    # Fill cost_basis if missing
    if "cost_basis_usd" not in df.columns or df["cost_basis_usd"].eq(0).all():
        df["cost_basis_usd"] = df["price_usd"]

    # String cleanup
    for col in ["ticker","action","analyst_rating","sector","company","notes"]:
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip().replace({"nan":"","None":""})
    df["ticker"] = df["ticker"].str.upper()
    df["action"] = df["action"].str.upper()

    # Standardise action values
    buy_syns  = {"BUY","PURCHASE","B","LONG","BOUGHT"}
    sell_syns = {"SELL","SALE","S","SHORT","SOLD"}
    df["action"] = df["action"].apply(
        lambda x: "BUY" if x in buy_syns else ("SELL" if x in sell_syns else x)
    )

    # Dedupe
    dupes = int(df.duplicated().sum())
    if dupes:
        df = df.drop_duplicates()
        log("warning", f"Removed {dupes} duplicate rows")
        warns.append(f"{dupes} duplicate rows removed")

    # Add metadata cols if not present
    if "sector" not in df.columns or df["sector"].eq("").all():
        df["sector"] = df["ticker"].map({t: m["sector"] for t, m in TICKER_META.items()}).fillna("Other")
    if "company" not in df.columns or df["company"].eq("").all():
        df["company"] = df["ticker"].map({t: m["name"] for t, m in TICKER_META.items()}).fillna(df["ticker"])

    # ── Derived metrics ──────────────────────────────────────
    df["trade_value_usd"]   = (df["price_usd"]      * df["shares"]).round(2)
    df["cost_basis_total"]  = (df["cost_basis_usd"]  * df["shares"]).round(2)
    df["unrealised_pnl"]    = ((df["price_usd"] - df["cost_basis_usd"]) * df["shares"]).round(2)
    df["pnl_pct"]           = np.where(
        df["cost_basis_usd"] > 0,
        ((df["price_usd"] - df["cost_basis_usd"]) / df["cost_basis_usd"] * 100).round(2),
        0.0,
    )
    df["is_winner"]         = df["pnl_pct"] > 0
    df["is_loser"]          = df["pnl_pct"] < -5
    df["fiscal_quarter"]    = "Q" + df["date"].dt.quarter.astype(str)
    df["month"]             = df["date"].dt.to_period("M").astype(str)
    df["year"]              = df["date"].dt.year
    df["trade_id"]          = [f"TRD-{10000+i:05d}" for i in range(len(df))]

    log("success", f"Transform complete — {len(df)} rows, {df['ticker'].nunique()} tickers")
    log("success", f"Total portfolio value: ${df.loc[df['action']=='BUY','trade_value_usd'].sum():,.0f}")
    return df, logs, warns

# ─────────────────────────────────────────────────────────────
# ALPHA VANTAGE — live quote fetch
# ─────────────────────────────────────────────────────────────
def fetch_live_quotes(tickers: list[str], api_key: str) -> dict:
    import requests
    quotes = {}
    for ticker in tickers[:5]:            # free tier: 25 calls/day
        try:
            url = (f"https://www.alphavantage.co/query"
                   f"?function=GLOBAL_QUOTE&symbol={ticker}&apikey={api_key}")
            r = requests.get(url, timeout=8)
            data = r.json().get("Global Quote", {})
            if data and "05. price" in data:
                quotes[ticker] = {
                    "price":  float(data["05. price"]),
                    "change": float(data["09. change"]),
                    "change_pct": data["10. change percent"].replace("%",""),
                    "volume": int(data["06. volume"]),
                    "prev_close": float(data["08. previous close"]),
                }
        except Exception:
            pass
        time.sleep(0.3)
    return quotes

# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────
def fmt(v, prefix="$", suffix="", decimals=1):
    if abs(v) >= 1_000_000_000: return f"{prefix}{v/1_000_000_000:.{decimals}f}B{suffix}"
    if abs(v) >= 1_000_000:     return f"{prefix}{v/1_000_000:.{decimals}f}M{suffix}"
    if abs(v) >= 1_000:         return f"{prefix}{v/1_000:.{decimals}f}K{suffix}"
    return f"{prefix}{v:.{decimals}f}{suffix}"

def kpi(col, label, value, sub="", color="gray"):
    with col:
        st.markdown(f"""
        <div class="kpi-card {color}">
            <div class="kpi-label">{label}</div>
            <div class="kpi-value">{value}</div>
            {"<div class='kpi-sub'>" + sub + "</div>" if sub else ""}
        </div>""", unsafe_allow_html=True)

def section(title):
    st.markdown(f'<div class="section-title">{title}</div>', unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────
# SIDEBAR
# ─────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("""
    <div style="padding:0.4rem 0 1.2rem">
        <div style="font-size:1.2rem;font-weight:600;color:#e6edf3;letter-spacing:-0.02em;">
            📈 Portfolio Analytics
        </div>
        <div style="font-size:0.7rem;color:#484f58;margin-top:0.2rem;font-family:'IBM Plex Mono',monospace;">
            Equity · ETL · Analysis
        </div>
    </div>
    """, unsafe_allow_html=True)

    page = st.radio("Navigation", [
        "Overview", "Run Pipeline", "P&L Analysis",
        "Sector Breakdown", "Holdings Table", "Data Quality"
    ], label_visibility="collapsed")

    st.markdown("<div style='border-top:1px solid #21262d;margin:1rem 0'></div>", unsafe_allow_html=True)

    # ── DATA SOURCE ─────────────────────────────────────────
    st.markdown('<div style="font-size:0.65rem;font-weight:600;letter-spacing:0.12em;text-transform:uppercase;color:#484f58;margin-bottom:0.6rem;">Data Source</div>', unsafe_allow_html=True)

    data_source = st.radio("Source", ["Demo Portfolio", "Upload CSV / Excel", "Alpha Vantage API"],
                           label_visibility="collapsed")

    raw_df = None
    data_label = "demo"

    if data_source == "Demo Portfolio":
        n_records = st.slider("Records", 100, 1000, 500, 50)
        seed_val  = st.number_input("Seed", value=42, step=1,
                                    help="Change to simulate a different portfolio")
        data_label = "demo"

    elif data_source == "Upload CSV / Excel":
        uploaded = st.file_uploader(
            "Drop file here",
            type=["csv","xlsx","xls"],
            help="Must have columns: ticker, action (BUY/SELL), shares, price_usd, date",
            label_visibility="collapsed",
        )
        if uploaded:
            try:
                if uploaded.name.endswith((".xlsx",".xls")):
                    raw_df = pd.read_excel(uploaded)
                else:
                    raw_df = pd.read_csv(uploaded)
                st.success(f"Loaded {len(raw_df):,} rows · {raw_df.shape[1]} columns")
                data_label = "csv"
            except Exception as e:
                st.error(f"Could not read file: {e}")
        else:
            st.markdown("""
            <div class="upload-hint">
                Upload any CSV or Excel file<br>
                with stock trade data.<br><br>
                <strong style="color:#d29922">Required:</strong> ticker, action, shares, price<br>
                <strong style="color:#58a6ff">Optional:</strong> date, sector, cost_basis, pe_ratio
            </div>
            """, unsafe_allow_html=True)
            # Show sample CSV template
            sample = pd.DataFrame([
                {"date":"2024-01-15","ticker":"AAPL","action":"BUY", "shares":50, "price_usd":185.20,"cost_basis_usd":175.00,"sector":"Technology","analyst_rating":"Buy","pe_ratio":29.4,"market_cap_bn":2850},
                {"date":"2024-02-10","ticker":"MSFT","action":"BUY", "shares":30, "price_usd":405.60,"cost_basis_usd":380.00,"sector":"Technology","analyst_rating":"Strong Buy","pe_ratio":34.1,"market_cap_bn":3100},
                {"date":"2024-03-05","ticker":"JPM", "action":"SELL","shares":20, "price_usd":197.80,"cost_basis_usd":165.00,"sector":"Financials","analyst_rating":"Hold","pe_ratio":11.8,"market_cap_bn":580},
            ])
            csv_bytes = sample.to_csv(index=False).encode()
            st.download_button("⬇ Download template CSV", csv_bytes,
                               "portfolio_template.csv", "text/csv",
                               use_container_width=True)

    else:  # Alpha Vantage
        av_key = st.text_input("API Key", type="password",
                               placeholder="Enter your free key…",
                               help="Get a free key at alphavantage.co")
        av_tickers_raw = st.text_input("Tickers (comma-separated)", value="AAPL,MSFT,NVDA,JPM,XOM")
        av_tickers = [t.strip().upper() for t in av_tickers_raw.split(",") if t.strip()]

        if av_key and st.button("Fetch Live Quotes", use_container_width=True, type="primary"):
            with st.spinner(f"Fetching {len(av_tickers)} quotes..."):
                quotes = fetch_live_quotes(av_tickers, av_key)
            if quotes:
                rows = []
                for tkr, q in quotes.items():
                    meta = TICKER_META.get(tkr, {})
                    rows.append({
                        "date": datetime.today().strftime("%Y-%m-%d"),
                        "ticker": tkr, "company": meta.get("name", tkr),
                        "sector": meta.get("sector", "Unknown"),
                        "action": "BUY", "shares": 100,
                        "price_usd": q["price"],
                        "cost_basis_usd": q["prev_close"],
                        "analyst_rating": "Hold",
                        "pe_ratio": meta.get("pe", 0),
                        "market_cap_bn": meta.get("mcap_bn", 0),
                        "notes": f"Live quote · {q['change_pct']}% today",
                    })
                raw_df = pd.DataFrame(rows)
                st.success(f"Fetched {len(quotes)} live quotes")
                data_label = "live"
            else:
                st.error("No data returned. Check your API key and tickers.")
        elif not av_key:
            st.info("Enter your Alpha Vantage key to fetch live data. Using demo data until then.")

        if raw_df is None:
            data_label = "demo"

    st.markdown("<div style='border-top:1px solid #21262d;margin:1rem 0'></div>", unsafe_allow_html=True)
    if st.button("↺  Refresh", use_container_width=True):
        st.cache_data.clear(); st.rerun()

    st.markdown(f"""
    <div style="font-size:0.68rem;color:#30363d;line-height:1.8;margin-top:0.5rem;">
        Ronith Reddy<br>
        Financial ETL Pipeline<br>
        Equity Analytics · v2.0
    </div>""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────
# LOAD & TRANSFORM DATA
# ─────────────────────────────────────────────────────────────
@st.cache_data(show_spinner=False, ttl=300)
def get_demo_data(n, seed):
    raw = generate_portfolio(n, seed)
    df, logs, warns = run_transform(raw)
    return df, logs, warns

if raw_df is not None:
    df, logs, warns = run_transform(raw_df)
    st.cache_data.clear()
elif data_source == "Demo Portfolio":
    df, logs, warns = get_demo_data(int(n_records), int(seed_val))
else:
    df, logs, warns = get_demo_data(500, 42)

# Source badge HTML
SOURCE_BADGE = {
    "demo": '<span class="source-badge source-demo">● DEMO DATA</span>',
    "csv":  '<span class="source-badge source-csv">● CSV UPLOAD</span>',
    "live": '<span class="source-badge source-live">● LIVE QUOTES</span>',
}[data_label]

buys = df[df["action"] == "BUY"]
sells = df[df["action"] == "SELL"]

# ═════════════════════════════════════════════════════════════
# PAGE 1 — OVERVIEW
# ═════════════════════════════════════════════════════════════
if page == "Overview":
    st.markdown(f"## Portfolio Overview &nbsp; {SOURCE_BADGE}", unsafe_allow_html=True)
    st.markdown(f"<div style='font-size:0.78rem;color:#484f58;margin-bottom:1.2rem'>{len(df):,} trades · {df['ticker'].nunique()} tickers · {df['sector'].nunique()} sectors · {df['date'].dt.year.nunique()} year(s)</div>", unsafe_allow_html=True)

    total_invested   = buys["trade_value_usd"].sum()
    total_pnl        = df["unrealised_pnl"].sum()
    pnl_pct_overall  = (total_pnl / total_invested * 100) if total_invested else 0
    winners          = int(df["is_winner"].sum())
    losers           = int(df["is_loser"].sum())
    win_rate         = winners / len(df) * 100 if len(df) else 0
    avg_pe           = df[df["pe_ratio"] > 0]["pe_ratio"].mean()
    best_ticker      = df.groupby("ticker")["pnl_pct"].mean().idxmax() if len(df) else "—"
    worst_ticker     = df.groupby("ticker")["pnl_pct"].mean().idxmin() if len(df) else "—"

    c1,c2,c3,c4,c5,c6 = st.columns(6)
    pnl_color = "green" if total_pnl >= 0 else "red"
    pnl_sign  = "▲" if total_pnl >= 0 else "▼"
    kpi(c1, "Total Invested",    fmt(total_invested),         f"{len(buys):,} buy orders",              "blue")
    kpi(c2, "Unrealised P&L",    fmt(total_pnl),              f"{pnl_sign} {abs(pnl_pct_overall):.1f}% vs cost basis", pnl_color)
    kpi(c3, "Win Rate",          f"{win_rate:.0f}%",          f"{winners} winners / {losers} losers",   "green" if win_rate>50 else "amber")
    kpi(c4, "Portfolio Tickers", str(df["ticker"].nunique()), f"across {df['sector'].nunique()} sectors","blue")
    kpi(c5, "Avg P/E Ratio",     f"{avg_pe:.1f}×",            "weighted by trade count",                "purple")
    kpi(c6, "Avg Trade Size",    fmt(df["trade_value_usd"].mean()), f"median {fmt(df['trade_value_usd'].median())}", "gray")

    st.markdown("<div style='margin-top:1rem'></div>", unsafe_allow_html=True)
    col_l, col_r = st.columns([3, 2])

    with col_l:
        section("Monthly Trade Volume & P&L")
        monthly = df.groupby("month").agg(
            invested=("trade_value_usd","sum"),
            pnl=("unrealised_pnl","sum"),
            trades=("trade_id","count"),
        ).reset_index().sort_values("month")
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                            row_heights=[0.65,0.35], vertical_spacing=0.05)
        fig.add_trace(go.Bar(x=monthly["month"], y=monthly["invested"]/1000,
                             name="Trade Volume", marker_color=C["blue"], opacity=0.75), row=1, col=1)
        bar_c = [C["green"] if v>=0 else C["red"] for v in monthly["pnl"]]
        fig.add_trace(go.Bar(x=monthly["month"], y=monthly["pnl"]/1000,
                             name="Unrealised P&L", marker_color=bar_c, opacity=0.85), row=2, col=1)
        fig.update_layout(**PT, height=330, showlegend=True,
                          legend=dict(orientation="h",y=1.06,x=0))
        fig.update_yaxes(tickprefix="$", ticksuffix="K", row=1, col=1)
        fig.update_yaxes(tickprefix="$", ticksuffix="K", row=2, col=1)
        st.plotly_chart(fig, use_container_width=True)

    with col_r:
        section("Sector Allocation (by Trade Value)")
        sec_alloc = buys.groupby("sector")["trade_value_usd"].sum().reset_index()
        sec_alloc.columns = ["sector","value"]
        sec_alloc = sec_alloc.sort_values("value", ascending=False)
        colors = [SECTOR_COLORS.get(s, C["gray"]) for s in sec_alloc["sector"]]
        fig2 = go.Figure(go.Pie(
            labels=sec_alloc["sector"], values=sec_alloc["value"],
            hole=0.52,
            marker=dict(colors=colors, line=dict(color="#0d1117", width=2)),
            textfont=dict(size=10, color="#e6edf3"),
            hovertemplate="<b>%{label}</b><br>$%{value:,.0f}<br>%{percent}<extra></extra>",
        ))
        fig2.update_layout(paper_bgcolor="rgba(0,0,0,0)",
                           font=dict(family="IBM Plex Sans",color="#8b949e"),
                           legend=dict(font=dict(size=10),orientation="v"),
                           height=330, margin=dict(l=0,r=0,t=20,b=0))
        st.plotly_chart(fig2, use_container_width=True)

    section("Top Performers vs Worst Performers")
    ticker_pnl = df.groupby(["ticker","company","sector"]).agg(
        avg_pnl_pct=("pnl_pct","mean"),
        total_pnl=("unrealised_pnl","sum"),
        trades=("trade_id","count"),
    ).reset_index().sort_values("avg_pnl_pct")

    col_worst, col_best = st.columns(2)
    with col_worst:
        bottom5 = ticker_pnl.head(5)
        fig = go.Figure(go.Bar(
            y=bottom5["ticker"], x=bottom5["avg_pnl_pct"],
            orientation="h", marker_color=C["red"], opacity=0.85,
            text=[f"{v:.1f}%" for v in bottom5["avg_pnl_pct"]],
            textposition="outside",
        ))
        fig.update_layout(**PT, height=230, title="Bottom 5 Positions",
                          xaxis_ticksuffix="%")
        fig.add_vline(x=0, line_color="#30363d", line_width=1)
        st.plotly_chart(fig, use_container_width=True)

    with col_best:
        top5 = ticker_pnl.tail(5).sort_values("avg_pnl_pct", ascending=True)
        fig = go.Figure(go.Bar(
            y=top5["ticker"], x=top5["avg_pnl_pct"],
            orientation="h", marker_color=C["green"], opacity=0.85,
            text=[f"+{v:.1f}%" for v in top5["avg_pnl_pct"]],
            textposition="outside",
        ))
        fig.update_layout(**PT, height=230, title="Top 5 Positions",
                          xaxis_ticksuffix="%")
        fig.add_vline(x=0, line_color="#30363d", line_width=1)
        st.plotly_chart(fig, use_container_width=True)


# ═════════════════════════════════════════════════════════════
# PAGE 2 — RUN PIPELINE
# ═════════════════════════════════════════════════════════════
elif page == "Run Pipeline":
    st.markdown(f"## ETL Pipeline Log &nbsp; {SOURCE_BADGE}", unsafe_allow_html=True)

    col_log, col_summary = st.columns([3, 2])
    with col_log:
        section("Execution Log")
        log_html = "<br>".join(logs)
        st.markdown(f"""
        <div style="background:#010409;border:1px solid #21262d;border-radius:6px;
                    padding:1rem;font-family:'IBM Plex Mono',monospace;font-size:0.73rem;
                    color:#8b949e;max-height:340px;overflow-y:auto;line-height:1.75;">
            {log_html}
        </div>""", unsafe_allow_html=True)

    with col_summary:
        section("Run Summary")
        total_invested = buys["trade_value_usd"].sum()
        items = [
            ("Data source",    data_source),
            ("Rows loaded",    f"{len(df):,}"),
            ("Tickers",        str(df["ticker"].nunique())),
            ("Buy orders",     str(len(buys))),
            ("Sell orders",    str(len(sells))),
            ("Warnings",       str(len(warns))),
            ("P&L calculated", "✓"),
            ("Sectors mapped", f"{df['sector'].nunique()}"),
            ("Total invested", fmt(total_invested)),
        ]
        for label, value in items:
            color = "#3fb950" if value == "✓" else "#e6edf3"
            st.markdown(f"""
            <div style="display:flex;justify-content:space-between;align-items:center;
                        padding:0.4rem 0;border-bottom:1px solid #21262d;font-size:0.82rem;">
                <span style="color:#6e7681">{label}</span>
                <span style="color:{color};font-family:'IBM Plex Mono',monospace">{value}</span>
            </div>""", unsafe_allow_html=True)

        if warns:
            st.markdown("<div style='margin-top:1rem'></div>", unsafe_allow_html=True)
            section("Warnings")
            for w in warns:
                st.markdown(f"<div style='font-size:0.78rem;color:#d29922;padding:0.3rem 0;'>⚠ {w}</div>", unsafe_allow_html=True)

    section("Pipeline Stages")
    stages = [
        ("01","INGEST",   "CSV / API / Demo\nRaw data load",           C["blue"]),
        ("02","CLEAN",    "Null imputation\nType coercion · Dedup",     C["purple"]),
        ("03","MAP",      "Sector tagging\nColumn normalisation",       C["amber"]),
        ("04","METRICS",  "P&L · Win rate\nP/E · Trade value",         C["green"]),
        ("05","VALIDATE", "Schema checks\nRange validation",            C["blue"]),
        ("06","SERVE",    "Dashboard\nDownload / API",                  C["gray"]),
    ]
    cols = st.columns(6)
    for col, (num, title, desc, color) in zip(cols, stages):
        with col:
            st.markdown(f"""
            <div style="border:1px solid {color}33;border-top:2px solid {color};
                        border-radius:6px;padding:0.85rem 0.7rem;text-align:center;background:#0d1117;">
                <div style="font-size:0.62rem;color:{color};font-weight:600;
                            font-family:'IBM Plex Mono',monospace;letter-spacing:0.1em">{num}</div>
                <div style="font-size:0.86rem;font-weight:600;color:#e6edf3;
                            margin:0.25rem 0;letter-spacing:-0.01em">{title}</div>
                <div style="font-size:0.7rem;color:#484f58;line-height:1.55;
                            white-space:pre-line">{desc}</div>
            </div>""", unsafe_allow_html=True)

    section("Export Clean Data")
    col_a, col_b = st.columns(2)
    with col_a:
        csv_buf = io.StringIO(); df.to_csv(csv_buf, index=False)
        st.download_button("⬇  Download clean_portfolio.csv", csv_buf.getvalue(),
                           f"clean_portfolio_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.csv",
                           "text/csv", use_container_width=True)
    with col_b:
        summary = df.groupby(["ticker","sector"]).agg(
            trades=("trade_id","count"),
            avg_pnl_pct=("pnl_pct","mean"),
            total_pnl=("unrealised_pnl","sum"),
            avg_pe=("pe_ratio","mean"),
        ).reset_index().round(2)
        sumcsv = io.StringIO(); summary.to_csv(sumcsv, index=False)
        st.download_button("⬇  Download ticker_summary.csv", sumcsv.getvalue(),
                           "ticker_summary.csv", "text/csv", use_container_width=True)


# ═════════════════════════════════════════════════════════════
# PAGE 3 — P&L ANALYSIS
# ═════════════════════════════════════════════════════════════
elif page == "P&L Analysis":
    st.markdown(f"## P&L Analysis &nbsp; {SOURCE_BADGE}", unsafe_allow_html=True)

    total_pnl    = df["unrealised_pnl"].sum()
    invested     = buys["trade_value_usd"].sum()
    pnl_pct      = total_pnl / invested * 100 if invested else 0
    win_rate     = df["is_winner"].mean() * 100
    avg_winner   = df[df["is_winner"]]["pnl_pct"].mean()
    avg_loser    = df[~df["is_winner"]]["pnl_pct"].mean()
    sharpe_proxy = df["pnl_pct"].mean() / (df["pnl_pct"].std() + 1e-9)

    c1,c2,c3,c4,c5 = st.columns(5)
    pnl_col = "green" if total_pnl >= 0 else "red"
    kpi(c1,"Total Unrealised P&L", fmt(total_pnl),             f"{'▲' if total_pnl>=0 else '▼'} {abs(pnl_pct):.1f}% vs basis", pnl_col)
    kpi(c2,"Win Rate",             f"{win_rate:.0f}%",          f"{int(df['is_winner'].sum())} of {len(df)} positions",          "green" if win_rate>55 else "amber")
    kpi(c3,"Avg Winner",           f"+{avg_winner:.1f}%",       "avg P&L on winning trades",                                     "green")
    kpi(c4,"Avg Loser",            f"{avg_loser:.1f}%",         "avg P&L on losing trades",                                      "red")
    kpi(c5,"Sharpe Proxy",         f"{sharpe_proxy:.2f}",       "mean/stdev of trade P&L%",                                      "purple")

    col_l, col_r = st.columns(2)
    with col_l:
        section("P&L Distribution")
        fig = go.Figure()
        fig.add_trace(go.Histogram(
            x=df[df["is_winner"]]["pnl_pct"], name="Winners",
            marker_color=C["green"], opacity=0.75, nbinsx=35,
        ))
        fig.add_trace(go.Histogram(
            x=df[~df["is_winner"]]["pnl_pct"], name="Losers",
            marker_color=C["red"], opacity=0.75, nbinsx=35,
        ))
        fig.add_vline(x=0, line_color="#30363d", line_width=1.5)
        fig.update_layout(**PT, barmode="overlay", height=300,
                          xaxis_ticksuffix="%",
                          legend=dict(orientation="h", y=1.07))
        st.plotly_chart(fig, use_container_width=True)

    with col_r:
        section("P&L by Analyst Rating")
        rating_order = ["Strong Buy","Buy","Hold","Underperform","Sell"]
        rating_pnl = df.groupby("analyst_rating")["pnl_pct"].mean().reindex(
            [r for r in rating_order if r in df["analyst_rating"].unique()]
        ).reset_index()
        rating_pnl.columns = ["rating","avg_pnl_pct"]
        bar_c = [C["green"] if v>0 else C["red"] for v in rating_pnl["avg_pnl_pct"]]
        fig2 = go.Figure(go.Bar(
            x=rating_pnl["rating"], y=rating_pnl["avg_pnl_pct"],
            marker_color=bar_c, opacity=0.85,
            text=[f"{v:.1f}%" for v in rating_pnl["avg_pnl_pct"]],
            textposition="outside",
        ))
        fig2.add_hline(y=0, line_color="#30363d", line_width=1)
        fig2.update_layout(**PT, height=300, yaxis_ticksuffix="%")
        st.plotly_chart(fig2, use_container_width=True)

    section("Price vs Cost Basis — Trade Scatter")
    fig3 = px.scatter(
        df, x="cost_basis_usd", y="price_usd",
        color="sector",
        color_discrete_map=SECTOR_COLORS,
        size="shares", size_max=16,
        opacity=0.65,
        hover_data=["ticker","company","pnl_pct","analyst_rating"],
        labels={"cost_basis_usd":"Cost Basis (USD)","price_usd":"Current Price (USD)","sector":"Sector"},
    )
    max_v = max(df["cost_basis_usd"].max(), df["price_usd"].max())
    fig3.add_trace(go.Scatter(x=[0,max_v], y=[0,max_v], mode="lines",
                              line=dict(color="#30363d",dash="dot",width=1.5),
                              name="Break-even", showlegend=True))
    fig3.update_layout(**PT, height=340,
                       xaxis_tickprefix="$", yaxis_tickprefix="$",
                       legend=dict(orientation="h", y=1.05))
    st.plotly_chart(fig3, use_container_width=True)

    section("Quarterly P&L Heatmap — Ticker × Quarter")
    top_tickers = df.groupby("ticker")["unrealised_pnl"].sum().abs().nlargest(12).index
    heat_df = df[df["ticker"].isin(top_tickers)]
    pivot = heat_df.pivot_table(values="pnl_pct", index="ticker",
                                columns="fiscal_quarter", aggfunc="mean").round(1)
    fig4 = go.Figure(go.Heatmap(
        z=pivot.values, x=pivot.columns.tolist(), y=pivot.index.tolist(),
        colorscale=[[0,"#3d0000"],[0.35,"#7c1a1a"],[0.5,"#21262d"],
                    [0.65,"#1a3d1a"],[1,"#003d00"]],
        zmid=0,
        text=[[f"{v:.1f}%" if not np.isnan(v) else "" for v in row] for row in pivot.values],
        texttemplate="%{text}", textfont=dict(size=11, color="#e6edf3"),
        colorbar=dict(title="Avg P&L %", ticksuffix="%", thickness=12),
    ))
    fig4.update_layout(**PT, height=max(260, len(top_tickers)*28))
    st.plotly_chart(fig4, use_container_width=True)


# ═════════════════════════════════════════════════════════════
# PAGE 4 — SECTOR BREAKDOWN
# ═════════════════════════════════════════════════════════════
elif page == "Sector Breakdown":
    st.markdown(f"## Sector Breakdown &nbsp; {SOURCE_BADGE}", unsafe_allow_html=True)

    sec = df.groupby("sector").agg(
        invested=("trade_value_usd","sum"),
        pnl=("unrealised_pnl","sum"),
        avg_pnl_pct=("pnl_pct","mean"),
        avg_pe=("pe_ratio","mean"),
        trades=("trade_id","count"),
        tickers=("ticker","nunique"),
        winners=("is_winner","sum"),
    ).reset_index()
    sec["win_rate"] = (sec["winners"] / sec["trades"] * 100).round(1)
    sec = sec.sort_values("invested", ascending=False)

    col_l, col_r = st.columns(2)
    with col_l:
        section("Invested Capital by Sector")
        bar_c = [SECTOR_COLORS.get(s, C["gray"]) for s in sec["sector"]]
        fig = go.Figure(go.Bar(
            x=sec["sector"], y=sec["invested"]/1_000_000,
            marker_color=bar_c, opacity=0.85,
            text=[fmt(v/1_000_000, prefix="$", suffix="M") for v in sec["invested"]],
            textposition="outside",
        ))
        fig.update_layout(**PT, height=320, yaxis_tickprefix="$", yaxis_ticksuffix="M")
        st.plotly_chart(fig, use_container_width=True)

    with col_r:
        section("Average P&L % by Sector")
        sec_sorted = sec.sort_values("avg_pnl_pct")
        bar_c2 = [C["green"] if v>0 else C["red"] for v in sec_sorted["avg_pnl_pct"]]
        fig2 = go.Figure(go.Bar(
            x=sec_sorted["avg_pnl_pct"], y=sec_sorted["sector"],
            orientation="h", marker_color=bar_c2, opacity=0.85,
            text=[f"{v:.1f}%" for v in sec_sorted["avg_pnl_pct"]],
            textposition="outside",
        ))
        fig2.add_vline(x=0, line_color="#30363d", line_width=1)
        fig2.update_layout(**PT, height=320, xaxis_ticksuffix="%")
        st.plotly_chart(fig2, use_container_width=True)

    section("Sector P/E Ratio vs Win Rate (bubble = trade volume)")
    sec_plot = sec[sec["avg_pe"] > 0].copy()
    fig3 = px.scatter(
        sec_plot, x="avg_pe", y="win_rate",
        size="trades", color="sector",
        color_discrete_map=SECTOR_COLORS,
        text="sector", size_max=50,
        hover_data=["invested","avg_pnl_pct"],
        labels={"avg_pe":"Avg P/E Ratio","win_rate":"Win Rate %","trades":"# Trades"},
    )
    fig3.update_traces(textposition="top center", textfont=dict(size=10, color="#e6edf3"))
    fig3.add_hline(y=50, line_color="#30363d", line_dash="dot", annotation_text="50% win rate")
    fig3.update_layout(**PT, height=360,
                       xaxis_ticksuffix="×", yaxis_ticksuffix="%",
                       showlegend=False)
    st.plotly_chart(fig3, use_container_width=True)

    section("Sector Summary Table")
    tbl = sec.copy()
    tbl["invested"]    = tbl["invested"].apply(fmt)
    tbl["pnl"]         = tbl["pnl"].apply(fmt)
    tbl["avg_pnl_pct"] = tbl["avg_pnl_pct"].apply(lambda x: f"{x:.1f}%")
    tbl["avg_pe"]      = tbl["avg_pe"].apply(lambda x: f"{x:.1f}×")
    tbl["win_rate"]    = tbl["win_rate"].apply(lambda x: f"{x:.0f}%")
    tbl = tbl.rename(columns={"sector":"Sector","invested":"Invested","pnl":"Unrealised P&L",
                               "avg_pnl_pct":"Avg P&L %","avg_pe":"Avg P/E","trades":"Trades",
                               "tickers":"Tickers","win_rate":"Win Rate"})
    st.dataframe(tbl[["Sector","Invested","Unrealised P&L","Avg P&L %","Avg P/E","Trades","Win Rate"]], 
                 use_container_width=True, hide_index=True)


# ═════════════════════════════════════════════════════════════
# PAGE 5 — HOLDINGS TABLE
# ═════════════════════════════════════════════════════════════
elif page == "Holdings Table":
    st.markdown(f"## Holdings Explorer &nbsp; {SOURCE_BADGE}", unsafe_allow_html=True)

    f1,f2,f3,f4 = st.columns(4)
    with f1: sel_sector  = st.multiselect("Sector",  sorted(df["sector"].unique()),  placeholder="All sectors")
    with f2: sel_action  = st.multiselect("Action",  ["BUY","SELL"],                  placeholder="BUY & SELL")
    with f3: sel_rating  = st.multiselect("Rating",  sorted(df["analyst_rating"].dropna().unique()), placeholder="All ratings")
    with f4: sel_winners = st.selectbox( "P&L",     ["All","Winners only","Losers only"])

    fdf = df.copy()
    if sel_sector:  fdf = fdf[fdf["sector"].isin(sel_sector)]
    if sel_action:  fdf = fdf[fdf["action"].isin(sel_action)]
    if sel_rating:  fdf = fdf[fdf["analyst_rating"].isin(sel_rating)]
    if sel_winners == "Winners only": fdf = fdf[fdf["is_winner"]]
    if sel_winners == "Losers only":  fdf = fdf[~fdf["is_winner"]]

    st.markdown(f"<div style='font-size:0.75rem;color:#484f58;margin:0.4rem 0'>{len(fdf):,} trades matching filters</div>", unsafe_allow_html=True)

    section("Trade Value vs P&L%")
    fig = px.scatter(
        fdf, x="trade_value_usd", y="pnl_pct",
        color="action",
        color_discrete_map={"BUY": C["green"], "SELL": C["red"]},
        hover_data=["ticker","company","sector","analyst_rating","shares"],
        opacity=0.6, size_max=10,
        labels={"trade_value_usd":"Trade Value (USD)","pnl_pct":"P&L %"},
    )
    fig.add_hline(y=0, line_color="#30363d", line_width=1)
    fig.update_layout(**PT, height=280, xaxis_tickprefix="$", yaxis_ticksuffix="%",
                      legend=dict(orientation="h", y=1.05))
    st.plotly_chart(fig, use_container_width=True)

    section("All Trades")
    show = ["trade_id","date","ticker","company","sector","action","shares",
            "price_usd","cost_basis_usd","trade_value_usd","unrealised_pnl",
            "pnl_pct","analyst_rating","pe_ratio","market_cap_bn","fiscal_quarter"]
    tbl = fdf[[c for c in show if c in fdf.columns]].copy()
    tbl["date"]             = tbl["date"].astype(str)
    tbl["pnl_pct"]          = tbl["pnl_pct"].round(2)
    tbl["unrealised_pnl"]   = tbl["unrealised_pnl"].round(2)
    tbl["trade_value_usd"]  = tbl["trade_value_usd"].round(2)
    st.dataframe(tbl, use_container_width=True, hide_index=True, height=420)


# ═════════════════════════════════════════════════════════════
# PAGE 6 — DATA QUALITY
# ═════════════════════════════════════════════════════════════
elif page == "Data Quality":
    st.markdown(f"## Data Quality Report &nbsp; {SOURCE_BADGE}", unsafe_allow_html=True)

    raw_test    = generate_portfolio(500, 42) if data_label == "demo" else df.copy()
    null_prices = int(raw_test.get("price_usd", pd.Series()).isna().sum()) if "price_usd" in raw_test.columns else 0
    null_any    = int(df.isna().sum().sum())
    dupes       = int(raw_test.duplicated().sum()) if hasattr(raw_test,"duplicated") else 0
    sectors_mapped = int((df["sector"] != "Other").sum())

    c1,c2,c3,c4 = st.columns(4)
    kpi(c1,"Null Prices Found",  str(null_prices),  "Imputed with ticker median",   "red" if null_prices>0 else "green")
    kpi(c2,"Remaining Nulls",    str(null_any),      "In clean output",               "red" if null_any>5 else "green")
    kpi(c3,"Duplicates Removed", str(dupes),         "Exact row deduplication",       "amber" if dupes>0 else "green")
    kpi(c4,"Sectors Mapped",     str(sectors_mapped),f"of {len(df)} trades",          "blue")

    col_l, col_r = st.columns(2)
    with col_l:
        section("P&L % Distribution")
        fig = go.Figure()
        fig.add_trace(go.Histogram(x=df["pnl_pct"], nbinsx=40,
                                   marker_color=C["blue"], opacity=0.8, name="All trades"))
        fig.add_vline(x=0,  line_color="#30363d",line_width=1.5)
        fig.add_vline(x=df["pnl_pct"].mean(), line_color=C["amber"], line_dash="dot",
                      annotation_text=f"Mean {df['pnl_pct'].mean():.1f}%", annotation_font_size=10)
        fig.update_layout(**PT, height=280, xaxis_ticksuffix="%")
        st.plotly_chart(fig, use_container_width=True)

    with col_r:
        section("Data Completeness by Column")
        completeness = (1 - df.isnull().mean()) * 100
        completeness = completeness.sort_values()
        bar_c = [C["red"] if v < 90 else C["green"] for v in completeness.values]
        fig2 = go.Figure(go.Bar(
            x=completeness.values, y=completeness.index,
            orientation="h", marker_color=bar_c, opacity=0.85,
        ))
        fig2.add_vline(x=100, line_color="#30363d", line_width=1)
        fig2.update_layout(**PT, height=280, xaxis_ticksuffix="%",
                           xaxis_range=[80,101])
        st.plotly_chart(fig2, use_container_width=True)

    section("Analyst Rating Distribution")
    rating_counts = df["analyst_rating"].value_counts().reset_index()
    rating_counts.columns = ["rating","count"]
    rating_order = ["Strong Buy","Buy","Hold","Underperform","Sell"]
    rating_counts = rating_counts.set_index("rating").reindex(
        [r for r in rating_order if r in rating_counts["rating"].values]
    ).reset_index()
    rating_colors = [C["green"],C["green"],C["amber"],C["red"],C["red"]]
    fig3 = go.Figure(go.Bar(
        x=rating_counts["rating"], y=rating_counts["count"],
        marker_color=rating_colors[:len(rating_counts)], opacity=0.85,
        text=rating_counts["count"], textposition="outside",
    ))
    fig3.update_layout(**PT, height=240)
    st.plotly_chart(fig3, use_container_width=True)

    section("Output Schema")
    schema = pd.DataFrame({
        "Column": df.columns,
        "Type":   [str(df[c].dtype) for c in df.columns],
        "Nulls":  [int(df[c].isna().sum()) for c in df.columns],
        "Unique": [int(df[c].nunique()) for c in df.columns],
        "Sample": [str(df[c].dropna().iloc[0])[:45] if len(df[c].dropna())>0 else "" for c in df.columns],
    })
    st.dataframe(schema, use_container_width=True, hide_index=True, height=360)
