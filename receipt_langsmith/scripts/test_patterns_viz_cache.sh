#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# test_patterns_viz_cache.sh
#
# End-to-end development script for the patterns viz-cache Spark helper.
#
# What it does:
#   1. Resolves infrastructure names (buckets, state machine) via Pulumi
#   2. Finds the latest SUCCEEDED step-function execution (→ execution_id)
#   3. Downloads parquet traces + S3 pattern files to /tmp
#   4. Creates (or reuses) a lightweight Python venv
#   5. Runs the pytest suite against real data
#
# Prerequisites:
#   - AWS CLI configured with credentials for the dev account
#   - Pulumi CLI (only for auto-discovery; skip with env vars below)
#   - Python 3.12+
#
# Usage:
#   ./receipt_langsmith/scripts/test_patterns_viz_cache.sh
#
# Environment overrides (skip Pulumi / auto-discovery):
#   LANGSMITH_EXPORT_BUCKET  - S3 bucket for LangSmith parquet exports
#   BATCH_BUCKET             - S3 bucket for pattern files
#   SF_ARN                   - Step Functions state machine ARN
#   EXECUTION_ID             - Override execution_id (skip SF lookup)
#   EXPORT_ID                - Override LangSmith export_id (skip detection)
#   PARQUET_DIR              - Local path for parquet data (default: /tmp/langsmith-traces)
#   FRESH                    - Set to "1" to delete and re-download all data
# ---------------------------------------------------------------------------

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
INFRA_DIR="$REPO_ROOT/infra"

PARQUET_DIR="${PARQUET_DIR:-/tmp/langsmith-traces}"
PATTERNS_DIR="/tmp/patterns-cache"
VENV_DIR="$REPO_ROOT/.venv-patterns-test"

# Text formatting
bold() { printf "\n\033[1m%s\033[0m\n" "$*"; }
info() { printf "  \033[36m→\033[0m %s\n" "$*"; }
ok()   { printf "  \033[32m✓\033[0m %s\n" "$*"; }
warn() { printf "  \033[33m!\033[0m %s\n" "$*"; }
err()  { printf "  \033[31m✗\033[0m %s\n" "$*" >&2; }

# Fresh start?
if [[ "${FRESH:-}" == "1" ]]; then
    warn "FRESH=1 — deleting cached data"
    rm -rf "$PARQUET_DIR" "$PATTERNS_DIR" "$VENV_DIR"
fi

# ---------------------------------------------------------------------------
# Step 1 — Resolve bucket names + state machine ARN
# ---------------------------------------------------------------------------
bold "Step 1: Resolving infrastructure outputs"

if [[ -z "${LANGSMITH_EXPORT_BUCKET:-}" ]]; then
    info "Looking up langsmith_export_bucket from Pulumi…"
    LANGSMITH_EXPORT_BUCKET=$(cd "$INFRA_DIR" && pulumi stack output langsmith_export_bucket --stack dev 2>/dev/null | head -1)
fi
ok "LANGSMITH_EXPORT_BUCKET=$LANGSMITH_EXPORT_BUCKET"

if [[ -z "${BATCH_BUCKET:-}" ]]; then
    info "Looking up label_evaluator_batch_bucket_name from Pulumi…"
    BATCH_BUCKET=$(cd "$INFRA_DIR" && pulumi stack output label_evaluator_batch_bucket_name --stack dev 2>/dev/null | head -1)
fi
ok "BATCH_BUCKET=$BATCH_BUCKET"

if [[ -z "${SF_ARN:-}" ]]; then
    info "Looking up label_evaluator_sf_arn from Pulumi…"
    SF_ARN=$(cd "$INFRA_DIR" && pulumi stack output label_evaluator_sf_arn --stack dev 2>/dev/null | head -1)
fi
ok "SF_ARN=$SF_ARN"

# ---------------------------------------------------------------------------
# Step 2 — Find the latest SUCCEEDED execution → execution_id
# ---------------------------------------------------------------------------
bold "Step 2: Finding latest succeeded step-function execution"

