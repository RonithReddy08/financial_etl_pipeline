"""
tests/test_pipeline.py — Automated Test Suite
==============================================
Tests each layer of the pipeline independently.
Run with: pytest tests/ -v

Why tests matter on a resume project:
  - Shows you write production-quality code, not just scripts
  - Every real data team requires tests before merging
  - Makes the project credible and reproducible
"""

import sys
import pytest
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from extract import generate_transactions, fetch_fx_rates
from transform import (
    clean_transactions,
    normalize_currencies,
    calculate_kpis,
    validate_data,
    run_transform,
)


# ===========================================================================
# Fixtures — reusable test data
# ===========================================================================

@pytest.fixture
def sample_transactions():
    """50 realistic transaction records."""
    return generate_transactions(n_records=50, seed=99)


@pytest.fixture
def sample_fx_rates():
    """FX rates (uses fallback — no API key needed in CI)."""
    return fetch_fx_rates()


@pytest.fixture
def dirty_transactions():
    """Intentionally messy data to test cleaning logic."""
    return pd.DataFrame([
        # Good row
        {"transaction_id": "TXN-001", "date": "2024-03-15", "country": "Kenya",
         "currency": "KES", "program": "Bicycle Procurement", "cost_center": "Operations",
         "vendor": "Vendor_A", "budgeted_amount": 10000, "actual_amount": 12000,
         "description": "Test", "notes": "ok"},
        # Null actual_amount
        {"transaction_id": "TXN-002", "date": "2024-04-01", "country": "Zambia",
         "currency": "ZMW", "program": "Field Logistics", "cost_center": "Programs",
         "vendor": "Vendor_B", "budgeted_amount": 5000, "actual_amount": None,
         "description": "Test", "notes": None},
        # Negative actual_amount
        {"transaction_id": "TXN-003", "date": "2024-05-01", "country": "Ghana",
         "currency": "GHS", "program": "Training Programs", "cost_center": "HR",
         "vendor": "Vendor_C", "budgeted_amount": 3000, "actual_amount": -1500,
         "description": "Test", "notes": "  "},  # whitespace-only notes
        # Duplicate transaction_id
        {"transaction_id": "TXN-001", "date": "2024-03-15", "country": "Kenya",
         "currency": "KES", "program": "Bicycle Procurement", "cost_center": "Operations",
         "vendor": "Vendor_A", "budgeted_amount": 10000, "actual_amount": 12000,
         "description": "Duplicate", "notes": ""},
        # Bad date
        {"transaction_id": "TXN-004", "date": "not-a-date", "country": "Uganda",
         "currency": "UGX", "program": "Community Outreach", "cost_center": "Finance",
         "vendor": "Vendor_D", "budgeted_amount": 8000, "actual_amount": 7500,
         "description": "Test", "notes": "approved"},
    ])


# ===========================================================================
# Extract layer tests
# ===========================================================================

class TestExtract:
    def test_generate_transactions_shape(self, sample_transactions):
        assert len(sample_transactions) == 50
        assert "transaction_id" in sample_transactions.columns
        assert "actual_amount" in sample_transactions.columns
        assert "currency" in sample_transactions.columns

    def test_transaction_ids_are_unique_before_cleaning(self, sample_transactions):
        # IDs should be unique in the generator
        assert sample_transactions["transaction_id"].nunique() == len(sample_transactions)

    def test_currencies_are_known(self, sample_transactions):
        known = {"USD", "KES", "ZMW", "ZAR", "GHS", "UGX"}
        assert set(sample_transactions["currency"].unique()).issubset(known)

    def test_fx_rates_has_required_columns(self, sample_fx_rates):
        required = {"base_currency", "target_currency", "rate", "fetched_at"}
        assert required.issubset(set(sample_fx_rates.columns))

    def test_fx_rates_positive(self, sample_fx_rates):
        assert (sample_fx_rates["rate"] > 0).all()

    def test_fx_rates_includes_usd(self, sample_fx_rates):
        usd_row = sample_fx_rates[sample_fx_rates["target_currency"] == "USD"]
        assert len(usd_row) == 1
        assert usd_row.iloc[0]["rate"] == 1.0

    def test_null_amount_injection(self):
        """Generator should inject ~3% nulls."""
        txns = generate_transactions(n_records=1000, seed=42)
        null_pct = txns["actual_amount"].isna().mean()
        assert 0.01 <= null_pct <= 0.06, f"Expected ~3% nulls, got {null_pct:.1%}"


# ===========================================================================
# Transform layer tests
# ===========================================================================

class TestCleanTransactions:
    def test_removes_bad_dates(self, dirty_transactions):
        clean = clean_transactions(dirty_transactions)
        assert clean["date"].isna().sum() == 0

    def test_fills_null_amounts(self, dirty_transactions):
        clean = clean_transactions(dirty_transactions)
        assert clean["actual_amount"].isna().sum() == 0

    def test_removes_duplicates(self, dirty_transactions):
        clean = clean_transactions(dirty_transactions)
        assert clean["transaction_id"].duplicated().sum() == 0

    def test_absolute_value_for_negatives(self, dirty_transactions):
        clean = clean_transactions(dirty_transactions)
        assert (clean["actual_amount"] >= 0).all()

    def test_whitespace_stripped(self, dirty_transactions):
        clean = clean_transactions(dirty_transactions)
        for col in clean.select_dtypes(include="object").columns:
            # No values should be pure whitespace
            mask = clean[col].notna() & (clean[col].str.strip() == "")
            assert mask.sum() == 0, f"Column {col} has whitespace-only values"

    def test_cleaned_at_added(self, dirty_transactions):
        clean = clean_transactions(dirty_transactions)
        assert "cleaned_at" in clean.columns


