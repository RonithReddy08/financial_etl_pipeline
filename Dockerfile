# =============================================================================
# Dockerfile — Containerised ETL Pipeline
# =============================================================================
# Why Docker for a portfolio project?
#   - Proves the pipeline runs on ANY machine, not just yours
#   - Interviewers can clone + docker-compose up and see it work immediately
#   - Matches how real data pipelines are deployed in production
#
# Usage:
#   docker build -t financial-etl .
#   docker run --env-file .env financial-etl --dry-run
#   docker run --env-file .env financial-etl --records 500
#   docker-compose up   (runs pipeline + postgres together)
# =============================================================================

FROM python:3.11-slim

# Metadata
LABEL maintainer="Ronith Reddy"
LABEL description="Automated Financial ETL Pipeline"
LABEL version="1.0"

# Set working directory
WORKDIR /app

# Install system dependencies (needed for psycopg2)
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first (layer caching: deps change less than code)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY src/     ./src/
COPY sql/     ./sql/
COPY tests/   ./tests/

# Create directories the pipeline writes to
RUN mkdir -p data/raw data/clean logs

# Non-root user for security (best practice)
RUN useradd --create-home --shell /bin/bash etl_user
RUN chown -R etl_user:etl_user /app
USER etl_user

# Default command: run pipeline once
# Override with: docker run financial-etl --schedule
ENTRYPOINT ["python", "src/pipeline.py"]
CMD ["--dry-run", "--records", "500"]
