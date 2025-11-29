#!/bin/bash
# Check status of running label harmonizer processes
#
# Usage:
#   ./scripts/check_harmonizer_status.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LOGS_DIR="$REPO_ROOT/label_harmonizer_logs"
PID_FILE="$LOGS_DIR/pids.txt"

if [ ! -f "$PID_FILE" ]; then
    echo "No PID file found. Have you started the harmonizers?"
    exit 1
fi

echo "=========================================="
echo "Label Harmonizer Status"
echo "=========================================="
echo ""

# Read PIDS from file (using arrays instead of associative arrays for compatibility)
LABELS=()
PIDS=()
while IFS=':' read -r label pid; do
    # Skip comments
    [[ "$label" =~ ^# ]] && continue
    LABELS+=("$label")
    PIDS+=("$pid")
done < "$PID_FILE"

# Check each process
RUNNING=0
COMPLETED=0
TOTAL=${#LABELS[@]}

for i in "${!LABELS[@]}"; do
    label="${LABELS[$i]}"
    pid="${PIDS[$i]}"
    label_lower=$(echo "$label" | tr '[:upper:]' '[:lower:]')
    log_file="$LOGS_DIR/harmonizer_${label_lower}.log"

    if ps -p "$pid" > /dev/null 2>&1; then
        # Process is running
        echo "🟢 $label (PID: $pid) - RUNNING"
        RUNNING=$((RUNNING + 1))

        # Show last log line
        if [ -f "$log_file" ]; then
            last_line=$(tail -1 "$log_file" 2>/dev/null | head -c 80)
            if [ -n "$last_line" ]; then
                echo "   Last: $last_line"
            fi
        fi
    else
        # Process completed
        echo "✅ $label (PID: $pid) - COMPLETED"
        COMPLETED=$((COMPLETED + 1))

        # Check for completion message
        if [ -f "$log_file" ]; then
            if grep -q "✅ Harmonization complete" "$log_file" 2>/dev/null; then
                echo "   Status: Success"
            elif grep -q "Error\|Traceback" "$log_file" 2>/dev/null; then
                echo "   Status: Error (check log)"
            else
                echo "   Status: Unknown"
            fi
        fi
    fi
    echo ""
done

echo "=========================================="
echo "Summary: $RUNNING running, $COMPLETED completed, $TOTAL total"
echo "=========================================="

