# Reset and Resubmit Batch Summaries Plan

## Overview

This document outlines the plan to reset all word and line embedding statuses, export and delete batch summaries, and resubmit all lines and words for OpenAI batch embedding.

## Current State Analysis

### Existing Scripts

1. **`dev.reset_embedding_status.py`** ✅
   - **Purpose**: Resets embedding statuses and exports/deletes batch summaries
   - **What it does**:
     - Exports all `BatchSummary` records to NDJSON file (`dev.batch_summaries_{timestamp}.ndjson`)
     - Deletes all `BatchSummary` records from DynamoDB
     - Resets all `ReceiptLine.embedding_status` to `NONE`
     - Resets all `ReceiptWord.embedding_status` to `NONE`
   - **Usage**:
     ```bash
     # Dry run
     python dev.reset_embedding_status.py --stack dev --dry-run

     # Execute
     python dev.reset_embedding_status.py --stack dev
     ```

2. **`dev.reset_batch_status.py`**
   - **Purpose**: Resets batch summary statuses (e.g., COMPLETED → PENDING)
   - **Note**: Not needed if we're deleting all batch summaries

3. **`dev.set_all_batches_to_pending.py`**
   - **Purpose**: Sets all batch summaries to PENDING status
   - **Note**: Not needed if we're deleting all batch summaries

4. **`scripts/start_ingestion_dev.sh`**
   - **Purpose**: Triggers the **ingestion** step functions (polling and storing embeddings)
   - **Note**: This is for the ingestion workflow, not the submission workflow

### Step Functions

Based on the codebase analysis, there are two types of workflows:

#### Submission Workflows (What we need)
- **`line-submit-sf-{stack}`**: Finds unembedded lines and submits them to OpenAI
  - Step 1: `FindUnembedded` - Finds lines with `embedding_status = NONE`
  - Step 2: `SubmitBatches` - Submits batches to OpenAI in parallel
- **`word-submit-sf-{stack}`**: Finds unembedded words and submits them to OpenAI
  - Step 1: `FindUnembeddedWords` - Finds words with `embedding_status = NONE`
  - Step 2: `SubmitWordBatches` - Submits batches to OpenAI in parallel

#### Ingestion Workflows (Not what we need right now)
- **`line-ingest-sf-{stack}`**: Polls OpenAI and stores completed embeddings
- **`word-ingest-sf-{stack}`**: Polls OpenAI and stores completed embeddings

## Step-by-Step Plan

### Step 1: Export and Delete Batch Summaries + Reset Embedding Statuses

Use the existing `dev.reset_embedding_status.py` script:

```bash
# First, do a dry run to see what will be affected
python dev.reset_embedding_status.py --stack dev --dry-run

# Review the output, then execute
python dev.reset_embedding_status.py --stack dev
```

**What this does**:
- Exports all batch summaries to `dev.batch_summaries_{timestamp}.ndjson`
- Deletes all batch summaries from DynamoDB
- Sets all `ReceiptLine.embedding_status` to `NONE`
- Sets all `ReceiptWord.embedding_status` to `NONE`

**Expected output**:
- A timestamped NDJSON file with all batch summaries
- Summary showing how many batch summaries were deleted
- Summary showing how many lines and words were updated

### Step 2: Find Submission Step Function ARNs

The submission step functions are named:
- `line-submit-sf-dev` (or similar with stack suffix)
- `word-submit-sf-dev` (or similar with stack suffix)

**Option A: Using Pulumi** (if you have access):
```bash
cd infra
pulumi stack output --stack dev --json | grep -i "submit.*arn"
```

**Option B: Using AWS CLI**:
```bash
# Find line submission step function
aws stepfunctions list-state-machines \
  --query 'stateMachines[?contains(name, `line-submit`) && contains(name, `dev`)].stateMachineArn' \
  --output text

# Find word submission step function
aws stepfunctions list-state-machines \
  --query 'stateMachines[?contains(name, `word-submit`) && contains(name, `dev`)].stateMachineArn' \
  --output text
```

### Step 3: Trigger Submission Step Functions

Once you have the ARNs, trigger the step functions:

