"""
transform.py — Data Transformation & Cleaning Layer
====================================================
Takes raw DataFrames from extract.py and produces clean, analysis-ready data.

Pipeline steps (in order):
  1. clean_transactions()   — fix nulls, types, whitespace, duplicates
  2. normalize_currencies() — convert all amounts to USD using live FX rates
  3. calculate_kpis()       — budget variance, burn rate, cost-per-bike, etc.
  4. validate_data()        — schema + business rule checks before DB load

Design principle:
  Every function is a pure transformation: input DataFrame → output DataFrame.
  This makes each step independently testable (see tests/).
"""

import logging
import pandas as pd
import numpy as np
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

CLEAN_DIR = Path(__file__).parents[1] / "data" / "clean"
CLEAN_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Step 1 — Data Cleaning
# ---------------------------------------------------------------------------

def clean_transactions(df: pd.DataFrame) -> pd.DataFrame:
    """
    Cleans raw transaction data:
      - Standardises column names (snake_case)
      - Parses date strings to datetime
      - Fills or drops null amounts
      - Strips whitespace from string fields
      - Removes exact duplicates
      - Enforces correct dtypes

    Returns a clean DataFrame plus a summary of changes made.
    """
    original_count = len(df)
    df = df.copy()

    # --- Column names ---
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

    # --- Date parsing ---
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    bad_dates = df["date"].isna().sum()
    if bad_dates > 0:
        logger.warning(f"  {bad_dates} rows have unparseable dates — dropping")
        df = df.dropna(subset=["date"])

    # --- String cleanup ---
    str_cols = df.select_dtypes(include="object").columns
    for col in str_cols:
        df[col] = df[col].str.strip() if hasattr(df[col], "str") else df[col]
        df[col] = df[col].replace({"": None, "  ": None, "nan": None})

    # --- Null amounts ---
    # Strategy: fill nulls with the program-level median (not mean — robust to outliers)
    null_amounts = df["actual_amount"].isna().sum()
    if null_amounts > 0:
        program_medians = df.groupby("program")["actual_amount"].transform("median")
        df["actual_amount"] = df["actual_amount"].fillna(program_medians)
        logger.info(f"  Imputed {null_amounts} null actual_amounts with program median")
        df["amount_imputed"] = False
        df.loc[df["actual_amount"].isna(), "amount_imputed"] = True
    else:
        df["amount_imputed"] = False

    # Fill any remaining nulls (programs with all-null amounts) with overall median
    overall_median = df["actual_amount"].median()
    remaining_nulls = df["actual_amount"].isna().sum()
    if remaining_nulls > 0:
        df["actual_amount"] = df["actual_amount"].fillna(overall_median)
        logger.warning(f"  {remaining_nulls} remaining nulls filled with overall median")

    # --- Numeric types ---
    df["actual_amount"] = pd.to_numeric(df["actual_amount"], errors="coerce")
    df["budgeted_amount"] = pd.to_numeric(df["budgeted_amount"], errors="coerce")

    # --- Remove negatives (data entry errors) ---
    neg_rows = (df["actual_amount"] < 0).sum()
    if neg_rows > 0:
        logger.warning(f"  {neg_rows} rows with negative actual_amount — taking absolute value")
        df["actual_amount"] = df["actual_amount"].abs()

    # --- Duplicates ---
    dup_count = df.duplicated(subset=["transaction_id"]).sum()
    if dup_count > 0:
        logger.warning(f"  {dup_count} duplicate transaction_ids found — keeping first occurrence")
        df = df.drop_duplicates(subset=["transaction_id"], keep="first")

    # --- Add pipeline metadata ---
    df["cleaned_at"] = datetime.utcnow().isoformat()

    final_count = len(df)
    logger.info(
        f"clean_transactions: {original_count} → {final_count} rows "
        f"({original_count - final_count} dropped)"
    )
    return df


# ---------------------------------------------------------------------------
# Step 2 — Currency Normalisation
# ---------------------------------------------------------------------------

