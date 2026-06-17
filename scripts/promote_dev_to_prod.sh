#!/bin/bash
# Promote new receipts from dev → prod and kick prod embedding ingestion.
#
# Steps:
#   1. Sync S3 images  (dev → prod bucket)
#   2. Sync DynamoDB records
#   3. Sync OCR jobs
#   4. Sync word labels
#   5. Start prod word + line embedding step functions
#
# Usage: ./scripts/promote_dev_to_prod.sh [--skip-sync] [--skip-embed] [--dry-run]
#   --skip-sync   Skip S3/DynamoDB sync (useful when already synced)
#   --skip-embed  Skip step function kick (data-only promotion)
#   --dry-run     Print what would run, do nothing
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

SKIP_SYNC=false
SKIP_EMBED=false
DRY_RUN=false

for arg in "$@"; do
  case "$arg" in
    --skip-sync)  SKIP_SYNC=true ;;
    --skip-embed) SKIP_EMBED=true ;;
    --dry-run)    DRY_RUN=true ;;
    --help|-h)
      sed -n '/^# /p' "$0" | sed 's/^# //'
      exit 0
      ;;
    *)
      echo -e "${RED}Unknown argument: $arg${NC}" >&2
      exit 1
      ;;
  esac
done

run() {
  if [[ "$DRY_RUN" == "true" ]]; then
    echo -e "${YELLOW}[dry-run] $*${NC}"
  else
    "$@"
  fi
}

echo -e "${BLUE}=== Promote dev → prod ===${NC}"
echo ""

# ── Step 1-4: Data sync ─────────────────────────────────────────────────────
if [[ "$SKIP_SYNC" == "true" ]]; then
  echo -e "${YELLOW}Skipping data sync (--skip-sync)${NC}"
else
  echo -e "${GREEN}[1/4] Syncing S3 images (dev → prod)...${NC}"
  run bash "$SCRIPT_DIR/sync_images_dev_to_prod_fast.sh"
  echo ""

  echo -e "${GREEN}[2/4] Syncing DynamoDB records (dev → prod)...${NC}"
  if [[ "$DRY_RUN" == "true" ]]; then
    run python3 "$SCRIPT_DIR/copy_dynamodb_dev_to_prod.py"
  else
    python3 "$SCRIPT_DIR/copy_dynamodb_dev_to_prod.py" --no-dry-run
  fi
  echo ""

  echo -e "${GREEN}[3/4] Syncing OCR jobs (dev → prod)...${NC}"
  if [[ "$DRY_RUN" == "true" ]]; then
    run python3 "$SCRIPT_DIR/sync_ocr_jobs_dev_to_prod.py"
  else
    python3 "$SCRIPT_DIR/sync_ocr_jobs_dev_to_prod.py" --no-dry-run
  fi
  echo ""

  echo -e "${GREEN}[4/4] Syncing word labels (dev → prod)...${NC}"
  if [[ "$DRY_RUN" == "true" ]]; then
    run python3 "$SCRIPT_DIR/sync_labels_dev_to_prod.py"
  else
    python3 "$SCRIPT_DIR/sync_labels_dev_to_prod.py" --no-dry-run --force-dump
  fi
  echo ""
fi

# ── Step 5: Kick prod embedding SFs ─────────────────────────────────────────
if [[ "$SKIP_EMBED" == "true" ]]; then
  echo -e "${YELLOW}Skipping embedding step functions (--skip-embed)${NC}"
else
  echo -e "${GREEN}[5/5] Starting prod embedding step functions...${NC}"
  run bash "$SCRIPT_DIR/start_ingestion_prod.sh" both
  echo ""
  echo -e "${BLUE}Monitor ingestion:${NC}"
  echo "  aws stepfunctions list-executions --state-machine-arn \$(aws stepfunctions list-state-machines --query 'stateMachines[?contains(name,\`word-ingest-sf-prod\`)].stateMachineArn' --output text) --query 'executions[0]' --output json"
fi

echo ""
if [[ "$DRY_RUN" == "true" ]]; then
  echo -e "${YELLOW}Done (dry-run — no changes made).${NC}"
elif [[ "$SKIP_EMBED" == "true" ]]; then
  echo -e "${GREEN}Done. Embedding skipped (--skip-embed).${NC}"
else
  echo -e "${GREEN}Done. Prod embedding will run in the background.${NC}"
fi
