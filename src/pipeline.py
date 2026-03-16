"""
pipeline.py — End-to-End Pipeline Orchestrator
===============================================
Chains Extract → Transform → Load → BI Refresh into a single run_pipeline() call.

Features:
  - Unique run_id per execution (for audit trail)
  - Structured logging to file + console
  - Email alert on anomalies (budget overruns >20%)
  - Power BI REST API refresh trigger after successful load
  - Full error handling with run log entry on failure

Usage:
  python pipeline.py              # run once immediately
  python pipeline.py --schedule   # run on cron schedule (daily at 6am)
  python pipeline.py --dry-run    # transform only, skip DB load
"""

import os
import sys
import uuid
import logging
import smtplib
import argparse
import requests
import schedule
import time
from datetime import datetime
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv

# Add src to path so we can import our modules
sys.path.insert(0, str(Path(__file__).parent))

from extract import fetch_fx_rates, generate_transactions, load_budget_plan
from transform import run_transform
from load import run_load, get_engine, log_pipeline_run

load_dotenv()

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

LOG_DIR = Path(__file__).parents[1] / "logs"
LOG_DIR.mkdir(exist_ok=True)

def setup_logging(run_id: str) -> None:
    """Configure console + file logging for this run."""
    log_file = LOG_DIR / f"pipeline_{run_id}.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  —  %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file),
        ],
    )
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Anomaly detection + alerting
# ---------------------------------------------------------------------------

def detect_anomalies(clean_df) -> list[dict]:
    """
    Identifies financially significant anomalies that warrant an alert:
      - Programs with >20% overspend
      - Single transactions >$50k USD
      - Countries with >5 overspend transactions in the run
    """
    anomalies = []

    # Program-level overspend
    program_stats = clean_df.groupby("program").agg(
        avg_variance_pct=("variance_pct", "mean"),
        overspend_count=("is_overspend", "sum"),
    ).reset_index()

    for _, row in program_stats.iterrows():
        if row["avg_variance_pct"] > 20:
            anomalies.append({
                "type": "program_overspend",
                "detail": f"{row['program']}: avg variance {row['avg_variance_pct']:.1f}%",
                "severity": "HIGH" if row["avg_variance_pct"] > 30 else "MEDIUM",
            })

    # Large single transactions
    large_txns = clean_df[clean_df["actual_amount_usd"] > 50_000]
    for _, row in large_txns.iterrows():
        anomalies.append({
            "type": "large_transaction",
            "detail": f"TXN {row['transaction_id']}: ${row['actual_amount_usd']:,.0f} USD — {row['program']}",
            "severity": "INFO",
        })

    # Country-level overspend
    country_overspend = clean_df.groupby("country")["is_overspend"].sum()
    for country, count in country_overspend.items():
        if count > 5:
            anomalies.append({
                "type": "country_overspend_cluster",
                "detail": f"{country}: {count} overspend transactions",
                "severity": "MEDIUM",
            })

    return anomalies