class TestNormalizeCurrencies:
    def test_usd_column_added(self, sample_transactions, sample_fx_rates):
        clean = clean_transactions(sample_transactions)
        normalized = normalize_currencies(clean, sample_fx_rates)
        assert "actual_amount_usd" in normalized.columns
        assert "budgeted_amount_usd" in normalized.columns
        assert "fx_rate_to_usd" in normalized.columns

    def test_usd_amounts_positive(self, sample_transactions, sample_fx_rates):
        clean = clean_transactions(sample_transactions)
        normalized = normalize_currencies(clean, sample_fx_rates)
        assert (normalized["actual_amount_usd"] >= 0).all()

    def test_usd_transactions_unchanged(self, sample_fx_rates):
        """USD rows should have the same amount before and after normalisation."""
        usd_rows = pd.DataFrame([{
            "transaction_id": "TXN-USD-001",
            "date": "2024-01-15", "country": "USA", "currency": "USD",
            "program": "Admin & Overhead", "cost_center": "Finance",
            "vendor": "Vendor_X", "budgeted_amount": 1000.0, "actual_amount": 950.0,
            "description": "Test USD row", "notes": "ok",
        }])
        clean = clean_transactions(usd_rows)
        normalized = normalize_currencies(clean, sample_fx_rates)
        assert abs(normalized.iloc[0]["actual_amount_usd"] - 950.0) < 0.01

    def test_no_nulls_after_normalisation(self, sample_transactions, sample_fx_rates):
        clean = clean_transactions(sample_transactions)
        normalized = normalize_currencies(clean, sample_fx_rates)
        assert normalized["actual_amount_usd"].isna().sum() == 0


class TestCalculateKPIs:
    def test_variance_columns_added(self, sample_transactions, sample_fx_rates):
        clean = clean_transactions(sample_transactions)
        normalized = normalize_currencies(clean, sample_fx_rates)
        kpis = calculate_kpis(normalized)
        assert "variance_usd" in kpis.columns
        assert "variance_pct" in kpis.columns
        assert "is_overspend" in kpis.columns
        assert "fiscal_quarter" in kpis.columns

    def test_variance_calculation_correct(self, sample_fx_rates):
        """Manual check: $1200 actual - $1000 budget = $200 variance (20%)."""
        row = pd.DataFrame([{
            "transaction_id": "TXN-TEST", "date": pd.Timestamp("2024-06-15"),
            "country": "USA", "currency": "USD", "program": "Admin & Overhead",
            "cost_center": "Finance", "vendor": "V", "budgeted_amount": 1000.0,
            "actual_amount": 1200.0, "description": "", "notes": "",
            "amount_imputed": False, "cleaned_at": datetime.utcnow().isoformat(),
        }])
        normalized = normalize_currencies(row, sample_fx_rates)
        kpis = calculate_kpis(normalized)
        assert abs(kpis.iloc[0]["variance_usd"] - 200.0) < 0.01
        assert abs(kpis.iloc[0]["variance_pct"] - 20.0) < 0.1

    def test_fiscal_quarter_values(self, sample_transactions, sample_fx_rates):
        clean = clean_transactions(sample_transactions)
        normalized = normalize_currencies(clean, sample_fx_rates)
        kpis = calculate_kpis(normalized)
        valid_quarters = {"Q1", "Q2", "Q3", "Q4"}
        assert set(kpis["fiscal_quarter"].unique()).issubset(valid_quarters)

    def test_is_overspend_bool(self, sample_transactions, sample_fx_rates):
        clean = clean_transactions(sample_transactions)
        normalized = normalize_currencies(clean, sample_fx_rates)
        kpis = calculate_kpis(normalized)
        assert kpis["is_overspend"].dtype == bool


class TestValidateData:
    def test_passes_clean_data(self, sample_transactions, sample_fx_rates):
        clean_df, warnings = run_transform(sample_transactions, sample_fx_rates)
        # Some warnings are expected (unknown currencies etc.) but should not raise
        assert isinstance(warnings, list)

    def test_raises_on_missing_columns(self):
        bad_df = pd.DataFrame([{"some_column": 1}])
        with pytest.raises(ValueError, match="Missing required columns"):
            validate_data(bad_df)

    def test_returns_tuple(self, sample_transactions, sample_fx_rates):
        clean = clean_transactions(sample_transactions)
        normalized = normalize_currencies(clean, sample_fx_rates)
        kpis = calculate_kpis(normalized)
        result = validate_data(kpis)
        assert isinstance(result, tuple)
        assert len(result) == 2


# ===========================================================================
# End-to-end pipeline test (no DB)
# ===========================================================================

class TestEndToEnd:
    def test_full_transform_pipeline(self, sample_transactions, sample_fx_rates):
        """Run the full transform chain and verify output shape and types."""
        clean_df, warnings = run_transform(sample_transactions, sample_fx_rates)

        # Shape: should have at most as many rows as input (some may be dropped)
        assert len(clean_df) <= len(sample_transactions)
        assert len(clean_df) > 0

        # Key columns present
        expected_cols = [
            "transaction_id", "date", "country", "currency",
            "actual_amount_usd", "budgeted_amount_usd",
            "variance_usd", "variance_pct", "is_overspend",
            "fiscal_year", "fiscal_quarter",
        ]
        for col in expected_cols:
            assert col in clean_df.columns, f"Missing column: {col}"

        # No nulls in critical columns
        for col in ["actual_amount_usd", "budgeted_amount_usd", "variance_usd"]:
            assert clean_df[col].isna().sum() == 0

    def test_deterministic_output(self):
        """Same seed should produce same results every run."""
        txns1 = generate_transactions(n_records=100, seed=7)
        txns2 = generate_transactions(n_records=100, seed=7)
        pd.testing.assert_frame_equal(txns1, txns2)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