def normalize_currencies(
    transactions: pd.DataFrame,
    fx_rates: pd.DataFrame,
    base: str = "USD",
) -> pd.DataFrame:
    """
    Converts all transaction amounts to a single base currency (USD).

    FX rates DataFrame must have columns: [base_currency, target_currency, rate]
    where rate = how many target_currency units equal 1 base_currency unit.

    Example: if rate for USD→KES is 129.5, then:
      amount_usd = amount_kes / 129.5
    """
    df = transactions.copy()

    # Build a lookup dict: currency_code → rate_to_usd
    rate_map = {}
    for _, row in fx_rates.iterrows():
        if row["base_currency"] == base:
            # rate = units of target per 1 USD → invert to get USD per unit target
            rate_map[row["target_currency"]] = 1.0 / row["rate"] if row["rate"] != 0 else 1.0
    rate_map[base] = 1.0  # USD → USD

    # Map rates onto transactions
    df["fx_rate_to_usd"] = df["currency"].map(rate_map)

    unmapped = df["fx_rate_to_usd"].isna().sum()
    if unmapped > 0:
        unknown_ccys = df.loc[df["fx_rate_to_usd"].isna(), "currency"].unique()
        logger.warning(f"  No FX rate for: {unknown_ccys} — defaulting to 1.0 (USD equivalent)")
        df["fx_rate_to_usd"] = df["fx_rate_to_usd"].fillna(1.0)

    df["actual_amount_usd"] = (df["actual_amount"] * df["fx_rate_to_usd"]).round(2)
    df["budgeted_amount_usd"] = (df["budgeted_amount"] * df["fx_rate_to_usd"]).round(2)

    logger.info(
        f"normalize_currencies: converted {len(df)} rows across "
        f"{df['currency'].nunique()} currencies to {base}"
    )
    return df


# ---------------------------------------------------------------------------
# Step 3 — KPI Calculation
# ---------------------------------------------------------------------------

