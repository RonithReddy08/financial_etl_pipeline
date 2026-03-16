"""
extract.py — Data Extraction Layer
===================================
Pulls financial data from two sources:
  1. Alpha Vantage API  → live FX exchange rates (free tier)
  2. Simulated CSV data → multi-currency transaction records (mirrors WBR use case)

Design choices:
  - Each extractor returns a plain pandas DataFrame so the Transform layer
    doesn't care where data came from.
  - API calls are wrapped in retry logic (3 attempts, exponential back-off).
  - Raw data is always saved to /data/raw/ before transformation — this gives
    us an audit trail and lets us re-run transforms without hitting the API again.
"""

import os
import time
import random
import logging
import requests
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
RAW_DIR = Path(__file__).parents[1] / "data" / "raw"
RAW_DIR.mkdir(parents=True, exist_ok=True)

ALPHA_VANTAGE_KEY = os.getenv("ALPHA_VANTAGE_KEY", "demo")
BASE_CURRENCY = "USD"

# Currencies used in our simulated WBR-style operations
CURRENCIES = ["USD", "KES", "ZMW", "ZAR", "GHS", "UGX"]

# ---------------------------------------------------------------------------
# 1. FX Rate Extraction
# ---------------------------------------------------------------------------

def fetch_fx_rates(base: str = BASE_CURRENCY) -> pd.DataFrame:
    """
    Fetches current FX rates from Alpha Vantage for each target currency.
    Falls back to hardcoded realistic rates if API limit is hit (free tier = 25 calls/day).

    Returns a DataFrame with columns: [base_currency, target_currency, rate, fetched_at]
    """
    rates = []
    fallback_rates = {
        "KES": 129.50,   # Kenyan Shilling
        "ZMW": 26.80,    # Zambian Kwacha
        "ZAR": 18.35,    # South African Rand
        "GHS": 15.20,    # Ghanaian Cedi
        "UGX": 3720.00,  # Ugandan Shilling
        "USD": 1.00,
    }

    for target in CURRENCIES:
        if target == base:
            rates.append({
                "base_currency": base,
                "target_currency": target,
                "rate": 1.0,
                "fetched_at": datetime.utcnow().isoformat(),
                "source": "identity",
            })
            continue

        rate = _fetch_single_fx_rate(base, target, fallback_rates)
        rates.append(rate)
        time.sleep(0.5)  # respect free-tier rate limit

    df = pd.DataFrame(rates)
    _save_raw(df, "fx_rates")
    logger.info(f"Fetched FX rates for {len(df)} currency pairs")
    return df


def _fetch_single_fx_rate(base: str, target: str, fallback: dict) -> dict:
    """Single FX rate fetch with retry logic."""
    url = (
        f"https://www.alphavantage.co/query"
        f"?function=CURRENCY_EXCHANGE_RATE"
        f"&from_currency={base}&to_currency={target}"
        f"&apikey={ALPHA_VANTAGE_KEY}"
    )

    for attempt in range(3):
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            data = resp.json()

            if "Realtime Currency Exchange Rate" in data:
                rate_val = float(data["Realtime Currency Exchange Rate"]["5. Exchange Rate"])
                return {
                    "base_currency": base,
                    "target_currency": target,
                    "rate": rate_val,
                    "fetched_at": datetime.utcnow().isoformat(),
                    "source": "alpha_vantage",
                }
        except Exception as e:
            logger.warning(f"FX fetch attempt {attempt+1} failed for {base}/{target}: {e}")
            time.sleep(2 ** attempt)

    # Fallback to realistic hardcoded rate with small random noise
    logger.warning(f"Using fallback rate for {base}/{target}")
    noise = 1 + random.uniform(-0.02, 0.02)
    return {
        "base_currency": base,
        "target_currency": target,
        "rate": round(fallback.get(target, 1.0) * noise, 4),
        "fetched_at": datetime.utcnow().isoformat(),
        "source": "fallback",
    }


# ---------------------------------------------------------------------------
# 2. Transaction Data Extraction (simulated multi-currency financial data)
# ---------------------------------------------------------------------------

