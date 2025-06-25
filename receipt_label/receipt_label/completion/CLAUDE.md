# Receipt Label Completion Module

This module handles the validation of receipt word labels using OpenAI's batch completion API.

## Architecture Overview

The module consists of two main pipelines that work together:

### 1. Submit Pipeline (`submit.py`)
**Purpose**: Prepare and submit label validation requests to OpenAI

**Key Functions**:
- `list_labels_that_need_validation()` - Find labels with validation_status=NONE
- `chunk_into_completion_batches()` - Group by (image_id, receipt_id)
- `format_batch_completion_file()` - Create OpenAI-formatted NDJSON
- `merge_ndjsons()` - Combine into ≤50k lines/100MB batches
- `submit_openai_batch()` - Submit to OpenAI Batch API

**State Changes**:
- Labels: NONE → PENDING
- Creates BatchSummary with status=PENDING

### 2. Poll Pipeline (`poll.py`)
**Purpose**: Check batch status and process completed validations

**Key Functions**:
- `list_pending_completion_batches()` - Find BatchSummary with status=PENDING
- `get_openai_batch_status()` - Check if batch is completed
- `download_openai_batch_result()` - Parse OpenAI results
- `update_valid_labels()` - Mark valid labels, update Pinecone
- `update_invalid_labels()` - Handle invalid labels, create corrections

**State Changes**:
- Labels: PENDING → VALID/INVALID/NEEDS_REVIEW
- BatchSummary: PENDING → COMPLETED
- Creates new labels for OpenAI suggestions

## Infrastructure Integration

The pipelines are deployed as AWS Step Functions in `infra/validation_pipeline/`:
- `infra.py` - Step Function definitions
- `lambda.py` - Lambda handler implementations

The Step Functions orchestrate the pipeline stages:
1. **Submit**: SubmitCompletionList → FormatNDJSONs (Map) → BatchLarger
2. **Poll**: ListPendingBatches → PollCompletionDownload (Map)

## Common Tasks

### Update Pipeline Documentation
The README files use Mermaid diagrams to visualize the flow. When updating:
1. Check actual implementation in `infra/validation_pipeline/lambda.py`
2. Update both function descriptions and usage sections
3. Ensure Mermaid diagrams match the actual step function flow

### Add New Validation Logic
1. Modify the prompt generation in `format_batch_completion_file()`
2. Update result parsing in `download_openai_batch_result()`
3. Adjust label update logic if needed

### Debug Failed Batches
1. Check BatchSummary status in DynamoDB
2. Use `get_openai_batch_status()` to check OpenAI status
3. Look for timeout issues (MAX_BATCH_TIMEOUT=300s default)

## Testing
- Unit tests should mock OpenAI API calls
- Integration tests use the full Step Function flow
- Check for existing test patterns in the codebase