# Financial ETL Pipeline 💰→🗄️→📊

**End-to-end automated financial data pipeline** — pulls multi-currency transaction data from REST APIs, cleans and normalises it in Python, loads it into PostgreSQL, and triggers a scheduled Power BI/Tableau dashboard refresh.

> Built to demonstrate production-style data engineering skills, inspired by multi-currency financial reporting work at World Bicycle Relief across 6 African countries.

---

## What it does

```
Alpha Vantage API  ─┐
CSV / Excel files  ─┼─► Python (pandas) ─► PostgreSQL ─► Power BI / Tableau
Simulated Txns     ─┘   clean, normalize   3-layer DWH    auto refresh
                         KPIs, validate
```

**The pipeline runs on a daily schedule and:**
1. Fetches live FX exchange rates (USD ↔ KES, ZMW, ZAR, GHS, UGX)
2. Ingests multi-currency financial transaction records
3. Cleans nulls, removes duplicates, fixes types, imputes missing values
4. Normalises all amounts to USD using live exchange rates
5. Calculates KPIs: budget variance %, burn rate, overspend flags
6. Loads raw → staging → mart tables in PostgreSQL (idempotent upserts)
7. Detects anomalies (>20% program overspend, large transactions)
8. Sends email alerts for flagged anomalies
9. Triggers a Power BI dataset refresh via REST API

---

## Tech stack

| Layer | Technology |
|---|---|
| Language | Python 3.11 |
| Data manipulation | pandas, numpy |
| Database | PostgreSQL (SQLAlchemy ORM) |
| Scheduling | Python `schedule` library (swap for Airflow in prod) |
| BI refresh | Power BI REST API / Tableau REST API |
| API source | Alpha Vantage (free tier) |
| Testing | pytest (25 automated tests) |
| Config | python-dotenv (.env file) |

---

## Project structure

```
financial_etl_pipeline/
├── src/
│   ├── extract.py       # Pull data from APIs + generate realistic transactions
│   ├── transform.py     # Clean, normalize currencies, calculate KPIs, validate
│   ├── load.py          # Load to PostgreSQL (raw → staging → mart upsert)
│   └── pipeline.py      # Orchestrator: chains all layers + scheduling + alerts
├── sql/
│   └── schema.sql       # Three-layer warehouse schema + mart refresh procedures
├── tests/
│   └── test_pipeline.py # 25 automated tests for each pipeline layer
├── data/
│   ├── raw/             # Timestamped raw extracts (audit trail)
│   └── clean/           # Clean outputs before DB load
├── logs/                # Per-run log files
├── .env.example         # Template for credentials (never commit .env)
├── requirements.txt
└── README.md
```

---

## Quick start

### 1. Clone and install

```bash
git clone https://github.com/yourusername/financial-etl-pipeline.git
cd financial-etl-pipeline
pip install -r requirements.txt
```

### 2. Configure credentials

```bash
cp .env.example .env
# Edit .env with your PostgreSQL and Alpha Vantage credentials
```

Minimum required in `.env`:
```
DB_HOST=localhost
DB_PORT=5432
DB_NAME=financial_pipeline
DB_USER=postgres
DB_PASSWORD=your_password
ALPHA_VANTAGE_KEY=your_free_api_key   # from alphavantage.co
```

### 3. Set up PostgreSQL

```bash
# Create the database
createdb financial_pipeline

# Run the schema (creates all tables and procedures)
psql -d financial_pipeline -f sql/schema.sql
```

### 4. Run the pipeline

```bash
# Run once (immediate)
python src/pipeline.py

# Dry-run (transform only, no DB write — great for testing)
python src/pipeline.py --dry-run

# Run on daily schedule (blocks — runs every day at 06:00 UTC)
python src/pipeline.py --schedule

# Custom record count (for testing)
python src/pipeline.py --dry-run --records 100
```

### 5. Run tests

```bash
pytest tests/ -v
# Expected: 25 tests, all passing
```

---

## Database schema

The warehouse uses a three-layer **medallion architecture**:

