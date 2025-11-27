# Label Harmonizer Step Function

AWS Step Function infrastructure for running label harmonization across all CORE_LABELS in parallel with batch processing.

## Architecture

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                         Label Harmonizer Step Function                        │
└──────────────────────────────────────────────────────────────────────────────┘
                                      │
┌─────────────────────────────────────┼─────────────────────────────────────────┐
│  Phase 1: ParallelPrepare (16 concurrent)                                     │
│                                     │                                         │
│    ┌─────────────┐  ┌─────────────┐  ┌─────────────┐                         │
│    │ PrepareLabels│  │ PrepareLabels│  │ PrepareLabels│  ... (16 label types) │
│    │ (ZIP Lambda) │  │ (ZIP Lambda) │  │ (ZIP Lambda) │                       │
│    │ MERCHANT_NAME│  │ GRAND_TOTAL │  │ DATE        │                         │
│    └──────┬──────┘  └──────┬──────┘  └──────┬──────┘                         │
│           │                │                │                                 │
│           └────────────────┼────────────────┘                                 │
│                            │                                                  │
│                     S3: NDJSON batch files                                    │
└────────────────────────────┼──────────────────────────────────────────────────┘
                             │
┌────────────────────────────┼──────────────────────────────────────────────────┐
│  Phase 2: ProcessInBatches (4 concurrent - LLM limited)                       │
│                            │                                                  │
│    ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐        │
│    │ Harmonize   │  │ Harmonize   │  │ Harmonize   │  │ Harmonize   │        │
│    │ (Container) │  │ (Container) │  │ (Container) │  │ (Container) │        │
│    │ Sprouts     │  │ Walmart     │  │ Target      │  │ Costco      │        │
│    └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘        │
│                            │                                                  │
│                     ChromaDB + LLM                                            │
└────────────────────────────┼──────────────────────────────────────────────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │ AggregateResults│
                    │ (ZIP Lambda)    │
                    └─────────────────┘
```

## Components

### 1. Zip Lambda: `prepare_labels`

**Purpose**: Fast, lightweight Lambda that queries DynamoDB and prepares batch files.

**Dependencies**: DynamoDB layer only

**Actions**:
1. Query DynamoDB using GSI1 (`get_receipt_word_labels_by_label`)
2. Batch fetch merchant names and word text
3. Group by merchant
4. Upload to S3 as NDJSON files

**Configuration**:
- Runtime: Python 3.12
- Memory: 512 MB
- Timeout: 5 minutes
- Architecture: ARM64

### 2. Container Lambda: `harmonize_labels`

**Purpose**: Heavy-lifting Lambda with ChromaDB, embeddings, and LLM.

**Dependencies** (via pyproject.toml):
- `receipt_agent` → LabelHarmonizerV2, LabelRecord, MerchantLabelGroup
  - Brings: langgraph, langchain-ollama, langsmith, httpx
  - **Note**: Uses V2 which has proper LangSmith trace nesting (all LLM calls under single trace)
- `receipt_chroma` → ChromaClient
  - Brings: chromadb, openai
- `receipt_dynamo` → DynamoClient
  - Brings: boto3, requests

**Actions**:
1. Download ChromaDB snapshot from S3 (cached in /tmp)
2. Stream labels from S3 NDJSON file
3. Run harmonization with LLM-based outlier detection (properly traced in LangSmith)
4. Upload results to S3

**Configuration**:
- Memory: 3 GB (for ChromaDB + LLM)
- Timeout: 15 minutes
- Ephemeral storage: 2 GB
- Architecture: ARM64

### 3. Zip Lambda: `aggregate_results`

**Purpose**: Combine all results and generate summary report.

**Configuration**:
- Memory: 256 MB
- Timeout: 2 minutes

## Usage

### Deploy

```bash
cd infra
pulumi up
```

### Start Execution

```bash
aws stepfunctions start-execution \
  --state-machine-arn arn:aws:states:us-west-2:xxx:stateMachine:label-harmonizer-sf \
  --input '{"dry_run": true}'
```

### Input Parameters

```json
{
  "dry_run": true,           // Don't update DynamoDB (default: true)
  "similarity_threshold": 0.70  // ChromaDB similarity threshold
}
```

### Monitor Execution

```bash
# List executions
aws stepfunctions list-executions \
  --state-machine-arn arn:aws:states:us-west-2:xxx:stateMachine:label-harmonizer-sf

# Get execution history
aws stepfunctions get-execution-history \
  --execution-arn arn:aws:states:us-west-2:xxx:execution:label-harmonizer-sf:xxx
