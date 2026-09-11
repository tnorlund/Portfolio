#!/bin/bash
# Promote new receipts from dev → prod.
#
# Steps:
#   1. Sync S3 images  (dev → prod bucket)
#   2. Reconcile DynamoDB records (dev → prod mirror: ADD / REPLACE / DELETE)
#   3. Sync OCR jobs (bucket rewrite + ocr_results/ S3 copy; prod-image filtered)
#
# There is no embedding step. Vector items are copied verbatim by the reconcile
# step (docs/chroma-removal/SPEC.md 3.1) because OpenAI embeddings are not
# bit-stable across calls or model revisions. The word/line embedding step
# functions this script used to kick no longer exist.
#
# The reconcile step (reconcile_dev_to_prod.py) makes prod an exact mirror of
# dev at image granularity: new images added, changed images replaced (delete +
# recopy, so re-OCR/merges are correct), removed images deleted. It subsumes the
# old copy + word-label sync. OCR jobs stay in the dedicated step because it also
# rewrites OCRJob.s3_bucket and copies the ocr_results/ S3 artifact; that step is
# now filtered to prod-existing images so it can't recreate orphan rows. Reconcile
# gates apply on prod compaction-queue health.
#
# Usage: ./scripts/promote_dev_to_prod.sh [--skip-sync] [--dry-run]
#   --skip-sync   Skip S3/DynamoDB sync (useful when already synced)
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
DRY_RUN=false
PROTECT=""

for arg in "$@"; do
  case "$arg" in
    --skip-sync)  SKIP_SYNC=true ;;
    --dry-run)    DRY_RUN=true ;;
    --protect=*)  PROTECT="${arg#*=}" ;;
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

# image_ids to hold back from deletion (kept in prod for later review)
PROTECT_ARG=()
[[ -n "$PROTECT" ]] && PROTECT_ARG=(--protect "$PROTECT")

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
  echo -e "${GREEN}[1/3] Syncing S3 images (dev → prod)...${NC}"
  # Resolve the CDN (assets) bucket names the sync script needs.
  DEV_CDN=$(python3 -c "from receipt_dynamo.data._pulumi import load_env; print(load_env('dev')['cdn_bucket_name'])" 2>/dev/null)
  PROD_CDN=$(python3 -c "from receipt_dynamo.data._pulumi import load_env; print(load_env('prod')['cdn_bucket_name'])" 2>/dev/null)
  if [[ -z "$DEV_CDN" || -z "$PROD_CDN" ]]; then
    echo -e "${RED}Could not resolve CDN bucket names${NC}" >&2; exit 1
  fi
  run bash "$SCRIPT_DIR/sync_images_dev_to_prod_fast.sh" "$DEV_CDN" "$PROD_CDN"
  echo ""

  # Mirror the dev corpus onto prod at image granularity (ADD / REPLACE / DELETE).
  # REPLACE = delete + recopy, so re-OCR/merges land correctly; DELETE propagates
  # dev cleanups. Subsumes the old copy + word-label sync. Gates on prod
  # compaction-queue health before writing (--skip-health-gate to override).
  echo -e "${GREEN}[2/3] Reconciling DynamoDB records (dev → prod mirror)...${NC}"
  if [[ "$DRY_RUN" == "true" ]]; then
    run python3 "$SCRIPT_DIR/reconcile_dev_to_prod.py" "${PROTECT_ARG[@]}"
  else
    python3 "$SCRIPT_DIR/reconcile_dev_to_prod.py" --no-dry-run "${PROTECT_ARG[@]}"
  fi
  echo ""

  # OCR jobs run AFTER reconcile so the prod-image filter sees the final image
  # set (rewrites OCRJob.s3_bucket dev→prod and copies the ocr_results/ artifact).
  # --all-job-types: copy no longer writes any OCRJob rows, so this is the sole
  # restore path — it must cover FIRST_PASS/refinement jobs, not just REGIONAL_REOCR.
  echo -e "${GREEN}[3/3] Syncing OCR jobs (dev → prod; all types, prod-image filtered)...${NC}"
  if [[ "$DRY_RUN" == "true" ]]; then
    run python3 "$SCRIPT_DIR/sync_ocr_jobs_dev_to_prod.py" --all-job-types
  else
    python3 "$SCRIPT_DIR/sync_ocr_jobs_dev_to_prod.py" --all-job-types --no-dry-run
  fi
  echo ""
fi

echo ""
if [[ "$DRY_RUN" == "true" ]]; then
  echo -e "${YELLOW}Done (dry-run — no changes made).${NC}"
else
  echo -e "${GREEN}Done.${NC}"
fi
