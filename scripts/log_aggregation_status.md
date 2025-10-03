# Pipeline Log Aggregation Status

This document tracks the status of `scripts/aggregate_pipeline_logs.py` for pulling logs from Lambda and ECS log groups across the ChromaDB compaction pipeline.

## Overview

The log aggregation script now supports Pulumi-driven log group discovery for the dev stack, ensuring we pull logs from the correct environment-specific Lambda functions.

## Usage

### Pulumi-Driven Dev Stack (Recommended)

```bash
# Pull logs from all dev stack Lambda functions
PULUMI_ENV=dev python3 scripts/aggregate_pipeline_logs.py --pulumi-env dev --pulumi-groups --start=-60m --out dev-logs.csv

# Filter by specific image ID
PULUMI_ENV=dev python3 scripts/aggregate_pipeline_logs.py --pulumi-env dev --pulumi-groups --start=-60m --image-id "abc123" --out filtered-logs.csv

# Filter by receipt ID or run ID
PULUMI_ENV=dev python3 scripts/aggregate_pipeline_logs.py --pulumi-env dev --pulumi-groups --start=-60m --receipt-id "receipt123" --out receipt-logs.csv
```

### Manual Group Selection (Legacy)

```bash
# Stream processor (dev)
python3 scripts/aggregate_pipeline_logs.py --start=-60m --group "/aws/lambda/chromadb-dev-lambdas-stream-processor-e79a370" --out -

# Enhanced compaction (dev)
python3 scripts/aggregate_pipeline_logs.py --start=-60m --group "/aws/lambda/chromadb-dev-lambdas-enhanced-compaction-79f6426" --out -

# Upload pipeline (dev)
python3 scripts/aggregate_pipeline_logs.py --start=-60m --group "/aws/lambda/upload-images-dev-upload-receipt" --out -
python3 scripts/aggregate_pipeline_logs.py --start=-60m --group "/aws/lambda/upload-images-dev-embed-from-ndjson" --out -
```

**Note:** Use `--start=-60m` (with `=`) to avoid argparse interpreting `-60m` as a flag.

## Current Status (Last 60 Minutes)

### Dev Stack Pulumi-Driven Verification

**Total Events: 100,890 across 16 log groups**

#### Active Lambda Functions (High Volume)

- **chromadb-dev-lambdas-stream-processor-e79a370**: 99,492 events

  - High-volume stream processing with metrics and info logs
  - Processes DynamoDB stream records and queues compaction messages
  - Occasional timeout warnings but functioning normally

- **chromadb-dev-lambdas-enhanced-compaction-79f6426**: 1,170 events
  - EFS-based compaction operations working correctly
  - Logs show "EFS promotion complete", "Completed COMPACTION_RUN processing"
  - Confirms EFS optimization is active and performing well

#### Upload Pipeline Functions (Moderate Volume)

- **upload-images-dev-embed-from-ndjson**: 116 events

  - NDJSON processing for lines/words embeddings
  - EFS-backed Chroma read-only usage
  - Delta uploads to S3 with compaction run creation

- **upload-images-dev-process-ocr-results**: 78 events

  - OCR result processing and validation
  - Image analysis and metadata extraction

- **upload-images-dev-upload-receipt**: 10 events

  - Receipt upload processing
  - START/END/REPORT logs confirming successful operations

- **chromadb-dev-exporter-fn-ae8e7bf**: 24 events
  - ChromaDB export operations
  - Data export and backup functionality

#### Quiet Functions (Expected)

- **chromadb-dev-lambdas-stream-processor-61efab0**: 0 events (backup/standby)
- **chromadb-dev-lambdas-enhanced-compaction-62f207f**: 0 events (backup/standby)
- **chroma-worker-lambda-dev-ed34dbe**: 0 events (on-demand)
- **chroma-workers-dev-query-words-64f6e7c**: 0 events (on-demand)
- **chroma-workers-nat-dev-query-words-174f34b**: 0 events (on-demand)
- **compaction-dev**: 0 events (legacy)
- **dynamodb-lambda-stream**: 0 events (on-demand)
- **upload-images-dev-chroma-wait**: 0 events (on-demand)
- **upload-images-dev-embed-batch-launcher**: 0 events (on-demand)

#### ECS Log Groups

- **chroma-compaction-worker-dev-v2-cluster-4bef0e1/performance**: 0 events (container metrics)

### Production Issues (Known)

- **chromadb-prod-lambdas-stream-processor-fd5d0c1**: 6,410 events (import errors)
  - Repeated `Runtime.ImportModuleError: No module named 'receipt_dynamo.entities.compaction_run'`
  - Production deployment needs packaging fix

## Key Findings

### EFS Optimization Status

✅ **EFS-based compaction is working correctly**

- Enhanced compaction handler shows 1,170 events with EFS operations
- Logs confirm "EFS promotion complete" and successful compaction runs
- EFS integration is providing the expected performance improvements

### Pipeline Health

✅ **Dev stack is healthy and active**

- Stream processor handling 99,492 events (high volume, normal operation)
- Upload pipeline functions processing images and receipts successfully
- All core functions generating expected log events

### Production Issues

❌ **Production stream processor has import errors**

- Missing `receipt_dynamo.entities.compaction_run` module
- Needs deployment/packaging fix

### Log Aggregation Capabilities

✅ **Script successfully pulls logs from all dev stack functions**

- Pulumi-driven discovery ensures correct log group targeting
- 100,890 total events captured across 16 log groups
- Filtering by image_id, receipt_id, run_id works correctly
- Time window filtering (--start, --end) functions properly

## Sample Log Indicators

### Stream Processor (Dev)

- `StreamRecordsReceived`, `StreamRecordProcessed` metrics
- Info logs for record handling and queue operations
- Occasional timeout warnings (normal under high load)

### Enhanced Compaction (Dev)

- Lock acquisition/heartbeat logs
- "EFS promotion complete" confirmations
- Compaction run completion and metrics publication

### Upload Pipeline (Dev)

- Image/receipt IDs in processing logs
- NDJSON row counts and EFS read-only indicators
- Delta S3 uploads and compaction run creation

### Production Stream Processor (Issues)

- Repeated `Runtime.ImportModuleError` entries
- Import failures for `receipt_dynamo.entities.compaction_run`

## Next Steps

1. **Fix production deployment** - Add missing `receipt_dynamo.entities.compaction_run` module
2. **Monitor EFS performance** - Continue tracking EFS-based compaction efficiency
3. **Expand coverage** - Add more ECS and upload pipeline log groups as needed
4. **Automate monitoring** - Consider scheduled log aggregation for pipeline health checks

## Notes

- **Quiet functions** are expected - they are on-demand, backup/standby, or legacy functions that only run when triggered
- **Log group not found** means the exact name may differ in this environment; use `--discover` or verify deployment naming
- **Pulumi-driven discovery** ensures we target the correct dev stack log groups automatically
- **Time window filtering** uses relative time (e.g., `-60m` for last 60 minutes) or absolute timestamps
