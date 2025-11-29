# Label Harmonizer Batch Execution Guide

## Overview

The label harmonizer can be run for all label types in parallel as background tasks. This allows you to process all CORE_LABELS simultaneously, significantly reducing total execution time.

## Quick Start

### Run All Label Types in Parallel

```bash
# Basic usage (all merchants, all groups)
./scripts/run_all_label_harmonizers.sh

# With limits (for testing)
MAX_MERCHANTS=5 LIMIT=3 ./scripts/run_all_label_harmonizers.sh

# With verbose logging
VERBOSE=true ./scripts/run_all_label_harmonizers.sh

# Custom batch size
BATCH_SIZE=500 ./scripts/run_all_label_harmonizers.sh
```

### Check Status

```bash
./scripts/check_harmonizer_status.sh
```

### View Logs

```bash
# View all logs
tail -f label_harmonizer_logs/harmonizer_*.log

# View specific label type
tail -f label_harmonizer_logs/harmonizer_grand_total.log

# View last 50 lines of all logs
for log in label_harmonizer_logs/*.log; do
    echo "=== $(basename $log) ==="
    tail -50 "$log"
    echo ""
done
```

### Stop All Processes

```bash
pkill -f test_label_harmonizer
```

## Label Types Processed

The script processes all 16 CORE_LABELS:

**Currency Labels:**
- GRAND_TOTAL
- SUBTOTAL
- TAX
- LINE_TOTAL

**Transaction Labels:**
- DATE
- TIME
- PAYMENT_METHOD
- COUPON
- DISCOUNT
- LOYALTY_ID

**Merchant Labels:**
- MERCHANT_NAME
- PHONE_NUMBER
- ADDRESS_LINE

**Line Item Labels:**
- PRODUCT_NAME
- QUANTITY
- UNIT_PRICE

## Log Files

Each label type gets its own log file:
- `label_harmonizer_logs/harmonizer_grand_total.log`
- `label_harmonizer_logs/harmonizer_subtotal.log`
- etc.

The PID file (`label_harmonizer_logs/pids.txt`) tracks all running processes.

## Performance

- **Sequential**: ~16 label types × 30 min = ~8 hours
- **Parallel**: ~16 label types ÷ 16 = ~30-60 minutes (limited by longest-running label type)

## Current Run Status

Your current run proves the CLI is working:
- ✅ Process running successfully
- ✅ LLM calls working
- ✅ Outlier detection functional
- ✅ Logging working correctly

## Next Steps

1. Let current run complete to see final results
2. Use batch script to run all labels in parallel
3. Monitor progress with status script
4. Review logs for each label type
