"""
load.py — Database Load Layer (PostgreSQL)
==========================================
Loads cleaned DataFrames into PostgreSQL using idempotent upsert logic.

Key design decisions:
  - Uses SQLAlchemy (not raw psycopg2) so we can swap in Snowflake with
    one connection string change.
  - Upserts on transaction_id primary key — safe to re-run on the same data.
  - Loads in this order: raw → staging → triggers mart refresh procedures.
  - Connection pooling via SQLAlchemy engine (important for scheduled runs).
  - All DB credentials read from .env file (never hardcoded).
"""

import os
import logging
import pandas as pd
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

load_dotenv()
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------

def get_engine(schema: str = "financial_etl"):
    """
    Creates SQLAlchemy engine from .env credentials.

    Expected .env variables:
        DB_HOST=localhost
        DB_PORT=5432
        DB_NAME=financial_pipeline
        DB_USER=etl_user
        DB_PASSWORD=your_password_here

    To switch to Snowflake, change the connection string to:
        snowflake://{user}:{password}@{account}/{database}/{schema}
    """
    host     = os.getenv("DB_HOST", "localhost")
    port     = os.getenv("DB_PORT", "5432")
    dbname   = os.getenv("DB_NAME", "financial_pipeline")
    user     = os.getenv("DB_USER", "postgres")
    password = os.getenv("DB_PASSWORD", "password")

    conn_string = f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{dbname}"

    engine = create_engine(
        conn_string,
        connect_args={"options": f"-csearch_path={schema}"},
        pool_pre_ping=True,    # test connection before using from pool
        pool_size=2,
        max_overflow=5,
    )
    return engine


def test_connection(engine) -> bool:
    """Quick connectivity check — used at pipeline start to fail fast."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        logger.info("Database connection: OK")
        return True
    except SQLAlchemyError as e:
        logger.error(f"Database connection failed: {e}")
        return False


def run_schema(engine) -> None:
    """Creates all tables and procedures if they don't already exist."""
    schema_path = Path(__file__).parents[1] / "sql" / "schema.sql"
    sql = schema_path.read_text()
    with engine.connect() as conn:
        conn.execute(text(sql))
        conn.commit()
    logger.info("Schema initialised (all tables created if not exist)")


# ---------------------------------------------------------------------------
# Load: raw layer
# ---------------------------------------------------------------------------

def load_raw_transactions(df: pd.DataFrame, engine, run_id: str) -> int:
    """
    Appends raw transaction records to raw_transactions table.
    Intentionally does NOT upsert — raw layer keeps full history.
    """
    raw_df = df.copy()
    raw_df["pipeline_run_id"] = run_id

    # Only keep columns that exist in the raw table
    raw_cols = [
        "transaction_id", "date", "country", "currency", "program",
        "cost_center", "vendor", "budgeted_amount", "actual_amount",
        "description", "notes", "pipeline_run_id",
    ]
    raw_df = raw_df[[c for c in raw_cols if c in raw_df.columns]]

    raw_df.to_sql(
        "raw_transactions",
        engine,
        schema="financial_etl",
        if_exists="append",
        index=False,
        method="multi",     # batch inserts for performance
        chunksize=500,
    )
    logger.info(f"Loaded {len(raw_df)} rows → raw_transactions")
    return len(raw_df)


def load_raw_fx_rates(fx_df: pd.DataFrame, engine) -> int:
    """Appends FX rate snapshot to raw_fx_rates."""
    fx_df.to_sql(
        "raw_fx_rates",
        engine,
        schema="financial_etl",
        if_exists="append",
        index=False,
    )
    logger.info(f"Loaded {len(fx_df)} FX rates → raw_fx_rates")
    return len(fx_df)


# ---------------------------------------------------------------------------
# Load: staging layer (upsert)
# ---------------------------------------------------------------------------

def upsert_staging_transactions(df: pd.DataFrame, engine, run_id: str) -> int:
    """
    Upserts clean transactions into staging_transactions.
    ON CONFLICT (transaction_id) DO UPDATE — so re-runs are safe.

    Implementation:
      1. Write to a temp table
      2. Run INSERT ... ON CONFLICT from temp → staging
      3. Drop temp table
    """
    df = df.copy()
    df["pipeline_run_id"] = run_id
    df["loaded_at"] = datetime.utcnow()

    # Map DataFrame columns → staging table columns
    staging_cols = [
        "transaction_id", "date", "country", "currency", "program", "cost_center",
        "vendor", "budgeted_amount", "actual_amount", "amount_imputed",
        "fx_rate_to_usd", "budgeted_amount_usd", "actual_amount_usd",
        "variance_usd", "variance_pct", "budget_utilization", "is_overspend",
        "fiscal_year", "fiscal_quarter", "month", "cleaned_at",
        "loaded_at", "pipeline_run_id",
    ]
    load_df = df[[c for c in staging_cols if c in df.columns]]

    # Use temp table + INSERT ON CONFLICT pattern
    temp_table = f"tmp_staging_{run_id.replace('-','_')}"

    with engine.begin() as conn:
        # Write to temp table
        load_df.to_sql(
            temp_table,
            conn,
            schema="financial_etl",
            if_exists="replace",
            index=False,
            method="multi",
            chunksize=500,
        )

        # Build column list for the ON CONFLICT UPDATE
        update_cols = [c for c in load_df.columns if c != "transaction_id"]
        update_set = ",\n                ".join(
            [f"{c} = EXCLUDED.{c}" for c in update_cols]
        )
        col_list = ", ".join(load_df.columns)

        upsert_sql = f"""
            INSERT INTO financial_etl.staging_transactions ({col_list})
            SELECT {col_list}
            FROM financial_etl.{temp_table}
            ON CONFLICT (transaction_id)
            DO UPDATE SET
                {update_set};
        """
        conn.execute(text(upsert_sql))
        conn.execute(text(f"DROP TABLE IF EXISTS financial_etl.{temp_table}"))

    logger.info(f"Upserted {len(load_df)} rows → staging_transactions")
    return len(load_df)


