# Realtime Embedding Step Function

## Overview

This document describes the new Step Function workflow for processing all receipts with realtime embeddings. Unlike the batch embedding Step Functions that use OpenAI's Batch API, this workflow uses OpenAI's synchronous Embeddings API to create embeddings immediately.

## Architecture

The realtime embedding workflow consists of:

1. **FindReceipts Lambda** (`find_receipts_realtime`): Finds all receipts from DynamoDB
2. **ProcessReceipt Lambda** (`process_receipt_realtime`): Processes a single receipt with realtime embedding
3. **Step Function**: Orchestrates the workflow

## Workflow

```
FindReceipts → CheckReceipts → ProcessReceipts (Map, MaxConcurrency: 10)
```

### State Machine Flow

1. **FindReceipts**: Invokes `find_receipts_realtime` Lambda to get all receipts
   - Optional filters: `limit`, `filter_by_embedding_status`
   - Returns: List of `{image_id, receipt_id}` objects

2. **CheckReceipts**: Choice state that checks if receipts were found
   - If receipts found → ProcessReceipts
   - If no receipts → NoReceipts (end)

3. **ProcessReceipts**: Map state that processes receipts in parallel
   - MaxConcurrency: 10 (configurable)
   - Each receipt is processed independently
   - Error handling: Failed receipts are caught and logged

4. **ProcessReceipt** (per receipt):
   - Loads lines and words from DynamoDB
   - Resolves merchant name (optional)
   - Creates embeddings using `embed_lines_realtime` and `embed_words_realtime`
   - Uploads deltas to S3 (`lines/delta/{run_id}/delta.tar.gz`, `words/delta/{run_id}/delta.tar.gz`)
   - Creates `COMPACTION_RUN` record in DynamoDB

## Lambda Functions

### `find_receipts_realtime`

**Location**: `infra/embedding_step_functions/simple_lambdas/find_receipts_realtime/handler.py`

**Purpose**: Finds all receipts from DynamoDB with optional filtering

**Input**:
```json
{
  "limit": 100,  // Optional
  "filter_by_embedding_status": "NONE"  // Optional
}
```

**Output**:
```json
{
  "receipts": [
    {"image_id": "uuid", "receipt_id": 1},
    ...
  ],
  "total_receipts": 100,
  "last_evaluated_key": null
}
```

**Environment Variables**:
- `DYNAMODB_TABLE_NAME`: DynamoDB table name

### `process_receipt_realtime`

**Location**: `infra/embedding_step_functions/simple_lambdas/process_receipt_realtime/handler.py`

**Purpose**: Processes a single receipt with realtime embedding

**Input**:
```json
{
  "image_id": "uuid",
  "receipt_id": 1
}
```

**Output**:
```json
{
  "status": "success",
  "image_id": "uuid",
  "receipt_id": 1,
  "run_id": "uuid",
  "lines_count": 10,
  "words_count": 50,
  "merchant_name": "Store Name"
}
```

**Environment Variables**:
- `DYNAMODB_TABLE_NAME`: DynamoDB table name
- `CHROMADB_BUCKET`: S3 bucket for ChromaDB deltas
- `GOOGLE_PLACES_API_KEY`: Optional, for merchant resolution
- `CHROMA_HTTP_ENDPOINT`: Optional, for ChromaDB read access
- `OPENAI_API_KEY`: Required for embedding generation

## Comparison: Batch vs Realtime

| Feature | Batch Embedding | Realtime Embedding |
|---------|----------------|-------------------|
| **API** | OpenAI Batch API | OpenAI Embeddings API |
| **Latency** | Async (minutes to hours) | Synchronous (seconds) |
| **Cost** | Lower (batch pricing) | Higher (per-request) |
| **Use Case** | Bulk processing | Immediate processing |
| **Step Function** | Submit → Poll → Process | Find → Process |
| **Throughput** | High (batches of 1000+) | Medium (10 concurrent) |

## Infrastructure

### Step Function

**Name**: `realtime-embedding-sf-{stack}`

**ARN Output**: `realtime_embedding_sf_arn` (from `EmbeddingInfrastructure`)

**IAM Role**: `realtime-sf-role-{stack}`
- Permissions: `lambda:InvokeFunction` on find and process Lambdas

### Lambda Functions

**Find Receipts**: `embedding-find-receipts-realtime-lambda-{stack}`
- Runtime: Python 3.12
- Memory: 1GB
- Timeout: 15 minutes
- Layer: `receipt_label[lambda]` (includes `receipt_dynamo`)

**Process Receipt**: `embedding-process-receipt-realtime-lambda-{stack}`
- Runtime: Python 3.12
- Memory: 2GB (higher for embedding processing)
- Timeout: 15 minutes
- Layer: `receipt_label[lambda]` (includes `receipt_dynamo`)

## Usage

### Starting the Step Function

After deployment, you can start the Step Function using AWS CLI:

```bash
# Start with default options (all receipts)
aws stepfunctions start-execution \
  --state-machine-arn "arn:aws:states:REGION:ACCOUNT:stateMachine:realtime-embedding-sf-dev" \
  --name "realtime-embedding-$(date +%s)"

# Start with limit
aws stepfunctions start-execution \
  --state-machine-arn "arn:aws:states:REGION:ACCOUNT:stateMachine:realtime-embedding-sf-dev" \
  --name "realtime-embedding-limit-100" \
  --input '{"limit": 100}'
```

### Monitoring

- **CloudWatch Logs**: Each Lambda has its own log group
- **Step Function Console**: View execution history and state transitions
- **X-Ray Tracing**: Enabled for container Lambdas (not zip-based)

## Files Created

1. **Lambda Handlers**:
   - `infra/embedding_step_functions/simple_lambdas/find_receipts_realtime/handler.py`
   - `infra/embedding_step_functions/simple_lambdas/process_receipt_realtime/handler.py`

2. **Workflow Component**:
   - `infra/embedding_step_functions/components/realtime_workflow.py`

3. **Infrastructure Updates**:
   - `infra/embedding_step_functions/components/lambda_functions.py` (added new Lambdas)
   - `infra/embedding_step_functions/components/__init__.py` (exported RealtimeEmbeddingWorkflow)
   - `infra/embedding_step_functions/infrastructure.py` (integrated workflow)

## Next Steps

1. Deploy to dev: `pulumi up --stack dev`
2. Test with a small limit: Start Step Function with `{"limit": 10}`
3. Monitor execution in Step Functions console
4. Verify deltas are uploaded to S3
5. Verify COMPACTION_RUN records are created
6. Scale up to process all receipts

## Related Documentation

- [Embedding Step Functions README](./README.md)
- [ChromaDB Embedding Write Paths](../../docs/CHROMADB_EMBEDDING_WRITE_PATHS.md)
- [ChromaDB Compaction](../../chromadb_compaction/README.md)