```bash
# Set variables (replace with actual ARNs from Step 2)
LINE_SUBMIT_ARN="arn:aws:states:us-east-1:ACCOUNT:stateMachine:line-submit-sf-dev-XXXXX"
WORD_SUBMIT_ARN="arn:aws:states:us-east-1:ACCOUNT:stateMachine:word-submit-sf-dev-XXXXX"

# Trigger line submission
aws stepfunctions start-execution \
  --state-machine-arn "$LINE_SUBMIT_ARN" \
  --name "reset-and-resubmit-lines-$(date +%s)"

# Trigger word submission
aws stepfunctions start-execution \
  --state-machine-arn "$WORD_SUBMIT_ARN" \
  --name "reset-and-resubmit-words-$(date +%s)"
```

**What these step functions do**:
1. Find all lines/words with `embedding_status = NONE`
2. Group them into batches
3. Serialize batches to S3
4. Submit batches to OpenAI Batch API
5. Create new `BatchSummary` records with status `PENDING`
6. Update line/word `embedding_status` to `PENDING`

### Step 4: Monitor Progress

Monitor the step function executions:

```bash
# Get execution ARNs from the output of Step 3, then:
EXECUTION_ARN="arn:aws:states:us-east-1:ACCOUNT:execution:..."

# Check status
aws stepfunctions describe-execution --execution-arn "$EXECUTION_ARN"

# View execution history
aws stepfunctions get-execution-history \
  --execution-arn "$EXECUTION_ARN" \
  --max-results 50 \
  --reverse-order
```

### Step 5: Verify Batch Summaries Created

After the submission step functions complete, verify new batch summaries were created:

```bash
# Check batch summaries (should show new PENDING batches)
python dev.reset_batch_status.py --status PENDING dev
```

## Automated Solution: Helper Script ✅

A helper script `dev.reset_and_resubmit_batch_summaries.py` has been created that automates the entire process:

**Usage**:
```bash
# Dry run (show what would happen)
python dev.reset_and_resubmit_batch_summaries.py --stack dev --dry-run

# Actually execute
python dev.reset_and_resubmit_batch_summaries.py --stack dev

# Only reset, don't trigger step functions
python dev.reset_and_resubmit_batch_summaries.py --stack dev --skip-submit

# Only trigger step functions (assumes reset already done)
python dev.reset_and_resubmit_batch_summaries.py --stack dev --skip-reset
```

**What it does**:
1. Calls `dev.reset_embedding_status.py` to export/delete batch summaries and reset embedding statuses
2. Automatically finds the submission step function ARNs using Pulumi outputs or AWS CLI
3. Triggers both line and word submission step functions
4. Provides monitoring commands for the executions

This is the **recommended approach** as it automates everything in one command.

## Important Notes

1. **Backup**: The `dev.reset_embedding_status.py` script already exports batch summaries before deleting them, so you have a backup.

2. **OpenAI Costs**: Resubmitting all lines and words will create new OpenAI batch jobs, which may incur costs. Make sure you're aware of this.

3. **Timing**: The submission step functions will create new batch summaries. After they complete, you'll need to run the **ingestion** step functions to poll OpenAI and store the embeddings.

4. **Existing OpenAI Batches**: If there are existing OpenAI batch jobs that are still processing, they will continue to run. The new batch summaries will reference new OpenAI batch jobs.

5. **Step Function Names**: The actual step function names may have suffixes (e.g., `line-submit-sf-dev-abc123`). Use the AWS CLI or Pulumi to find the exact names.

## Git History Context

Based on the git history, this branch (`feat/more_ollama`) has been used to develop the line and word ingestion step functions. The step functions are now working end-to-end, so resetting and resubmitting is a good way to start fresh with a clean state.

## Next Steps After Resubmission

After the submission step functions complete:

1. **Wait for OpenAI batches to complete** (can take hours depending on batch size)
2. **Run ingestion step functions** to poll OpenAI and store embeddings:
   ```bash
   ./scripts/start_ingestion_dev.sh both
   ```
3. **Monitor progress** in CloudWatch and Step Functions console

