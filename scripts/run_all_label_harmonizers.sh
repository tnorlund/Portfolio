#!/bin/bash
# Run Label Harmonizer for all label types in parallel as background tasks
#
# Usage:
#   ./scripts/run_all_label_harmonizers.sh
#   ./scripts/run_all_label_harmonizers.sh --max-merchants 5 --limit 3
#
# This script runs harmonization for all CORE_LABELS in parallel, with each
# label type running as a separate background process. Logs are saved to
# separate files for each label type.

set -e

# Default arguments
MAX_MERCHANTS="${MAX_MERCHANTS:-}"
LIMIT="${LIMIT:-}"
BATCH_SIZE="${BATCH_SIZE:-1000}"
VERBOSE="${VERBOSE:-}"

# Parse command line arguments
ARGS=""
if [ -n "$MAX_MERCHANTS" ]; then
    ARGS="$ARGS --max-merchants $MAX_MERCHANTS"
fi
if [ -n "$LIMIT" ]; then
    ARGS="$ARGS --limit $LIMIT"
fi
if [ -n "$BATCH_SIZE" ]; then
    ARGS="$ARGS --batch-size $BATCH_SIZE"
fi
if [ "$VERBOSE" = "true" ]; then
    ARGS="$ARGS --verbose"
fi

# All CORE_LABELS to process
LABEL_TYPES=(
    "GRAND_TOTAL"
    "SUBTOTAL"
    "TAX"
    "LINE_TOTAL"
    "DATE"
    "TIME"
    "MERCHANT_NAME"
    "PHONE_NUMBER"
    "ADDRESS_LINE"
    "PRODUCT_NAME"
    "QUANTITY"
    "UNIT_PRICE"
    "PAYMENT_METHOD"
    "COUPON"
    "DISCOUNT"
    "LOYALTY_ID"
)

# Create logs directory
LOGS_DIR="label_harmonizer_logs"
mkdir -p "$LOGS_DIR"

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "=========================================="
echo "Label Harmonizer - Parallel Execution"
echo "=========================================="
echo "Label Types: ${#LABEL_TYPES[@]}"
echo "Logs Directory: $LOGS_DIR"
echo "Arguments: $ARGS"
echo ""

# Activate venv
if [ -f "$REPO_ROOT/venv312/bin/activate" ]; then
    source "$REPO_ROOT/venv312/bin/activate"
else
    echo "⚠️  Warning: venv312 not found, using system Python"
fi

# Track PIDs
PIDS=()
LABEL_NAMES=()

# Start background processes for each label type
for label_type in "${LABEL_TYPES[@]}"; do
    # Sanitize label name for filename (convert to lowercase, compatible with older bash)
    label_lower=$(echo "$label_type" | tr '[:upper:]' '[:lower:]')
    log_file="$LOGS_DIR/harmonizer_${label_lower}.log"

    echo "🚀 Starting harmonizer for $label_type..."
    echo "   Log: $log_file"

    # Run in background, redirect output to log file
    (
        cd "$REPO_ROOT"
        python receipt_agent/examples/test_label_harmonizer.py \
            --label-type "$label_type" \
            $ARGS \
            > "$log_file" 2>&1
    ) &

    PID=$!
    PIDS+=($PID)
    LABEL_NAMES+=("$label_type")

    echo "   PID: $PID"
    echo ""
done

# Save PIDs to file for later reference
PID_FILE="$LOGS_DIR/pids.txt"
echo "# Label Harmonizer PIDs - Started $(date)" > "$PID_FILE"
for i in "${!PIDS[@]}"; do
    echo "${LABEL_NAMES[$i]}:${PIDS[$i]}" >> "$PID_FILE"
done

echo "=========================================="
echo "All harmonizers started!"
echo "=========================================="
echo "Total processes: ${#PIDS[@]}"
echo "PID file: $PID_FILE"
echo ""
echo "To check status:"
echo "  ./scripts/check_harmonizer_status.sh"
echo ""
echo "To view logs:"
echo "  tail -f $LOGS_DIR/harmonizer_*.log"
echo ""
echo "To stop all:"
echo "  pkill -f test_label_harmonizer"
echo ""

