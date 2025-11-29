#!/bin/bash
# Check Label Harmonizer Custom Metrics from CloudWatch
#
# Usage:
#   ./scripts/check_harmonizer_metrics.sh [hours]
#
# Options:
#   hours    Number of hours to look back (default: 24)
#
# Examples:
#   ./scripts/check_harmonizer_metrics.sh 24    # Last 24 hours (default)
#   ./scripts/check_harmonizer_metrics.sh 2      # Last 2 hours
#   ./scripts/check_harmonizer_metrics.sh 168   # Last week

set -e

HOURS="${1:-24}"
REGION="${AWS_REGION:-us-east-1}"
NAMESPACE="LabelHarmonizer"

# Calculate time range
END_TIME=$(date -u +%Y-%m-%dT%H:%M:%S)
START_TIME=$(date -u -v-${HOURS}H +%Y-%m-%dT%H:%M:%S)

# Period: use 1 hour for 24+ hours, 5 minutes for shorter periods
if [ "$HOURS" -ge 24 ]; then
    PERIOD=3600
else
    PERIOD=300
fi

# All CORE_LABELS
LABELS=(
    "MERCHANT_NAME" "PRODUCT_NAME" "GRAND_TOTAL" "ADDRESS_LINE"
    "DATE" "TIME" "PAYMENT_METHOD" "LINE_TOTAL" "SUBTOTAL" "TAX"
    "PHONE_NUMBER" "STORE_HOURS" "WEBSITE" "LOYALTY_ID"
    "COUPON" "DISCOUNT" "QUANTITY" "UNIT_PRICE"
)

echo "==========================================="
echo "LABEL HARMONIZER METRICS"
echo "==========================================="
echo "Time Range: Last $HOURS hours"
echo "Start: $START_TIME"
echo "End: $END_TIME"
echo ""

# Function to get metric value
get_metric() {
    local metric_name=$1
    local label_type=$2
    local dimension=""

    if [ -n "$label_type" ]; then
        dimension="--dimensions Name=LabelType,Value=$label_type"
    fi

    aws cloudwatch get-metric-statistics \
        --namespace "$NAMESPACE" \
        --metric-name "$metric_name" \
        $dimension \
        --start-time "$START_TIME" \
        --end-time "$END_TIME" \
        --period "$PERIOD" \
        --statistics Sum \
        --region "$REGION" \
        --output json 2>/dev/null | \
        jq -r '[.Datapoints[]?.Sum // 0] | add | floor' || echo "0"
}

# ============================================
# 1. PROCESSING METRICS BY LABEL TYPE
# ============================================
echo "📊 PROCESSING METRICS BY LABEL TYPE:"
echo ""

for label in "${LABELS[@]}"; do
    labels_processed=$(get_metric "LabelsProcessed" "$label")
    outliers=$(get_metric "OutliersDetected" "$label")
    batches=$(get_metric "BatchSucceeded" "$label")
    failed_batches=$(get_metric "BatchFailed" "$label")

    if [ "$labels_processed" != "0" ] || [ "$batches" != "0" ]; then
        echo "  $label:"
        echo "    Labels Processed: $labels_processed"
        echo "    Outliers Detected: $outliers"
        if [ "$labels_processed" != "0" ] && [ "$outliers" != "0" ]; then
            outlier_rate=$(echo "scale=1; $outliers * 100 / $labels_processed" | bc 2>/dev/null || echo "0")
            echo "    Outlier Rate: ${outlier_rate}%"
        fi
        echo "    Batches Succeeded: $batches"
        if [ "$failed_batches" != "0" ]; then
            echo "    Batches Failed: $failed_batches"
        fi
        echo ""
    fi
done

# ============================================
# 2. LABEL UPDATE METRICS
# ============================================
echo "📈 LABEL UPDATE METRICS BY LABEL TYPE:"
echo ""

total_updated=0
total_skipped=0
total_failed=0
total_needs_review=0

for label in "${LABELS[@]}"; do
    updated=$(get_metric "LabelsUpdated" "$label")
    skipped=$(get_metric "LabelsSkipped" "$label")
    failed=$(get_metric "LabelsFailed" "$label")
    needs_review=$(get_metric "LabelsNeedsReview" "$label")

    total_updated=$((total_updated + updated))
    total_skipped=$((total_skipped + skipped))
    total_failed=$((total_failed + failed))
    total_needs_review=$((total_needs_review + needs_review))

    if [ "$updated" != "0" ] || [ "$skipped" != "0" ] || [ "$needs_review" != "0" ]; then
        total=$((updated + skipped + failed + needs_review))
        if [ "$total" != "0" ]; then
            update_pct=$(echo "scale=1; $updated * 100 / $total" | bc 2>/dev/null || echo "0")
            skip_pct=$(echo "scale=1; $skipped * 100 / $total" | bc 2>/dev/null || echo "0")
            review_pct=$(echo "scale=1; $needs_review * 100 / $total" | bc 2>/dev/null || echo "0")

            echo "  $label:"
            echo "    Updated: $updated (${update_pct}%)"
            echo "    Skipped: $skipped (${skip_pct}%)"
            echo "    Needs Review: $needs_review (${review_pct}%)"
            if [ "$failed" != "0" ]; then
                echo "    Failed: $failed"
            fi
            echo ""
        fi
    fi
done

