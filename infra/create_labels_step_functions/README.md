# Create Labels Step Function

## Overview

This step function processes all receipts and creates/updates labels with **PENDING** validation status using LangGraph and Ollama.

## Purpose

- **Runs against all receipts** in the database
- **Creates new labels** suggested by LangGraph/Ollama workflow
- **Updates existing labels** based on comparison logic
- **Sets all labels to PENDING** validation status (for later validation)

## Workflow

```
ListReceipts (Lambda)
  → HasReceipts? (Choice)
  → ForEachReceipt (Map, MaxConcurrency: 10)
    → CreateLabels (Lambda) - Runs LangGraph, sets PENDING, saves
  → Done
```

## How It Works

1. **List Receipts**: Lists all receipts from DynamoDB
2. **Process Each Receipt**:
   - Runs `analyze_receipt_simple()` with LangGraph/Ollama
   - Gets suggested labels (uses comparison logic from `combine_results()`)
   - Sets all labels to **PENDING** validation status
   - Saves labels to DynamoDB (adds new labels, updates existing ones)

## Comparison Logic

The step function uses the built-in comparison logic from `combine_results()` in `currency_validation.py`:

- **Labels to Add**: Labels that don't exist in DynamoDB yet
- **Labels to Update**: Labels that exist but need updates (reasoning changes, etc.)

This is the same logic used by `dev.reverify_word_labels_cove.py` and `dev.process_all_receipts_cove.py`.

## Configuration

- **Max Concurrency**: 10 receipts in parallel
- **Timeout**: 600 seconds per receipt (10 minutes)
- **Memory**: 1536 MB
- **Environment**:
  - `OLLAMA_API_KEY` (from Pulumi secrets)
  - `LANGCHAIN_API_KEY` (for LangSmith tracing)
  - `LANGCHAIN_PROJECT`: "create-labels"

## Usage

The step function can be triggered manually via AWS Console or programmatically:

```python
import boto3

sfn = boto3.client('stepfunctions')
response = sfn.start_execution(
    stateMachineArn='arn:aws:states:...:create_labels_state_machine',
    input='{}'  # No input needed - lists all receipts
)
```

## Differences from Currency Validation Step Function

| Feature | Currency Validation | Create Labels |
|---------|---------------------|---------------|
| **Purpose** | Validates currency labels | Creates/updates all labels |
| **Validation Status** | Uses CoVe verification (VALID/INVALID) | Always sets PENDING |
| **Use Case** | Periodic validation | Initial label creation |

## Related Step Functions

- **Currency Validation**: Validates currency labels with CoVe
- **Validate Pending Labels**: Validates existing PENDING labels
- **Create Labels** (this one): Creates/updates labels with PENDING status

## Next Steps

After running this step function, labels will be created/updated with PENDING status. You can then:

1. Run **Validate Pending Labels** step function to validate them
2. Labels will be marked as VALID, INVALID, or NEEDS_REVIEW

