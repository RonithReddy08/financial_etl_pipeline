-- =============================================================================
-- schema.sql — Three-layer Financial Data Warehouse
-- =============================================================================
-- Architecture follows the medallion pattern used in production data warehouses:
--
--   raw_*       → Raw ingest, no transformation, preserves original data
--   staging_*   → Cleaned and typed, still row-level
--   mart_*      → Aggregated KPI tables, optimised for BI tools
--
-- Why three layers?
--   • Raw layer lets you re-run transforms without re-hitting the API
--   • Staging layer is what Power BI / Tableau connects to for row-level drills
--   • Mart layer pre-aggregates common queries so dashboards are fast
-- =============================================================================

-- ---------------------------------------------------------------------------
-- Setup
-- ---------------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS financial_etl;
SET search_path TO financial_etl;

-- ---------------------------------------------------------------------------
-- RAW LAYER — untouched extracts
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS raw_transactions (
    id                  SERIAL PRIMARY KEY,
    transaction_id      VARCHAR(20),
    date                VARCHAR(20),            -- raw string, parsed in staging
    country             VARCHAR(100),
    currency            VARCHAR(10),
    program             VARCHAR(100),
    cost_center         VARCHAR(100),
    vendor              VARCHAR(100),
    budgeted_amount     NUMERIC(15, 2),
    actual_amount       NUMERIC(15, 2),         -- may be NULL (handled in transform)
    description         TEXT,
    notes               TEXT,
    ingested_at         TIMESTAMPTZ DEFAULT NOW(),
    pipeline_run_id     VARCHAR(50)             -- links row to a specific pipeline run
);