| Layer | Table | Description |
|---|---|---|
| Raw | `raw_transactions` | Append-only ingest, no transformation |
| Raw | `raw_fx_rates` | FX rate snapshots |
| Staging | `staging_transactions` | Cleaned, USD-normalised, KPIs computed |
| Mart | `mart_monthly_kpis` | Monthly rollup by program + country |
| Mart | `mart_program_quarterly` | Quarterly program performance |
| Mart | `mart_country_summary` | Annual country-level summary |
| Audit | `pipeline_run_log` | Run history, row counts, durations |

**Connect Power BI / Tableau to the `mart_*` tables** for pre-aggregated, fast-refreshing dashboards.

---

## Power BI setup

1. In Power BI Desktop, connect to PostgreSQL → `financial_etl` schema
2. Import `mart_monthly_kpis`, `mart_program_quarterly`, `mart_country_summary`
3. Build your visuals (suggested: budget variance waterfall, country map, program burn rate)
4. Publish to Power BI Service (workspace)
5. In Azure, create an App Registration and add it to your workspace as a Member
6. Add the credentials to `.env` (see `POWERBI_*` vars in `.env.example`)
7. The pipeline will now automatically refresh your dataset after every run

---

## Key design decisions (interview talking points)

**Why upsert (not truncate-and-reload)?**
Running the pipeline twice on the same data should be safe. Upsert on `transaction_id` ensures idempotency — you can re-run after a failure without duplicating data.

**Why three DB layers (raw / staging / mart)?**
The raw layer preserves original data for audit and replay. The staging layer is the "source of truth" for row-level analysis. The mart layer pre-aggregates for fast BI queries — dashboards don't need to scan 500k rows per refresh.

**Why program-median imputation (not mean) for null amounts?**
Financial data often has large outliers (a single $200k procurement vs. many $5k logistics entries). The mean would be pulled high by outliers; the median imputes a more representative value for the program.

**Why Python `schedule` instead of Airflow?**
For a portfolio project, `schedule` is a zero-infrastructure solution that demonstrates the concept clearly. In production I'd use Airflow (DAG-based, retry logic, monitoring UI) or AWS EventBridge for the same trigger.

---

## Connecting to Snowflake (one-line swap)

In `load.py`, replace the `get_engine()` connection string:

```python
# PostgreSQL (current)
conn_string = f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{dbname}"

# Snowflake (swap in)
conn_string = f"snowflake://{user}:{password}@{account}/{database}/{schema}"
```

The rest of the pipeline (SQLAlchemy calls, mart refresh) works unchanged.

---

## Sample output

```
2024-11-15 06:00:01  INFO  pipeline  — ============================================================
2024-11-15 06:00:01  INFO  pipeline  — FINANCIAL ETL PIPELINE — Run a3f8b91c
2024-11-15 06:00:01  INFO  pipeline  — Started: 2024-11-15 06:00:01 UTC
2024-11-15 06:00:01  INFO  pipeline  — Mode: PRODUCTION
2024-11-15 06:00:02  INFO  extract   — Fetched FX rates for 6 currency pairs
2024-11-15 06:00:02  INFO  extract   — Generated 500 transaction records
2024-11-15 06:00:02  INFO  transform — clean_transactions: 500 → 497 rows (3 dropped)
2024-11-15 06:00:02  INFO  transform — Imputed 15 null actual_amounts with program median
2024-11-15 06:00:02  INFO  transform — normalize_currencies: converted 497 rows across 6 currencies
2024-11-15 06:00:02  INFO  transform — calculate_kpis: 163 overspend transactions (32.8% of total)
2024-11-15 06:00:03  INFO  load      — Upserted 497 rows → staging_transactions
2024-11-15 06:00:03  INFO  load      — Refreshed mart: refresh_mart_monthly_kpis
2024-11-15 06:00:04  INFO  pipeline  — PIPELINE COMPLETE ✓  (2.8s)
```

## Author

Ronith Reddy | [LinkedIn](https://linkedin.com/in/ronithreddyy) | [GitHub](https://github.com/RonithReddy08)
