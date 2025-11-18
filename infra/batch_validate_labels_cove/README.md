# Batch Validate Labels CoVe Step Function

## Overview

This step function batch validates PENDING and NEEDS_REVIEW `ReceiptWordLabel` entities using CoVe (Chain of Verification) templates. Instead of running full CoVe verification for each label, it reuses verification questions and answers from successful CoVe runs stored in DynamoDB.

## Purpose

- **Efficient validation**: Reuses CoVe verification questions from previous successful validations
- **Template-based approach**: Applies verification questions from similar labels (same label type, same merchant)
- **Auditability**: Stores full CoVe verification chains in DynamoDB for audit purposes
- **Scalability**: Processes receipts in parallel using AWS Step Functions

## Workflow

```
ListReceiptsWithPendingLabels (Lambda)
  ↓
HasReceipts? (Choice)
  ├─ receipts.length > 0 → ForEachReceipt (Map)
  └─ receipts.length = 0 → NoReceipts (End)
      ↓
ForEachReceipt (Map, MaxConcurrency: 3)
  └─ ValidateReceipt (Lambda) - Validates labels using CoVe templates
```

## How It Works

1. **List Receipts**: Queries DynamoDB for receipts that have PENDING or NEEDS_REVIEW labels
2. **For Each Receipt**:
   - Loads receipt data (lines, words, metadata)
   - Queries PENDING/NEEDS_REVIEW labels for that receipt
   - For each label:
     - Queries DynamoDB for CoVe verification templates (same label type, same merchant)
     - Applies verification questions from templates
     - Answers questions against receipt text using LLM
     - Determines if label is VALID or INVALID
     - Stores CoVe verification record in DynamoDB
   - Updates labels in DynamoDB with new validation status

## CoVe Template Reuse

The step function leverages CoVe verification records stored in DynamoDB:

- **Label Type Matching**: Finds CoVe verification records for the same label type (e.g., `GRAND_TOTAL`)
- **Merchant Matching**: Prefers CoVe verification records from the same merchant
- **Template Applicability Score**: Uses scores to select the best templates
- **Question Reuse**: Applies verification questions from successful CoVe runs

## Configuration

- **Max Concurrency**: 3 receipts in parallel (reduced to avoid Ollama rate limiting)
- **Timeout**: 600 seconds per receipt (10 minutes)
- **Memory**: 1536 MB
- **Environment**:
  - `OLLAMA_API_KEY` (from Pulumi secrets)
  - `LANGCHAIN_API_KEY` (for LangSmith tracing)
  - `LANGCHAIN_PROJECT`: "batch-validate-labels-cove"

## Usage

The step function can be triggered manually via AWS Console or programmatically:

```python
import boto3

sfn = boto3.client('stepfunctions')
response = sfn.start_execution(
    stateMachineArn='arn:aws:states:...:batch-validate-labels-cove-dev-sf',
    input='{}'  # No input needed - lists all receipts with PENDING/NEEDS_REVIEW labels
)
```

Optional input parameters:
```json
{
  "limit": 100,  // Optional: limit number of receipts to process
  "label_type": "GRAND_TOTAL"  // Optional: filter by label type
}
```

## Differences from Other Step Functions

| Feature | Create Labels | Validate Pending Labels | Batch Validate CoVe (this one) |
|---------|---------------|-------------------------|-------------------------------|
| **Purpose** | Creates/updates labels | Validates PENDING labels | Validates using CoVe templates |
| **Validation Method** | LangGraph/Ollama | ChromaDB + CoVe | CoVe templates only |
| **ChromaDB** | No | Yes | No |
| **CoVe Storage** | No | No | Yes (full chain) |
| **Template Reuse** | No | No | Yes |

## Related Components

- **CoVe Verification Storage**: `ReceiptWordLabelCoVeVerification` entities in DynamoDB
- **Core Label Definitions**: `ReceiptCoreLabelDefinition` entities in DynamoDB
- **Batch Validation Utils**: `receipt_label/langchain/utils/batch_validation_cove.py`
- **CoVe Storage Utils**: `receipt_label/langchain/utils/cove_storage.py`

## Next Steps

After running this step function:

1. Labels will be updated to VALID, INVALID, or remain PENDING/NEEDS_REVIEW
2. CoVe verification records will be stored in DynamoDB for future template reuse
3. You can query CoVe verification records to audit validation decisions

