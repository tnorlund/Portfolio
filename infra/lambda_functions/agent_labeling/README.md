# Agent Labeling Lambda Functions

This directory contains the Lambda functions that power the agent-based receipt labeling system.

## Overview

The agent labeling system uses a Step Function to orchestrate five Lambda functions that work together to intelligently label receipt words while minimizing GPT API calls.

## Lambda Functions

### 1. check_metadata

**Purpose**: Verifies that a receipt has the necessary metadata (merchant name, location) before proceeding with labeling.

**Input**:
```json
{
  "receipt_id": "receipt-123"
}
```

**Output**:
```json
{
  "found": true,
  "merchant_name": "Starbucks",
  "location": {"city": "Seattle", "state": "WA"},
  "missing_fields": []
}
```

### 2. check_embeddings

**Purpose**: Verifies that receipt word embeddings exist in Pinecone and optionally retrieves merchant-specific patterns.

**Input**:
```json
{
  "receipt_id": "receipt-123",
  "metadata": {
    "merchant_name": "Starbucks"
  }
}
```

**Output**:
```json
{
  "has_embeddings": true,
  "embedding_count": 150,
  "merchant_patterns": [
    {
      "pattern_type": "date_format",
      "confidence": 0.95,
      "example": "MM/DD/YYYY"
    }
  ]
}
```

### 3. run_agent

**Purpose**: Core labeling logic that runs pattern detection and makes smart decisions about GPT usage.

**Input**:
```json
{
  "receipt_id": "receipt-123",
  "metadata": {
    "merchant_name": "Starbucks",
    "location": {"city": "Seattle", "state": "WA"}
  },
  "embeddings": {
    "has_embeddings": true,
    "embedding_count": 150
  }
}
```

**Output**:
```json
{
  "pattern_labels": {
    "word-1": {
      "label": "DATE",
      "confidence": 0.98,
      "source": "pattern_detection"
    }
  },
  "gpt_required": true,
  "gpt_reason": "Missing essential labels: MERCHANT_NAME, GRAND_TOTAL",
  "essential_labels_found": ["DATE"],
  "missing_essential_labels": ["MERCHANT_NAME", "GRAND_TOTAL"],
  "unlabeled_count": 45,
  "total_words": 150,
  "processing_time_ms": 185
}
```

### 4. prepare_batch

**Purpose**: Prepares receipt data for batch processing via OpenAI's Batch API when GPT is needed.

**Input**:
```json
{
  "receipt_id": "receipt-123",
  "labeling": {
    "pattern_labels": {...},
    "missing_essential_labels": ["MERCHANT_NAME", "GRAND_TOTAL"]
  },
  "metadata": {...}
}
```

**Output**:
```json
{
  "batch_id": "batch_20240115_120000_abc123",
  "batch_size": 1,
  "estimated_tokens": 2500,
  "batch_data": {
    "batch_id": "batch_20240115_120000_abc123",
    "receipts": [...],
    "total_tokens": 2500
  }
}
```

### 5. store_labels

**Purpose**: Stores the final labels (from patterns and/or GPT) in DynamoDB as ReceiptWordLabel entities.

**Input**:
```json
{
  "receipt_id": "receipt-123",
  "labeling": {
    "pattern_labels": {...}
  },
  "gpt_labels": {
    "word-45": "MERCHANT_NAME",
    "word-123": "GRAND_TOTAL"
  },
  "batch": {
    "batch_id": "batch_20240115_120000_abc123"
  }
}
```

**Output**:
```json
{
  "labels_stored": 47,
  "errors": 0,
  "label_summary": {
    "DATE": 2,
    "MERCHANT_NAME": 1,
    "GRAND_TOTAL": 1,
    "PRODUCT_NAME": 15,
    "PRICE": 20
  },
  "total_labels": 47,
  "pattern_labels": 45,
  "gpt_labels": 2
}
```

## Environment Variables

All Lambda functions require these environment variables:

- `DYNAMO_TABLE_NAME`: Name of the DynamoDB table
- `OPENAI_API_KEY`: OpenAI API key (for run_agent)
- `PINECONE_API_KEY`: Pinecone API key (for check_embeddings, run_agent)
- `PINECONE_INDEX_NAME`: Pinecone index name
- `PINECONE_HOST`: Pinecone host URL
- `ENVIRONMENT`: Deployment environment (production, staging, dev)

## Error Handling

All Lambda functions include:
- Comprehensive error logging
- Proper exception propagation for Step Function error handling
- Retry logic handled by Step Function configuration

## Performance Targets

- **check_metadata**: < 100ms
- **check_embeddings**: < 200ms
- **run_agent**: < 200ms for pattern-only, < 5s with GPT
- **prepare_batch**: < 100ms
- **store_labels**: < 500ms for typical receipt

## Local Testing

Each Lambda function can be tested locally using the test events in the `test_events/` directory:

```bash
# Test individual handler
python -m pytest tests/lambda_functions/test_check_metadata.py

# Test with local event
python handler.py < test_events/check_metadata_event.json
```

## Integration with Step Functions

These Lambda functions are orchestrated by the `AgentLabelingStepFunction` defined in `infra/step_functions/agent_labeling.py`. The Step Function handles:

1. Conditional merchant validation
2. Parallel pattern detection
3. Smart GPT decision logic
4. Batch processing for efficiency
5. Error handling and retries