total_processed=$((total_updated + total_skipped + total_failed + total_needs_review))
echo "📊 OVERALL UPDATE TOTALS:"
echo "  Total Labels Processed: $total_processed"
echo "  Updated: $total_updated"
echo "  Skipped: $total_skipped"
echo "  Needs Review: $total_needs_review"
echo "  Failed: $total_failed"
echo ""

if [ "$total_processed" != "0" ]; then
    update_rate=$(echo "scale=2; $total_updated * 100 / $total_processed" | bc 2>/dev/null || echo "0")
    skip_rate=$(echo "scale=2; $total_skipped * 100 / $total_processed" | bc 2>/dev/null || echo "0")
    review_rate=$(echo "scale=2; $total_needs_review * 100 / $total_processed" | bc 2>/dev/null || echo "0")
    echo "  Update Rate: ${update_rate}%"
    echo "  Skip Rate: ${skip_rate}%"
    echo "  Review Rate: ${review_rate}%"
fi
echo ""

# ============================================
# 3. API USAGE METRICS
# ============================================
echo "🤖 API USAGE METRICS:"
echo ""

total_llm=0
total_successful=0
total_failed=0
total_retries=0
total_rate_limits=0
total_server_errors=0
total_timeouts=0
total_circuit=0

for label in "${LABELS[@]}"; do
    llm=$(get_metric "LLMCallsTotal" "$label")
    successful=$(get_metric "LLMCallsSuccessful" "$label")
    failed=$(get_metric "LLMCallsFailed" "$label")
    retries=$(get_metric "RetryAttempts" "$label")
    rate_limits=$(get_metric "RateLimitErrors" "$label")
    server_errors=$(get_metric "ServerErrors" "$label")
    timeouts=$(get_metric "TimeoutErrors" "$label")
    circuit=$(get_metric "CircuitBreakerTriggers" "$label")

    total_llm=$((total_llm + llm))
    total_successful=$((total_successful + successful))
    total_failed=$((total_failed + failed))
    total_retries=$((total_retries + retries))
    total_rate_limits=$((total_rate_limits + rate_limits))
    total_server_errors=$((total_server_errors + server_errors))
    total_timeouts=$((total_timeouts + timeouts))
    total_circuit=$((total_circuit + circuit))
done

echo "  Total LLM Calls: $total_llm"
echo "  Successful: $total_successful"
echo "  Failed: $total_failed"

if [ "$total_llm" != "0" ]; then
    success_rate=$(echo "scale=2; $total_successful * 100 / $total_llm" | bc 2>/dev/null || echo "0")
    echo "  Success Rate: ${success_rate}%"
fi

echo "  Retry Attempts: $total_retries"
echo "  Rate Limit Errors: $total_rate_limits"
echo "  Server Errors (5xx): $total_server_errors"
echo "  Timeout Errors: $total_timeouts"
echo "  Circuit Breaker Triggers: $total_circuit"
echo ""

# ============================================
# 4. OVERALL SUMMARY
# ============================================
echo "📊 OVERALL SUMMARY:"
echo ""

total_labels=0
total_outliers=0
total_batches=0
total_failed_batches=0

for label in "${LABELS[@]}"; do
    labels=$(get_metric "LabelsProcessed" "$label")
    outliers=$(get_metric "OutliersDetected" "$label")
    batches=$(get_metric "BatchSucceeded" "$label")
    failed_batches=$(get_metric "BatchFailed" "$label")

    total_labels=$((total_labels + labels))
    total_outliers=$((total_outliers + outliers))
    total_batches=$((total_batches + batches))
    total_failed_batches=$((total_failed_batches + failed_batches))
done

echo "  Total Labels Processed: $total_labels"
echo "  Total Outliers Detected: $total_outliers"

if [ "$total_labels" != "0" ]; then
    outlier_rate=$(echo "scale=2; $total_outliers * 100 / $total_labels" | bc 2>/dev/null || echo "0")
    echo "  Outlier Rate: ${outlier_rate}%"
fi

echo "  Total Batches Succeeded: $total_batches"
echo "  Total Batches Failed: $total_failed_batches"

if [ "$total_batches" != "0" ] || [ "$total_failed_batches" != "0" ]; then
    total_batch_count=$((total_batches + total_failed_batches))
    if [ "$total_batch_count" != "0" ]; then
        batch_success_rate=$(echo "scale=1; ($total_batches * 100) / $total_batch_count" | bc 2>/dev/null || echo "0")
        echo "  Batch Success Rate: ${batch_success_rate}%"
    fi
fi
echo ""

# ============================================
# 5. HEALTH CHECK
# ============================================
echo "✅ HEALTH CHECK:"
echo ""

if [ "$total_rate_limits" = "0" ] && [ "$total_circuit" = "0" ] && [ "$total_failed_batches" = "0" ]; then
    echo "  ✅ No rate limit issues"
    echo "  ✅ No circuit breaker triggers"
    echo "  ✅ No batch failures"
    echo "  ✅ System is healthy!"
elif [ "$total_rate_limits" != "0" ] || [ "$total_circuit" != "0" ]; then
    echo "  ⚠️  Rate limit issues detected!"
    echo "     Rate Limit Errors: $total_rate_limits"
    echo "     Circuit Breaker Triggers: $total_circuit"
    echo "     Consider reducing concurrency"
elif [ "$total_failed_batches" != "0" ]; then
    echo "  ⚠️  Batch failures detected: $total_failed_batches"
    echo "     Check CloudWatch logs for details"
else
    echo "  ✅ System appears healthy"
fi

echo ""
echo "==========================================="