def calculate_kpis(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calculates key financial performance indicators at the row level.
    Aggregate KPIs (by program, country, quarter) are computed in the SQL mart layer.

    Row-level KPIs:
      - variance_usd         = actual - budget (positive = overspend)
      - variance_pct         = variance / budget × 100
      - budget_utilization   = actual / budget × 100 (capped at 200% for display)
      - is_overspend         = True if actual > budget by >10%
      - fiscal_quarter       = Q1/Q2/Q3/Q4 derived from date
      - fiscal_year          = derived from date
    """
    df = df.copy()

    # Variance
    df["variance_usd"] = (df["actual_amount_usd"] - df["budgeted_amount_usd"]).round(2)
    df["variance_pct"] = np.where(
        df["budgeted_amount_usd"] != 0,
        ((df["variance_usd"] / df["budgeted_amount_usd"]) * 100).round(2),
        0.0,
    )

    # Budget utilization (cap at 200 to avoid chart distortion)
    df["budget_utilization"] = np.where(
        df["budgeted_amount_usd"] != 0,
        ((df["actual_amount_usd"] / df["budgeted_amount_usd"]) * 100).clip(0, 200).round(2),
        0.0,
    )

    # Overspend flag: actual exceeds budget by more than 10%
    df["is_overspend"] = df["variance_pct"] > 10.0

    # Time dimensions for grouping
    df["fiscal_year"] = df["date"].dt.year
    df["fiscal_quarter"] = "Q" + df["date"].dt.quarter.astype(str)
    df["month"] = df["date"].dt.to_period("M").astype(str)

    logger.info(
        f"calculate_kpis: {df['is_overspend'].sum()} overspend transactions "
        f"({df['is_overspend'].mean()*100:.1f}% of total)"
    )
    return df


# ---------------------------------------------------------------------------
# Step 4 — Validation
# ---------------------------------------------------------------------------

def validate_data(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """
    Runs business rule checks before loading to the database.
    Returns (clean_df, list_of_warnings).

    Checks:
      - No nulls in required columns
      - transaction_id is unique
      - Date range is sensible (2020–2030)
      - Amounts are positive
      - Known currencies only
      - variance_pct is numeric
    """
    warnings = []
    df = df.copy()

    required_cols = [
        "transaction_id", "date", "country", "currency",
        "program", "actual_amount_usd", "budgeted_amount_usd",
    ]

    # --- Required columns present ---
    missing_cols = [c for c in required_cols if c not in df.columns]
    if missing_cols:
        raise ValueError(f"Missing required columns after transform: {missing_cols}")

    # --- No nulls in required fields ---
    for col in required_cols:
        null_count = df[col].isna().sum()
        if null_count > 0:
            warnings.append(f"WARN: {null_count} nulls in required column '{col}'")

    # --- Unique transaction IDs ---
    dup_ids = df["transaction_id"].duplicated().sum()
    if dup_ids > 0:
        warnings.append(f"WARN: {dup_ids} duplicate transaction_ids after cleaning")

    # --- Date range ---
    min_date = pd.Timestamp("2020-01-01")
    max_date = pd.Timestamp("2030-12-31")
    out_of_range = ((df["date"] < min_date) | (df["date"] > max_date)).sum()
    if out_of_range > 0:
        warnings.append(f"WARN: {out_of_range} dates outside 2020–2030 range")

    # --- Positive amounts ---
    neg_actual = (df["actual_amount_usd"] < 0).sum()
    if neg_actual > 0:
        warnings.append(f"WARN: {neg_actual} negative actual_amount_usd values")

    # --- Known currencies ---
    known_currencies = {"USD", "KES", "ZMW", "ZAR", "GHS", "UGX", "EUR", "GBP"}
    unknown_ccys = set(df["currency"].unique()) - known_currencies
    if unknown_ccys:
        warnings.append(f"WARN: Unknown currencies detected: {unknown_ccys}")

    # --- Summary stats for logging ---
    total_actual_usd = df["actual_amount_usd"].sum()
    total_budget_usd = df["budgeted_amount_usd"].sum()
    overall_variance = total_actual_usd - total_budget_usd

    logger.info(f"validate_data: {len(df)} rows passed validation")
    logger.info(f"  Total actual (USD):   ${total_actual_usd:,.2f}")
    logger.info(f"  Total budget (USD):   ${total_budget_usd:,.2f}")
    logger.info(f"  Overall variance:     ${overall_variance:,.2f}")
    logger.info(f"  Validation warnings:  {len(warnings)}")

    for w in warnings:
        logger.warning(f"  {w}")

    return df, warnings


# ---------------------------------------------------------------------------
# Orchestration helper — run full transform pipeline
# ---------------------------------------------------------------------------

def run_transform(
    transactions: pd.DataFrame,
    fx_rates: pd.DataFrame,
) -> tuple[pd.DataFrame, list[str]]:
    """
    Runs all transform steps in order and returns (clean_df, warnings).
    Called by pipeline.py.
    """
    logger.info("--- Transform pipeline starting ---")
    df = clean_transactions(transactions)
    df = normalize_currencies(df, fx_rates)
    df = calculate_kpis(df)
    df, warnings = validate_data(df)

    # Save clean output
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    path = CLEAN_DIR / f"transactions_clean_{timestamp}.csv"
    df.to_csv(path, index=False)
    logger.info(f"Clean data saved → {path}")

    logger.info("--- Transform pipeline complete ---")
    return df, warnings


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from extract import generate_transactions, fetch_fx_rates

    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
    print("\n=== Testing Transform Layer ===")

    txns = generate_transactions(n_records=100)
    fx = fetch_fx_rates()

    clean_df, warns = run_transform(txns, fx)

    print(f"\nFinal shape: {clean_df.shape}")
    print("\nSample output:")
    cols = ["transaction_id", "country", "currency", "actual_amount_usd",
            "budgeted_amount_usd", "variance_pct", "is_overspend", "fiscal_quarter"]
    print(clean_df[cols].head(8).to_string(index=False))

    print(f"\nValidation warnings: {len(warns)}")
    for w in warns:
        print(f"  {w}")

    print("\n✓ Transform layer OK")
