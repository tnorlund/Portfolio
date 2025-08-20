# Embedding Step Functions Infrastructure

This directory contains a sophisticated AWS-based embedding pipeline infrastructure that processes receipt data through OpenAI's embedding API and stores results in ChromaDB vector databases. The system is designed for high-throughput, cost-effective processing of both line-level and word-level embeddings.

## Architecture Overview

The embedding pipeline follows a **hybrid Lambda architecture** that optimizes for both performance and cost:

- **Zip-based Lambdas**: Handle simple operations (discovery, submission, chunking) with fast cold starts
- **Container-based Lambdas**: Handle complex operations (ChromaDB operations, result processing) with full library access

## Directory Structure

```
infra/embedding_step_functions/
├── components/                    # Modular Pulumi infrastructure components
│   ├── base.py                   # Shared configurations and base classes
│   ├── docker_image.py           # ECR repository and container management
│   ├── lambda_functions.py       # Lambda function creation and configuration
│   ├── line_workflow.py          # Step Functions for line embedding workflows
│   └── word_workflow.py          # Step Functions for word embedding workflows
├── simple_lambdas/               # Zip-based lightweight Lambda functions
│   ├── find_unembedded/          # Discovers items needing embeddings
│   ├── find_unembedded_words/    # Discovers words needing embeddings
│   ├── list_pending/             # Lists pending OpenAI batch jobs
│   ├── split_into_chunks/        # Splits results for parallel processing
│   ├── submit_openai/            # Submits line batches to OpenAI
│   └── submit_words_openai/      # Submits word batches to OpenAI
├── unified_embedding/            # Container-based complex operations
│   ├── handlers/                 # Individual business logic handlers
│   │   ├── compaction.py         # ChromaDB vector database compaction
│   │   ├── line_polling.py       # Polls OpenAI for line embedding results
│   │   └── word_polling.py       # Polls OpenAI for word embedding results
│   ├── utils/                    # Shared utilities
│   │   ├── aws_clients.py        # AWS service client management
│   │   ├── logging.py            # Centralized logging configuration
│   │   └── response.py           # Response formatting for different contexts
│   ├── config.py                 # Centralized configuration management
│   ├── handler.py                # Main Lambda entry point
│   └── router.py                 # Request routing based on handler type
└── infrastructure.py             # Main Pulumi infrastructure orchestration
```

## Core Components

### 1. Infrastructure Orchestration (`infrastructure.py`)

The `EmbeddingInfrastructure` Pulumi component orchestrates the entire system:

- **DockerImageComponent**: Manages ECR repository and builds container images
- **LambdaFunctionsComponent**: Creates and configures both zip and container-based Lambda functions
- **LineEmbeddingWorkflow**: Step Functions for processing line embeddings
- **WordEmbeddingWorkflow**: Step Functions for processing word embeddings
- **ChromaDB Integration**: S3 buckets, SQS queues, and vector database operations

### 2. Step Functions Workflows

#### Line Embedding Workflows
- **Submission Workflow** (`create-batches-sf`): FindUnembedded → SubmitBatches (parallel)
- **Ingestion Workflow** (`poll-store-sf`): ListPending → PollBatches → SplitIntoChunks → ProcessChunks → FinalMerge

#### Word Embedding Workflows
- **Submission**: `create-word-batches-sf`
- **Ingestion**: `poll-word-embeddings-sf`

Both workflows use sophisticated error handling, retry logic, and parallel processing with configurable concurrency limits.

### 3. Lambda Functions

#### Zip-based Functions (Fast cold starts, simple operations)
- `embedding-list-pending`: Lists pending OpenAI batch jobs
- `embedding-line-find`: Identifies receipt lines needing embeddings
- `embedding-word-find`: Identifies words needing embeddings
- `embedding-line-submit`: Submits line batches to OpenAI API
- `embedding-word-submit`: Submits word batches to OpenAI API
- `embedding-split-chunks`: Splits results into 10-item chunks for parallel processing

#### Container-based Functions (Complex operations, full library access)
- `embedding-line-poll`: Polls OpenAI for completed line embeddings (3GB memory)
- `embedding-word-poll`: Polls OpenAI for completed word embeddings (3GB memory)
- `embedding-vector-compact`: Merges ChromaDB deltas into snapshots (8-10GB memory)

## Data Flow Pipeline

### 1. Discovery Phase
The system uses DynamoDB queries to identify items requiring embeddings:
- **Lines**: Receipt lines without `line_embedding_id`
- **Words**: Individual words without `word_embedding_id`

### 2. Batch Creation and Submission
Items are grouped into optimal batch sizes (100-300 items) and submitted to OpenAI's Batch API as NDJSON files stored in S3.

### 3. Polling and Processing
The system polls OpenAI for batch completion and processes results:
- Downloads completed embeddings
- Stores them as delta files in S3 ChromaDB buckets
- Triggers compaction when delta accumulation reaches thresholds