def generate_transactions(n_records: int = 500, seed: int = 42) -> pd.DataFrame:
    """
    Generates realistic multi-currency financial transactions modelled on
    World Bicycle Relief's program data: bicycle procurement, logistics,
    field operations, and donor disbursements across African countries.

    In a real pipeline this would be pd.read_csv() or a database query.
    We simulate it so the project is self-contained and portable.
    """
    random.seed(seed)

    programs = ["Bicycle Procurement", "Field Logistics", "Training Programs",
                "Community Outreach", "Admin & Overhead", "Donor Projects"]
    countries = [
        ("Kenya", "KES"), ("Zambia", "ZMW"), ("South Africa", "ZAR"),
        ("Ghana", "GHS"), ("Uganda", "UGX"), ("USA", "USD"),
    ]
    cost_centers = ["Operations", "Programs", "Finance", "HR", "Technology"]
    vendors = [f"Vendor_{chr(65+i)}" for i in range(10)]

    records = []
    base_date = datetime.today() - timedelta(days=365)

    for i in range(n_records):
        country, currency = random.choice(countries)
        program = random.choice(programs)
        budgeted = round(random.uniform(500, 50000), 2)
        # Introduce realistic variance: actuals deviate from budget by -20% to +30%
        variance_pct = random.uniform(-0.20, 0.30)
        actual = round(budgeted * (1 + variance_pct), 2)
        txn_date = base_date + timedelta(days=random.randint(0, 365))

        records.append({
            "transaction_id": f"TXN-{1000+i:05d}",
            "date": txn_date.strftime("%Y-%m-%d"),
            "country": country,
            "currency": currency,
            "program": program,
            "cost_center": random.choice(cost_centers),
            "vendor": random.choice(vendors),
            "budgeted_amount": budgeted,
            "actual_amount": actual,
            "description": f"{program} expense — {country}",
            # Introduce some data quality issues for the transform layer to fix
            "notes": random.choice(["", None, "approved", "pending review", "  "]),
        })

    # Deliberately inject ~3% nulls into actual_amount to test cleaning logic
    null_indices = random.sample(range(n_records), k=int(n_records * 0.03))
    for idx in null_indices:
        records[idx]["actual_amount"] = None

    df = pd.DataFrame(records)
    _save_raw(df, "transactions")
    logger.info(f"Generated {len(df)} transaction records across {len(countries)} countries")
    return df


# ---------------------------------------------------------------------------
# 3. Budget Plan Extraction
# ---------------------------------------------------------------------------

def load_budget_plan() -> pd.DataFrame:
    """
    Loads the annual budget plan from a CSV file.
    In production this would come from the finance team's Excel/SharePoint.
    We create a realistic CSV the first time this runs.
    """
    budget_path = RAW_DIR.parents[0] / "budget_plan.csv"

    if not budget_path.exists():
        _create_sample_budget_csv(budget_path)

    df = pd.read_csv(budget_path)
    logger.info(f"Loaded budget plan: {len(df)} line items")
    return df


def _create_sample_budget_csv(path: Path) -> None:
    """Creates a sample annual budget CSV for demonstration."""
    programs = ["Bicycle Procurement", "Field Logistics", "Training Programs",
                "Community Outreach", "Admin & Overhead", "Donor Projects"]
    quarters = ["Q1", "Q2", "Q3", "Q4"]

    rows = []
    for program in programs:
        for quarter in quarters:
            rows.append({
                "fiscal_year": 2024,
                "quarter": quarter,
                "program": program,
                "budgeted_usd": round(random.uniform(20000, 200000), 2),
                "approved_by": "Finance Committee",
                "approval_date": "2024-01-15",
            })

    pd.DataFrame(rows).to_csv(path, index=False)
    logger.info(f"Created sample budget plan at {path}")


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _save_raw(df: pd.DataFrame, name: str) -> None:
    """Saves raw extract to timestamped CSV for audit trail."""
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    path = RAW_DIR / f"{name}_{timestamp}.csv"
    df.to_csv(path, index=False)
    logger.info(f"Raw extract saved → {path}")


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
    print("\n=== Testing Extraction Layer ===")

    print("\n[1/3] Fetching FX rates...")
    fx = fetch_fx_rates()
    print(fx[["target_currency", "rate", "source"]].to_string(index=False))

    print("\n[2/3] Generating transaction records...")
    txns = generate_transactions(n_records=50)
    print(f"  Shape: {txns.shape}")
    print(txns[["transaction_id", "date", "country", "currency", "actual_amount"]].head(5).to_string(index=False))

    print("\n[3/3] Loading budget plan...")
    budget = load_budget_plan()
    print(budget.head(5).to_string(index=False))

    print("\n✓ Extraction layer OK")