CREATE TABLE IF NOT EXISTS raw_fx_rates (
    id                  SERIAL PRIMARY KEY,
    base_currency       VARCHAR(10),
    target_currency     VARCHAR(10),
    rate                NUMERIC(18, 6),
    fetched_at          TIMESTAMPTZ,
    source              VARCHAR(50),            -- 'alpha_vantage' or 'fallback'
    ingested_at         TIMESTAMPTZ DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- STAGING LAYER — cleaned, typed, USD-normalised
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS staging_transactions (
    transaction_id          VARCHAR(20) PRIMARY KEY,
    date                    DATE NOT NULL,
    country                 VARCHAR(100) NOT NULL,
    currency                VARCHAR(10) NOT NULL,
    program                 VARCHAR(100) NOT NULL,
    cost_center             VARCHAR(100),
    vendor                  VARCHAR(100),

    -- Original currency amounts
    budgeted_amount         NUMERIC(15, 2) NOT NULL,
    actual_amount           NUMERIC(15, 2) NOT NULL,
    amount_imputed          BOOLEAN DEFAULT FALSE,  -- TRUE if null was filled

    -- USD-normalised amounts
    fx_rate_to_usd          NUMERIC(18, 6),
    budgeted_amount_usd     NUMERIC(15, 2) NOT NULL,
    actual_amount_usd       NUMERIC(15, 2) NOT NULL,

    -- KPIs
    variance_usd            NUMERIC(15, 2),
    variance_pct            NUMERIC(8, 2),
    budget_utilization      NUMERIC(8, 2),
    is_overspend            BOOLEAN,

    -- Time dimensions
    fiscal_year             SMALLINT,
    fiscal_quarter          VARCHAR(2),
    month                   VARCHAR(7),

    -- Metadata
    cleaned_at              TIMESTAMPTZ,
    loaded_at               TIMESTAMPTZ DEFAULT NOW(),
    pipeline_run_id         VARCHAR(50)
);

-- Index for common filter patterns in BI tools
CREATE INDEX IF NOT EXISTS idx_stg_date         ON staging_transactions(date);
CREATE INDEX IF NOT EXISTS idx_stg_country       ON staging_transactions(country);
CREATE INDEX IF NOT EXISTS idx_stg_program       ON staging_transactions(program);
CREATE INDEX IF NOT EXISTS idx_stg_fiscal        ON staging_transactions(fiscal_year, fiscal_quarter);
CREATE INDEX IF NOT EXISTS idx_stg_overspend     ON staging_transactions(is_overspend);

-- ---------------------------------------------------------------------------
-- MART LAYER — pre-aggregated for BI dashboards
-- ---------------------------------------------------------------------------

-- Monthly KPI summary (main dashboard source)
CREATE TABLE IF NOT EXISTS mart_monthly_kpis (
    id                      SERIAL PRIMARY KEY,
    fiscal_year             SMALLINT NOT NULL,
    month                   VARCHAR(7) NOT NULL,
    program                 VARCHAR(100) NOT NULL,
    country                 VARCHAR(100) NOT NULL,

    total_budgeted_usd      NUMERIC(15, 2),
    total_actual_usd        NUMERIC(15, 2),
    total_variance_usd      NUMERIC(15, 2),
    avg_variance_pct        NUMERIC(8, 2),
    avg_budget_utilization  NUMERIC(8, 2),
    transaction_count       INTEGER,
    overspend_count         INTEGER,
    overspend_pct           NUMERIC(8, 2),        -- % of transactions that overspent

    refreshed_at            TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(fiscal_year, month, program, country)
);

-- Program-level quarterly rollup
CREATE TABLE IF NOT EXISTS mart_program_quarterly (
    id                      SERIAL PRIMARY KEY,
    fiscal_year             SMALLINT NOT NULL,
    fiscal_quarter          VARCHAR(2) NOT NULL,
    program                 VARCHAR(100) NOT NULL,

    total_budgeted_usd      NUMERIC(15, 2),
    total_actual_usd        NUMERIC(15, 2),
    total_variance_usd      NUMERIC(15, 2),
    variance_pct            NUMERIC(8, 2),
    budget_utilization      NUMERIC(8, 2),
    countries_count         INTEGER,
    transaction_count       INTEGER,

    refreshed_at            TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(fiscal_year, fiscal_quarter, program)
);

-- Country-level summary (geographic drilldown)
CREATE TABLE IF NOT EXISTS mart_country_summary (
    id                      SERIAL PRIMARY KEY,
    fiscal_year             SMALLINT NOT NULL,
    country                 VARCHAR(100) NOT NULL,

    primary_currency        VARCHAR(10),
    total_budgeted_usd      NUMERIC(15, 2),
    total_actual_usd        NUMERIC(15, 2),
    total_variance_usd      NUMERIC(15, 2),
    avg_variance_pct        NUMERIC(8, 2),
    transaction_count       INTEGER,
    programs_count          INTEGER,

    refreshed_at            TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(fiscal_year, country)
);

-- Pipeline run log (audit + alerting)
CREATE TABLE IF NOT EXISTS pipeline_run_log (
    run_id                  VARCHAR(50) PRIMARY KEY,
    started_at              TIMESTAMPTZ NOT NULL,
    completed_at            TIMESTAMPTZ,
    status                  VARCHAR(20),          -- 'running', 'success', 'failed'
    rows_extracted          INTEGER,
    rows_loaded_staging     INTEGER,
    rows_loaded_mart        INTEGER,
    warnings_count          INTEGER,
    error_message           TEXT,
    duration_seconds        NUMERIC(8, 2)
);

-- ---------------------------------------------------------------------------
-- MART REFRESH VIEWS (called after each load)
-- ---------------------------------------------------------------------------

-- These INSERT ... SELECT statements refresh the mart tables.
-- Called by load.py after staging upsert is complete.

-- Refresh monthly KPI mart
CREATE OR REPLACE PROCEDURE refresh_mart_monthly_kpis()
LANGUAGE plpgsql AS $$
BEGIN
    DELETE FROM mart_monthly_kpis;
    INSERT INTO mart_monthly_kpis (
        fiscal_year, month, program, country,
        total_budgeted_usd, total_actual_usd, total_variance_usd,
        avg_variance_pct, avg_budget_utilization,
        transaction_count, overspend_count, overspend_pct
    )
    SELECT
        fiscal_year,
        month,
        program,
        country,
        ROUND(SUM(budgeted_amount_usd), 2),
        ROUND(SUM(actual_amount_usd), 2),
        ROUND(SUM(variance_usd), 2),
        ROUND(AVG(variance_pct), 2),
        ROUND(AVG(budget_utilization), 2),
        COUNT(*),
        SUM(CASE WHEN is_overspend THEN 1 ELSE 0 END),
        ROUND(100.0 * SUM(CASE WHEN is_overspend THEN 1 ELSE 0 END) / COUNT(*), 2)
    FROM staging_transactions
    GROUP BY fiscal_year, month, program, country;
END;
$$;

-- Refresh quarterly program mart
CREATE OR REPLACE PROCEDURE refresh_mart_program_quarterly()
LANGUAGE plpgsql AS $$
BEGIN
    DELETE FROM mart_program_quarterly;
    INSERT INTO mart_program_quarterly (
        fiscal_year, fiscal_quarter, program,
        total_budgeted_usd, total_actual_usd, total_variance_usd,
        variance_pct, budget_utilization, countries_count, transaction_count
    )
    SELECT
        fiscal_year,
        fiscal_quarter,
        program,
        ROUND(SUM(budgeted_amount_usd), 2),
        ROUND(SUM(actual_amount_usd), 2),
        ROUND(SUM(variance_usd), 2),
        ROUND(100.0 * (SUM(actual_amount_usd) - SUM(budgeted_amount_usd))
              / NULLIF(SUM(budgeted_amount_usd), 0), 2),
        ROUND(100.0 * SUM(actual_amount_usd) / NULLIF(SUM(budgeted_amount_usd), 0), 2),
        COUNT(DISTINCT country),
        COUNT(*)
    FROM staging_transactions
    GROUP BY fiscal_year, fiscal_quarter, program;
END;
$$;

-- Refresh country summary mart
CREATE OR REPLACE PROCEDURE refresh_mart_country_summary()
LANGUAGE plpgsql AS $$
BEGIN
    DELETE FROM mart_country_summary;
    INSERT INTO mart_country_summary (
        fiscal_year, country, primary_currency,
        total_budgeted_usd, total_actual_usd, total_variance_usd,
        avg_variance_pct, transaction_count, programs_count
    )
    SELECT
        fiscal_year,
        country,
        MODE() WITHIN GROUP (ORDER BY currency),
        ROUND(SUM(budgeted_amount_usd), 2),
        ROUND(SUM(actual_amount_usd), 2),
        ROUND(SUM(variance_usd), 2),
        ROUND(AVG(variance_pct), 2),
        COUNT(*),
        COUNT(DISTINCT program)
    FROM staging_transactions
    GROUP BY fiscal_year, country;
END;
$$;