```

## S3 Structure

```
harmonizer-batch-bucket/
├── batches/
│   └── {execution_id}/
│       ├── GRAND_TOTAL/
│       │   ├── sprouts-farmers-market.ndjson
│       │   ├── walmart.ndjson
│       │   └── ...
│       ├── MERCHANT_NAME/
│       │   └── ...
│       └── ...
├── results/
│   └── {execution_id}/
│       ├── GRAND_TOTAL/
│       │   ├── sprouts-farmers-market.json
│       │   └── ...
│       └── ...
└── reports/
    └── {execution_id}/
        ├── summary.json
        └── all_outliers.json
```

## Performance Estimates

| Phase | Concurrency | Time per Unit | Total Items | Estimated Time |
|-------|-------------|---------------|-------------|----------------|
| Prepare | 16 parallel | 30s per label type | 18 | ~30s |
| Process | 4 parallel | 2-10min per merchant | ~100 merchants | ~50-100min |
| Aggregate | 1 | 30s | 1 | ~30s |

**Total estimated time**: 1-2 hours for full harmonization run

## Error Handling

### Retry Policies

- **Prepare Lambda**: 3 retries, 2s backoff, 2.0 rate
- **Harmonize Lambda**: 2 retries, 5s backoff, 2.0 rate
- **Ollama Rate Limit**: 5 retries, 30s backoff, 1.5 rate

### Error States

- `PrepareFailed`: Failed to prepare labels for one or more label types
- `ProcessFailed`: Failed to process one or more merchant groups

## Environment Variables

### Zip Lambdas (prepare_labels, aggregate_results)

| Variable | Description |
|----------|-------------|
| `DYNAMODB_TABLE_NAME` | DynamoDB table name |
| `BATCH_BUCKET` | S3 bucket for batch files |

### Container Lambda (harmonize_labels)

Uses `receipt_agent` which requires `RECEIPT_AGENT_*` prefixed environment variables via pydantic-settings.

| Variable | Description |
|----------|-------------|
| `BATCH_BUCKET` | S3 bucket for batch files (Lambda-specific) |
| `CHROMADB_BUCKET` | S3 bucket with ChromaDB snapshots (Lambda-specific) |
| `RECEIPT_AGENT_DYNAMO_TABLE_NAME` | DynamoDB table name |
| `RECEIPT_AGENT_OPENAI_API_KEY` | OpenAI API key for embeddings |
| `RECEIPT_AGENT_OLLAMA_API_KEY` | Ollama API key for LLM |
| `RECEIPT_AGENT_OLLAMA_BASE_URL` | Ollama API base URL (default: https://ollama.com) |
| `RECEIPT_AGENT_OLLAMA_MODEL` | Ollama model name (default: llama3.2) |
| `RECEIPT_AGENT_CHROMA_PERSIST_DIRECTORY` | ChromaDB local path (/tmp/chromadb) |
| `LANGCHAIN_API_KEY` | LangSmith API key for tracing |
| `LANGCHAIN_TRACING_V2` | Enable LangSmith tracing (true) |
| `LANGCHAIN_ENDPOINT` | LangSmith API endpoint |
| `LANGCHAIN_PROJECT` | LangSmith project name (label-harmonizer) |

## Dependency Chain

```
receipt_agent (pyproject.toml)
├── receipt-dynamo (boto3, requests)
├── receipt-chroma (chromadb, openai, receipt-dynamo)
├── receipt-places
├── langgraph>=0.2.0
├── langchain-core>=0.3.0
├── langchain-ollama>=0.2.0
├── langsmith>=0.1.0
└── httpx>=0.27.0
```

The container Lambda installs packages from their `pyproject.toml` files, **not** from a `requirements.txt`:

```dockerfile
RUN pip install --no-cache-dir /tmp/receipt_dynamo && \
    pip install --no-cache-dir /tmp/receipt_chroma && \
    pip install --no-cache-dir /tmp/receipt_agent
```

## Files

```
infra/label_harmonizer_step_functions/
├── __init__.py                    # Package exports
├── infrastructure.py              # Main Pulumi component
├── README.md                      # This file
├── handlers/
│   ├── prepare_labels.py          # Zip Lambda handler
│   └── aggregate_results.py       # Zip Lambda handler
└── lambdas/
    ├── Dockerfile                 # Container Lambda image (no requirements.txt!)
    └── harmonize_labels.py        # Container Lambda handler
```

