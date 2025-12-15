# ChromaDB Version Update Testing Guide

## Overview

This document outlines the testing strategy for updating ChromaDB from 1.0.0 to 1.3.4 in AWS.

## Pre-Deployment Checklist

- [x] Updated `receipt_label/pyproject.toml`: `chromadb>=1.3.4`
- [x] Updated `infra/merchant_validation_container/requirements.txt`: `chromadb>=1.3.4`
- [x] Tested migration script locally
- [x] Verified compatibility between 1.3.2 → 1.3.4 (no migration needed)

## Deployment Steps

### Step 1: Deploy to Dev Environment

```bash
# Update dependencies
cd receipt_label
pip install -e '.[full]'  # This will install chromadb>=1.3.4

# Deploy infrastructure
cd ../infra
pulumi up --stack dev
```

This will:
- Build new Docker images with ChromaDB 1.3.4
- Deploy Lambda functions with new layer versions
- Update ECS tasks with new container images

### Step 2: Verify Deployment

Check that new versions are deployed:

```bash
# Check Lambda functions
aws lambda list-functions --query 'Functions[?contains(FunctionName, `chromadb`) || contains(FunctionName, `compaction`)].{Name:FunctionName, Runtime:Runtime, LastModified:LastModified}' --output table

# Check ECS tasks
aws ecs list-tasks --cluster <your-cluster> --service-name <your-service>
```

## Testing Strategy

### Test 1: Read Existing Database (No Migration)

**Goal**: Verify that ChromaDB 1.3.4 can read existing databases in S3/EFS.

**Steps**:

1. **Trigger a query operation** (read-only):
   ```bash
   # This should use existing snapshot from S3/EFS
   # Test via API or Lambda function that queries ChromaDB
   ```

2. **Check CloudWatch logs** for:
   - No errors reading database
   - Successful collection access
   - Query results returned

3. **Verify in Lambda logs**:
   ```bash
   aws logs tail /aws/lambda/<your-query-function> --follow
   ```

   Look for:
   - ✅ "Successfully retrieved existing collection"
   - ✅ Query results with data
   - ❌ Any errors about database format

### Test 2: Write to Database (Create New Records)

**Goal**: Verify that new writes work with ChromaDB 1.3.4.

**Steps**:

1. **Upload a new receipt/image**:
   ```bash
   # Use your upload API or trigger a test upload
   ```

2. **Monitor the embedding pipeline**:
   - Embedding Lambda should create new vectors
   - Compaction Lambda should merge them into snapshot
   - Check that new records appear in queries

3. **Verify in CloudWatch**:
   ```bash
   # Check embedding Lambda
   aws logs tail /aws/lambda/<embedding-function> --follow

   # Check compaction Lambda
   aws logs tail /aws/lambda/<compaction-function> --follow
   ```

### Test 3: Update Existing Labels (Write to Existing Records)

**Goal**: Verify that updating existing records works (your specific test case).

**Steps**:

1. **Update a label on an existing receipt**:
   ```bash
   # Use your API or trigger a label update
   # This should update metadata in ChromaDB
   ```

2. **Monitor the update process**:
   ```bash
   # Check stream processor Lambda (if using DynamoDB streams)
   aws logs tail /aws/lambda/<stream-processor> --follow

   # Check compaction Lambda (if metadata updates trigger compaction)
   aws logs tail /aws/lambda/<compaction-function> --follow
   ```

3. **Verify the update**:
   - Query the updated record
   - Confirm new label values are present
   - Check that old values are replaced correctly

### Test 4: Full Workflow Test

**Goal**: Test complete end-to-end flow.

**Steps**:

1. **Upload new receipt** → Creates embeddings
2. **Query receipt** → Verifies read works
3. **Update labels** → Verifies write works
4. **Query again** → Verifies updates persisted
5. **Trigger compaction** → Verifies compaction works with new version

## Monitoring During Testing

### CloudWatch Metrics to Watch

1. **Lambda Errors**:
   ```bash
   aws cloudwatch get-metric-statistics \
     --namespace AWS/Lambda \
     --metric-name Errors \
     --dimensions Name=FunctionName,Value=<your-function> \
     --start-time <start-time> \
     --end-time <end-time> \
     --period 300 \
     --statistics Sum
   ```

2. **Lambda Duration**:
   - Ensure no significant performance degradation

3. **EFS I/O**:
   ```bash
   aws cloudwatch get-metric-statistics \
     --namespace AWS/EFS \
     --metric-name ClientConnections \
     --dimensions Name=FileSystemId,Value=<efs-id> \
     --start-time <start-time> \
     --end-time <end-time> \
     --period 300 \
     --statistics Sum
   ```

### Log Patterns to Watch For

**Success Indicators**:
- "Successfully retrieved existing collection"
- "ChromaDB version: 1.3.4" (in logs)
- Query results with data
- Successful upsert operations

**Failure Indicators**:
- "Failed to read database"
- "Database format error"
- "Collection not found" (when it should exist)
- Rust/HNSW related errors
- Import/export errors

## Rollback Plan

If issues occur:

1. **Revert code changes**:
   ```bash
   git revert <commit-hash>
   ```

2. **Redeploy previous version**:
   ```bash
   pulumi up --stack dev
   ```

3. **Verify rollback**:
   - Check that old ChromaDB version is deployed
   - Verify services work again

## Post-Deployment Validation

After successful deployment:

1. **Verify version in deployed containers**:
   ```bash
   # SSH into ECS task or check Lambda logs
   # Should see: chromadb.__version__ == '1.3.4'
   ```

2. **Run full test suite** (if available):
   ```bash
   pytest tests/integration/test_chromadb.py
   ```

3. **Monitor for 24-48 hours**:
   - Watch error rates
   - Monitor performance metrics
   - Check user reports

## Specific Test: Update Label Workflow

### Manual Test Steps

1. **Find an existing receipt** in your system
2. **Get its ID** (image_id, receipt_id, etc.)
3. **Update a label** via API:
   ```bash
   curl -X POST https://<your-api>/receipts/<id>/labels \
     -H "Content-Type: application/json" \
     -d '{"label": "new_value"}'
   ```

4. **Verify update**:
   ```bash
   # Query the receipt
   curl https://<your-api>/receipts/<id>

   # Should show updated label
   ```

5. **Check ChromaDB directly** (if possible):
   - Query the collection
   - Verify metadata contains new label
   - Verify old label is replaced

### Automated Test Script

```python
# test_label_update.py
import boto3
import requests

def test_label_update():
    # 1. Find existing receipt
    receipt_id = get_existing_receipt()

    # 2. Update label
    response = update_label(receipt_id, "test_label", "test_value")
    assert response.status_code == 200

    # 3. Verify update
    receipt = get_receipt(receipt_id)
    assert receipt["labels"]["test_label"] == "test_value"

    # 4. Query ChromaDB to verify
    # (This would require ChromaDB client access)
```

## Expected Behavior

### ✅ Success Indicators

- All queries return data
- New embeddings are created successfully
- Label updates persist
- Compaction runs without errors
- No database format errors in logs
- Performance is similar to previous version

### ❌ Failure Indicators

If you see any of these, migration may be needed:

- "Failed to open database"
- "Database format not supported"
- "HNSW index error"
- "Rust bindings error"
- Empty results when data should exist

## Next Steps After Successful Testing

1. **Document results** in this file
2. **Update production** following same process
3. **Monitor production** for 48 hours
4. **Archive old snapshots** (after verification period)

