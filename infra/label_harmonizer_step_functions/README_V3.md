# Label Harmonizer V3 Step Function

AWS Step Function infrastructure for running label harmonization V3 (whole receipt processing) with financial validation sub-agent.

## Architecture

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                    Label Harmonizer V3 Step Function                          │
└──────────────────────────────────────────────────────────────────────────────┘
                                      │
┌─────────────────────────────────────┼─────────────────────────────────────────┐
│  1. ListReceipts (Zip Lambda)                                                  │
│     - Scans DynamoDB for all receipts                                          │
│     - Batches receipts (default: 50 per batch)                                 │
│     - Supports limit parameter for testing                                     │
│                                     │                                         │
│  2. ProcessInBatches (Map State, 5 concurrent)                                 │
│     ┌─────────────┐  ┌─────────────┐  ┌─────────────┐                         │
│     │ Harmonize   │  │ Harmonize   │  │ Harmonize   │  ... (5 parallel)       │
│     │ Labels V3   │  │ Labels V3   │  │ Labels V3   │                         │
│     │ (Container) │  │ (Container) │  │ (Container) │                         │
│     └─────────────┘  └─────────────┘  └─────────────┘                         │
│     - Downloads ChromaDB snapshot                                              │
│     - Processes batch of receipts                                              │
│     - Runs LabelHarmonizerV3 agent (whole receipt)                             │
│     - Financial validation sub-agent                                           │
│     - Uploads results to S3                                                    │
│                                     │                                         │
│  3. Done - Results aggregated                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

## Components

### 1. List Receipts Lambda (Zip)
- **Purpose**: Scan DynamoDB and batch receipts
- **Memory**: 256 MB
- **Timeout**: 5 minutes
- **Input**: `execution_id`, `batch_bucket`, `batch_size`, `limit` (optional)
- **Output**: `receipt_batches` (list of batches), `total_receipts`, `total_batches`

### 2. Harmonize Labels V3 Lambda (Container)
- **Purpose**: Process batch of receipts with LabelHarmonizerV3 agent
- **Memory**: 2048 MB (2 GB)
- **Timeout**: 15 minutes
- **Input**: `receipts` (list), `execution_id`, `dry_run`, `batch_bucket`
- **Output**: Results uploaded to S3, EMF metrics emitted

## Usage

### Deploy

```bash
cd infra
pulumi up
```

### Start Execution (Dry Run with Limit)

```bash
# Get the Step Function ARN from Pulumi outputs
pulumi stack output label_harmonizer_v3_sf_arn

# Start execution with dry_run=true and limit=10
aws stepfunctions start-execution \
  --state-machine-arn <ARN_FROM_OUTPUT> \
  --input '{
    "dry_run": true,
    "limit": 10,
    "batch_size": 10,
    "langchain_project": "label-harmonizer-v3-test"
  }'
```

### Input Parameters

```json
{
  "dry_run": true,              // Don't update DynamoDB (default: true)
  "limit": 10,                  // Optional: limit total receipts (for testing)
  "batch_size": 50,             // Optional: receipts per batch (default: 50)
  "langchain_project": "label-harmonizer-v3"  // Optional: LangSmith project name
}
```

### Monitor Execution

```bash
# List executions
aws stepfunctions list-executions \
  --state-machine-arn <ARN>

# Get execution details
aws stepfunctions describe-execution \
  --execution-arn <EXECUTION_ARN>

# Get execution history
aws stepfunctions get-execution-history \
  --execution-arn <EXECUTION_ARN>
```

## S3 Structure

```
harmonizer-v3-batch-bucket/
├── batches/
│   └── {execution_id}/
│       └── batch_0.json
│       └── batch_1.json
│       └── ...
├── results/
│   └── {execution_id}/
│       └── batch_0_results.json
│       └── batch_1_results.json
│       └── ...
```

## Features

- **Whole Receipt Processing**: Processes entire receipts, not just merchant groups
- **Financial Validation Sub-Agent**: Validates totals, currency, line items
- **Add/Update Safeguards**: Properly handles multiple labels per word with audit trail
- **Dry Run Support**: Test without updating DynamoDB
- **Limit Support**: Test with small number of receipts
- **Parallel Processing**: Process multiple batches concurrently (default: 5)

## Performance Estimates

| Phase | Concurrency | Time per Batch | Estimated Time |
|-------|-------------|----------------|----------------|
| List Receipts | 1 | ~30s | ~30s |
| Process Batches | 5 parallel | 2-5min per batch | Varies by batch count |
| Total (10 receipts, 1 batch) | - | - | ~2-5 minutes |

## Error Handling

### Retry Policies

- **List Receipts Lambda**: 3 retries, 2s backoff, 2.0 rate
- **Harmonize Labels V3 Lambda**: 2 retries, 5s backoff, 2.0 rate
- **Ollama Rate Limit**: 5 retries, 30s backoff, 1.5 rate

### Error States

- **ProcessFailed**: If batch processing fails after retries
- **NoReceiptsToProcess**: If no receipts found

## Differences from V2

- **V2**: Processes labels by merchant group and label type
- **V3**: Processes entire receipts, validates financial consistency
- **V2**: Uses merchant-based harmonization
- **V3**: Uses receipt text as source of truth, financial validation sub-agent