if [[ -z "${EXECUTION_ID:-}" ]]; then
    EXECUTION_JSON=$(aws stepfunctions list-executions \
        --state-machine-arn "$SF_ARN" \
        --status-filter SUCCEEDED \
        --max-items 1)

    EXECUTION_ID=$(echo "$EXECUTION_JSON" | python3 -c "
import sys, json
data = json.load(sys.stdin)
print(data['executions'][0]['name'])
")
fi
ok "EXECUTION_ID=$EXECUTION_ID"

# ---------------------------------------------------------------------------
# Step 3 — Discover the LangSmith export_id
# ---------------------------------------------------------------------------
bold "Step 3: Discovering LangSmith export_id"

if [[ -z "${EXPORT_ID:-}" ]]; then
    # The export_id isn't stored in the execution output, so we find it by
    # looking at the most recently modified parquet file in the export bucket.
    # This is a single `s3api` call that lists all prefixes, then we check
    # only the last few (by alphabetical order) for the newest file.
    #
    # For reliability, we check all prefixes' most-recent object timestamp.
    # With ~90 prefixes this makes ~90 lightweight HEAD-like calls.
    info "Finding most recent parquet export (this takes ~30s)…"

    EXPORT_ID=$(python3 -c "
import boto3, sys
s3 = boto3.client('s3')
bucket = '$LANGSMITH_EXPORT_BUCKET'

# List all export_id prefixes
paginator = s3.get_paginator('list_objects_v2')
prefixes = []
for page in paginator.paginate(Bucket=bucket, Prefix='traces/export_id=', Delimiter='/'):
    for p in page.get('CommonPrefixes', []):
        eid = p['Prefix'].split('export_id=')[1].rstrip('/')
        prefixes.append(eid)

# For each prefix, get the most recent object's timestamp
best_ts = None
best_eid = None
for eid in prefixes:
    resp = s3.list_objects_v2(Bucket=bucket, Prefix=f'traces/export_id={eid}/', MaxKeys=1)
    contents = resp.get('Contents', [])
    if not contents:
        continue
    ts = contents[0]['LastModified']
    if best_ts is None or ts > best_ts:
        best_ts = ts
        best_eid = eid

print(best_eid or '')
" 2>/dev/null)

    if [[ -z "$EXPORT_ID" ]]; then
        err "Could not discover export_id automatically."
        err "Set EXPORT_ID env var manually (check S3 bucket for available exports)."
        exit 1
    fi
fi
ok "EXPORT_ID=$EXPORT_ID"

PARQUET_S3="s3://$LANGSMITH_EXPORT_BUCKET/traces/export_id=$EXPORT_ID/"
PATTERNS_S3="s3://$BATCH_BUCKET/patterns/$EXECUTION_ID/"

info "Parquet source:  $PARQUET_S3"
info "Patterns source: $PATTERNS_S3"

# Verify the S3 paths exist
PARQUET_S3_COUNT=$(aws s3 ls "$PARQUET_S3" --recursive 2>/dev/null | grep -c '\.parquet$' || true)
PATTERN_S3_COUNT=$(aws s3 ls "$PATTERNS_S3" 2>/dev/null | grep -c '\.json$' || true)

if [[ "$PARQUET_S3_COUNT" -eq 0 ]]; then
    err "No parquet files found at $PARQUET_S3"
    exit 1
fi
ok "Found $PARQUET_S3_COUNT parquet files in S3"

if [[ "$PATTERN_S3_COUNT" -eq 0 ]]; then
    err "No pattern files found at $PATTERNS_S3"
    exit 1
fi
ok "Found $PATTERN_S3_COUNT pattern files in S3"

# ---------------------------------------------------------------------------
# Step 4 — Download data (skip if already present)
# ---------------------------------------------------------------------------
bold "Step 4: Downloading test data"

# Parquet traces (~1.2 GB)
PARQUET_COUNT=$(find "$PARQUET_DIR" -name '*.parquet' 2>/dev/null | wc -l | tr -d ' ')
if [[ "$PARQUET_COUNT" -gt 0 ]]; then
    info "Reusing $PARQUET_COUNT existing parquet files in $PARQUET_DIR"
    info "(Set FRESH=1 to force re-download)"
else
    info "Downloading ~1.2 GB of parquet traces to $PARQUET_DIR …"
    mkdir -p "$PARQUET_DIR"
    aws s3 sync "$PARQUET_S3" "$PARQUET_DIR/"
    PARQUET_COUNT=$(find "$PARQUET_DIR" -name '*.parquet' | wc -l | tr -d ' ')
    ok "Downloaded $PARQUET_COUNT parquet files"
fi

# Pattern files (~400 KB)
PATTERN_COUNT=$(find "$PATTERNS_DIR" -name '*.json' 2>/dev/null | wc -l | tr -d ' ')
if [[ "$PATTERN_COUNT" -gt 0 ]]; then
    info "Reusing $PATTERN_COUNT existing pattern files in $PATTERNS_DIR"
else
    info "Downloading pattern files to $PATTERNS_DIR …"
    mkdir -p "$PATTERNS_DIR"
    aws s3 sync "$PATTERNS_S3" "$PATTERNS_DIR/"
    PATTERN_COUNT=$(find "$PATTERNS_DIR" -name '*.json' | wc -l | tr -d ' ')
    ok "Downloaded $PATTERN_COUNT pattern files"
fi

# ---------------------------------------------------------------------------
# Step 5 — Create or reuse venv
# ---------------------------------------------------------------------------
bold "Step 5: Setting up Python virtual environment"

if [[ -d "$VENV_DIR" && -f "$VENV_DIR/bin/python" ]]; then
    info "Reusing existing venv at $VENV_DIR"
else
    PYTHON_BIN="${PYTHON:-python3}"
    info "Creating venv with $($PYTHON_BIN --version) at $VENV_DIR …"
    "$PYTHON_BIN" -m venv "$VENV_DIR"
    info "Installing dependencies …"
    "$VENV_DIR/bin/pip" install --quiet --upgrade pip
    "$VENV_DIR/bin/pip" install --quiet \
        "pyarrow>=14.0.0" \
        "pytest>=8.0" \
        "boto3>=1.34.0" \
        "pydantic>=2.0.0" \
        "httpx>=0.25.0" \
        "tenacity>=8.0.0" \
        "langsmith>=0.1.0"
    # Install receipt_langsmith in editable mode (no pyspark needed for local tests)
    "$VENV_DIR/bin/pip" install --quiet -e "$REPO_ROOT/receipt_langsmith"
    ok "Venv ready"
fi

# ---------------------------------------------------------------------------
# Step 6 — Run the tests
# ---------------------------------------------------------------------------
bold "Step 6: Running tests"

export PARQUET_DIR
export BATCH_BUCKET
export EXECUTION_ID

info "PARQUET_DIR=$PARQUET_DIR"
info "BATCH_BUCKET=$BATCH_BUCKET"
info "EXECUTION_ID=$EXECUTION_ID"
echo ""

cd "$REPO_ROOT"
exec "$VENV_DIR/bin/python" -m pytest \
    receipt_langsmith/tests/spark/test_evaluator_patterns_viz_cache.py \
    -v \
    --tb=short \
    -x