### 4. ChromaDB Integration
Vector embeddings are stored in ChromaDB collections:
- **receipt_lines**: Line-level embeddings
- **receipt_words**: Word-level embeddings

The system uses a **delta-snapshot architecture**:
- **Delta files**: Individual batch results stored as `/delta/UUID.tar.gz`
- **Snapshots**: Persistent ChromaDB instances stored in `/snapshot/`
- **Compaction**: SQS-triggered process that merges deltas into snapshots

## Dependencies on Receipt Packages

### receipt_dynamo Package
- **DynamoClient**: Standardized DynamoDB access patterns used in compaction handler
- Provides consistent client management and query operations

### receipt_label Package
The embedding infrastructure heavily leverages the receipt_label package:

#### Core Embedding Logic
- `receipt_label.embedding.line.*`: Line embedding operations (find, submit, poll, store)
- `receipt_label.embedding.word.*`: Word embedding operations
- `receipt_label.embedding.common.*`: Shared utilities (batch status, retry logic)

#### Utility Functions
- `receipt_label.utils.get_client_manager`: AWS client management
- `receipt_label.utils.lock_manager.LockManager`: Distributed locking for ChromaDB operations

#### Key Integration Points
- OpenAI API interaction and batch management
- ChromaDB vector database operations
- Status tracking and error handling
- Distributed locking for safe concurrent operations

## Memory Optimization Architecture

The system implements sophisticated memory management to handle large embedding workloads:

### Sequential Processing
- Processes one delta at a time to minimize memory usage
- Explicit garbage collection between operations
- Resource cleanup after each processing cycle

### Chunked Processing
- Groups 25 deltas per chunk for parallel processing
- Two-phase compaction: parallel processing + locked final merge
- Dynamic memory allocation based on workload size

### Configuration-Driven Optimization
The `config.py` file provides centralized memory and timeout configurations:

```python
LAMBDA_CONFIGS = {
    "compaction": {
        "memory": 10240,  # Maximum memory for large batches
        "timeout": 900,
        "ephemeral_storage": 10240,
        "env_vars": {
            "USE_SEQUENTIAL_PROCESSING": "true",
            "HEARTBEAT_INTERVAL_SECONDS": "60"
        }
    }
}
```

## Error Handling and Monitoring

### Intelligent Response Formatting
The system provides context-aware error handling:
- **Step Function context**: Raises exceptions for proper workflow error handling
- **API Gateway context**: Returns structured HTTP responses
- **Direct invocation**: Returns appropriate status codes

### Distributed Locking
Uses DynamoDB-based distributed locking to prevent corruption during compaction:
- TTL-based lock expiration
- Heartbeat mechanism for long-running operations
- Graceful error recovery

### Structured Logging
CloudWatch-compatible logging with:
- Consistent formatting across all handlers
- Context-aware log levels
- Structured data for monitoring and alerting

## Key Environment Variables

### Common Configuration
- `DYNAMODB_TABLE_NAME`: Batch metadata storage
- `CHROMADB_BUCKET`: Vector database storage bucket
- `COMPACTION_QUEUE_URL`: SQS queue for triggering compaction
- `OPENAI_API_KEY`: OpenAI API authentication
- `S3_BUCKET`: NDJSON batch file storage

### Handler-Specific
- `HANDLER_TYPE`: Routes container requests to appropriate handler
- `CHROMA_PERSIST_DIRECTORY`: ChromaDB local storage path
- `SKIP_SQS_NOTIFICATION`: Prevents redundant compaction triggers

## Architecture Highlights

1. **Cost Optimization**: ARM64 architecture, hybrid Lambda strategy, lifecycle policies
2. **Fault Tolerance**: Distributed locking, retry logic, graceful error handling
3. **Scalability**: Parallel processing with configurable concurrency limits
4. **Memory Efficiency**: Sequential processing, explicit cleanup, dynamic allocation
5. **Observability**: Structured logging, status tracking, comprehensive monitoring

## Usage Patterns

### Manual Execution
```bash
# Trigger line embedding batch creation
aws stepfunctions start-execution \
  --state-machine-arn arn:aws:states:region:account:stateMachine:create-batches-sf \
  --input '{}'

# Trigger compaction
aws sqs send-message \
  --queue-url $COMPACTION_QUEUE_URL \
  --message-body '{"collection": "receipt_lines"}'
```

### Monitoring
```bash
# Check Step Function executions
aws stepfunctions list-executions \
  --state-machine-arn arn:aws:states:region:account:stateMachine:poll-store-sf

# Monitor Lambda memory usage
aws logs filter-log-events \
  --log-group-name /aws/lambda/embedding-vector-compact \
  --filter-pattern "REPORT"
```

This infrastructure represents a production-ready, scalable embedding pipeline that efficiently processes large volumes of receipt data while maintaining cost-effectiveness and reliability.