def send_alert_email(anomalies: list[dict], run_id: str) -> None:
    """
    Sends an HTML email summary of detected anomalies.
    Credentials read from .env: SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD,
    ALERT_FROM_EMAIL, ALERT_TO_EMAIL.
    """
    smtp_host     = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port     = int(os.getenv("SMTP_PORT", "587"))
    smtp_user     = os.getenv("SMTP_USER", "")
    smtp_password = os.getenv("SMTP_PASSWORD", "")
    from_email    = os.getenv("ALERT_FROM_EMAIL", smtp_user)
    to_email      = os.getenv("ALERT_TO_EMAIL", "")

    if not all([smtp_user, smtp_password, to_email]):
        logger.warning("Email alert skipped — SMTP credentials not configured in .env")
        return

    # Build HTML email body
    severity_colors = {"HIGH": "#dc2626", "MEDIUM": "#d97706", "INFO": "#2563eb"}
    rows_html = ""
    for a in anomalies:
        color = severity_colors.get(a["severity"], "#6b7280")
        rows_html += f"""
        <tr>
            <td style="padding:8px;border-bottom:1px solid #e5e7eb;">
                <span style="background:{color};color:white;padding:2px 8px;border-radius:4px;font-size:11px;">
                    {a['severity']}
                </span>
            </td>
            <td style="padding:8px;border-bottom:1px solid #e5e7eb;">{a['type'].replace('_', ' ').title()}</td>
            <td style="padding:8px;border-bottom:1px solid #e5e7eb;">{a['detail']}</td>
        </tr>"""

    html = f"""
    <html><body style="font-family:sans-serif;color:#111;">
    <h2 style="color:#1e40af;">🔔 Financial Pipeline Alert — Run {run_id[:8]}</h2>
    <p style="color:#6b7280;">Detected {len(anomalies)} anomalies during pipeline run at {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}</p>
    <table style="border-collapse:collapse;width:100%;max-width:700px;">
        <thead>
            <tr style="background:#f3f4f6;">
                <th style="padding:8px;text-align:left;">Severity</th>
                <th style="padding:8px;text-align:left;">Type</th>
                <th style="padding:8px;text-align:left;">Detail</th>
            </tr>
        </thead>
        <tbody>{rows_html}</tbody>
    </table>
    <p style="color:#9ca3af;font-size:12px;margin-top:24px;">
        Automated alert from Financial ETL Pipeline · See logs/{run_id}.log for full details
    </p>
    </body></html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[Pipeline Alert] {len(anomalies)} financial anomalies detected — {datetime.utcnow().strftime('%Y-%m-%d')}"
    msg["From"]    = from_email
    msg["To"]      = to_email
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.sendmail(from_email, to_email, msg.as_string())
        logger.info(f"Alert email sent to {to_email}: {len(anomalies)} anomalies")
    except Exception as e:
        logger.warning(f"Alert email failed (non-critical): {e}")


# ---------------------------------------------------------------------------
# Power BI refresh trigger
# ---------------------------------------------------------------------------

def trigger_powerbi_refresh() -> bool:
    """
    Triggers a Power BI dataset refresh via the REST API.
    Requires .env: POWERBI_CLIENT_ID, POWERBI_CLIENT_SECRET,
                   POWERBI_TENANT_ID, POWERBI_WORKSPACE_ID, POWERBI_DATASET_ID

    For Tableau: swap this function with a Tableau REST API POST to
    /api/{version}/sites/{siteId}/datasources/{datasourceId}/refresh

    Returns True on success, False on failure (non-blocking).
    """
    client_id     = os.getenv("POWERBI_CLIENT_ID")
    client_secret = os.getenv("POWERBI_CLIENT_SECRET")
    tenant_id     = os.getenv("POWERBI_TENANT_ID")
    workspace_id  = os.getenv("POWERBI_WORKSPACE_ID")
    dataset_id    = os.getenv("POWERBI_DATASET_ID")

    if not all([client_id, client_secret, tenant_id, workspace_id, dataset_id]):
        logger.info("Power BI refresh skipped — credentials not configured in .env")
        logger.info("  (Set POWERBI_* vars to enable automatic dashboard refresh)")
        return False

    try:
        # Step 1: Get OAuth2 access token from Microsoft
        token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
        token_resp = requests.post(token_url, data={
            "grant_type":    "client_credentials",
            "client_id":     client_id,
            "client_secret": client_secret,
            "scope":         "https://analysis.windows.net/powerbi/api/.default",
        }, timeout=15)
        token_resp.raise_for_status()
        access_token = token_resp.json()["access_token"]

        # Step 2: POST refresh request to Power BI API
        refresh_url = (
            f"https://api.powerbi.com/v1.0/myorg/groups/{workspace_id}"
            f"/datasets/{dataset_id}/refreshes"
        )
        refresh_resp = requests.post(
            refresh_url,
            headers={"Authorization": f"Bearer {access_token}"},
            json={"notifyOption": "MailOnCompletion"},
            timeout=15,
        )
        refresh_resp.raise_for_status()

        logger.info(f"Power BI dataset refresh triggered successfully (dataset: {dataset_id})")
        return True

    except Exception as e:
        logger.warning(f"Power BI refresh failed (non-blocking): {e}")
        return False


# ---------------------------------------------------------------------------
# Core pipeline run
# ---------------------------------------------------------------------------

def run_pipeline(dry_run: bool = False, n_records: int = 500) -> dict:
    """
    Executes the full ETL pipeline end-to-end.

    Steps:
      1. Extract   — FX rates + transactions + budget plan
      2. Transform — clean, normalize currencies, calculate KPIs, validate
      3. Load      — raw → staging (upsert) → mart refresh (skipped in dry-run)
      4. Alert     — detect anomalies, send email if configured
      5. BI Refresh— trigger Power BI dataset refresh

    Returns a metadata dict with counts and status for the run log.
    """
    run_id     = str(uuid.uuid4())
    started_at = datetime.utcnow()
    setup_logging(run_id)

    logger.info("=" * 60)
    logger.info(f"FINANCIAL ETL PIPELINE — Run {run_id[:8]}")
    logger.info(f"Started: {started_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    logger.info(f"Mode: {'DRY RUN (no DB load)' if dry_run else 'PRODUCTION'}")
    logger.info("=" * 60)

    metadata = {
        "run_id":      run_id,
        "started_at":  started_at,
        "status":      "running",
    }

    try:
        # ---- EXTRACT ----
        logger.info("\n[1/4] EXTRACT")
        fx_df   = fetch_fx_rates()
        raw_df  = generate_transactions(n_records=n_records)
        budget  = load_budget_plan()

        metadata["rows_extracted"] = len(raw_df)
        logger.info(f"  Extracted {len(raw_df)} transactions, {len(fx_df)} FX rates")

        # ---- TRANSFORM ----
        logger.info("\n[2/4] TRANSFORM")
        clean_df, warnings = run_transform(raw_df, fx_df)
        metadata["warnings_count"] = len(warnings)

        logger.info(f"  {len(clean_df)} rows after transform")
        logger.info(f"  {clean_df['is_overspend'].sum()} overspend transactions detected")

        # ---- LOAD ----
        if dry_run:
            logger.info("\n[3/4] LOAD (skipped — dry-run mode)")
            metadata["rows_loaded_staging"] = 0
            metadata["rows_loaded_mart"]    = 0
        else:
            logger.info("\n[3/4] LOAD")
            load_result = run_load(clean_df, fx_df, raw_df, run_id)
            metadata.update(load_result)

        # ---- ALERT + BI REFRESH ----
        logger.info("\n[4/4] ALERTS + BI REFRESH")
        anomalies = detect_anomalies(clean_df)
        logger.info(f"  Anomalies detected: {len(anomalies)}")

        if anomalies:
            send_alert_email(anomalies, run_id)

        if not dry_run:
            trigger_powerbi_refresh()

        # ---- SUCCESS ----
        completed_at = datetime.utcnow()
        duration     = (completed_at - started_at).total_seconds()
        metadata.update({
            "status":       "success",
            "completed_at": completed_at,
            "duration_seconds": round(duration, 2),
        })

        logger.info("\n" + "=" * 60)
        logger.info(f"PIPELINE COMPLETE ✓  ({duration:.1f}s)")
        logger.info(f"  Rows extracted:   {metadata.get('rows_extracted', 0)}")
        logger.info(f"  Rows → staging:   {metadata.get('rows_loaded_staging', 0)}")
        logger.info(f"  Mart tables:      {metadata.get('rows_loaded_mart', 0)} refreshed")
        logger.info(f"  Anomalies:        {len(anomalies)}")
        logger.info(f"  Warnings:         {len(warnings)}")
        logger.info("=" * 60)

        if not dry_run:
            try:
                engine = get_engine()
                log_pipeline_run(engine, run_id, metadata)
            except Exception as e:
                logger.warning(f"Failed to write run log (non-critical): {e}")

        return metadata

    except Exception as e:
        metadata.update({
            "status":        "failed",
            "completed_at":  datetime.utcnow(),
            "error_message": str(e),
        })
        logger.error(f"PIPELINE FAILED: {e}", exc_info=True)

        if not dry_run:
            try:
                engine = get_engine()
                log_pipeline_run(engine, run_id, metadata)
            except Exception:
                pass

        raise


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

def run_scheduled(hour: int = 6, minute: int = 0) -> None:
    """
    Runs the pipeline on a daily schedule.
    Default: every day at 06:00 UTC (common for financial reporting).
    """
    logger.info(f"Scheduler started — pipeline will run daily at {hour:02d}:{minute:02d} UTC")
    logger.info("Press Ctrl+C to stop")

    schedule.every().day.at(f"{hour:02d}:{minute:02d}").do(run_pipeline)

    while True:
        schedule.run_pending()
        time.sleep(60)   # check every minute


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Financial ETL Pipeline")
    parser.add_argument(
        "--schedule", action="store_true",
        help="Run on daily schedule instead of once"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Run extract + transform only, skip DB load"
    )
    parser.add_argument(
        "--records", type=int, default=500,
        help="Number of simulated transaction records (default: 500)"
    )
    args = parser.parse_args()

    if args.schedule:
        run_scheduled()
    else:
        result = run_pipeline(dry_run=args.dry_run, n_records=args.records)
        sys.exit(0 if result["status"] == "success" else 1)