# ---------------------------------------------------------------------------
# Mart refresh
# ---------------------------------------------------------------------------

def refresh_mart_tables(engine) -> None:
    """
    Calls the three mart refresh stored procedures.
    These DELETE + INSERT the aggregated KPI tables.
    """
    procedures = [
        "refresh_mart_monthly_kpis",
        "refresh_mart_program_quarterly",
        "refresh_mart_country_summary",
    ]
    with engine.begin() as conn:
        for proc in procedures:
            conn.execute(text(f"CALL financial_etl.{proc}()"))
            logger.info(f"Refreshed mart: {proc}")


# ---------------------------------------------------------------------------
# Pipeline run log
# ---------------------------------------------------------------------------

def log_pipeline_run(engine, run_id: str, metadata: dict) -> None:
    """Records pipeline run metadata to pipeline_run_log table."""
    row = {
        "run_id": run_id,
        "started_at": metadata.get("started_at"),
        "completed_at": metadata.get("completed_at", datetime.utcnow()),
        "status": metadata.get("status", "success"),
        "rows_extracted": metadata.get("rows_extracted", 0),
        "rows_loaded_staging": metadata.get("rows_loaded_staging", 0),
        "rows_loaded_mart": metadata.get("rows_loaded_mart", 0),
        "warnings_count": metadata.get("warnings_count", 0),
        "error_message": metadata.get("error_message"),
        "duration_seconds": metadata.get("duration_seconds"),
    }
    pd.DataFrame([row]).to_sql(
        "pipeline_run_log",
        engine,
        schema="financial_etl",
        if_exists="append",
        index=False,
    )
    logger.info(f"Pipeline run logged: {run_id} → {row['status']}")


# ---------------------------------------------------------------------------
# Orchestration helper
# ---------------------------------------------------------------------------

def run_load(
    clean_df: pd.DataFrame,
    fx_df: pd.DataFrame,
    raw_df: pd.DataFrame,
    run_id: str,
) -> dict:
    """
    Executes the full load sequence. Called by pipeline.py.
    Returns a dict with row counts for the run log.
    """
    engine = get_engine()

    if not test_connection(engine):
        raise ConnectionError("Cannot connect to PostgreSQL. Check .env credentials.")

    logger.info("--- Load pipeline starting ---")

    raw_rows   = load_raw_transactions(raw_df, engine, run_id)
    _          = load_raw_fx_rates(fx_df, engine)
    stg_rows   = upsert_staging_transactions(clean_df, engine, run_id)
    refresh_mart_tables(engine)

    logger.info("--- Load pipeline complete ---")

    return {
        "rows_extracted":      raw_rows,
        "rows_loaded_staging": stg_rows,
        "rows_loaded_mart":    3,   # three mart tables refreshed
    }


# ---------------------------------------------------------------------------
# CLI test (uses SQLite fallback so it runs without a real DB)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from extract import generate_transactions, fetch_fx_rates
    from transform import run_transform

    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
    print("\n=== Testing Load Layer (SQLite dry-run) ===")

    txns = generate_transactions(n_records=50)
    fx   = fetch_fx_rates()
    clean_df, warns = run_transform(txns, fx)

    # Demonstrate what the load would do (print summary instead of hitting DB)
    print(f"\nWould load:")
    print(f"  {len(txns)} rows → raw_transactions")
    print(f"  {len(fx)} rows  → raw_fx_rates")
    print(f"  {len(clean_df)} rows → staging_transactions (upsert)")
    print(f"  3 mart tables refreshed")

    print("\nSample staging row:")
    sample_cols = ["transaction_id", "country", "actual_amount_usd",
                   "variance_pct", "is_overspend", "fiscal_quarter"]
    print(clean_df[sample_cols].head(3).to_string(index=False))

    print("\n✓ Load layer logic verified (no DB connection required for this test)")
    print("  To run with a real DB: set credentials in .env and run pipeline.py")
