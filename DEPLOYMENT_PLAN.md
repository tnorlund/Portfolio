# Container-Based process_ocr_results Deployment Plan

## Current State

### What's Committed (but NOT deployed):
- ✅ Container-based Lambda handler (`infra/upload_images/container_ocr/handler/`)
  - `handler.py` - Main orchestrator
  - `ocr_processor.py` - OCR parsing and DynamoDB storage
  - `embedding_processor.py` - Merchant validation + embedding creation
- ✅ Dockerfile with `receipt_dynamo`, `receipt_label[full]`, `receipt_upload`
- ✅ Pulumi infrastructure (`infra/upload_images/infra.py` lines 984-996)
  - Uses `CodeBuildDockerImage` component
  - Configured with ChromaDB bucket, Chroma HTTP endpoint, API keys

### What's Currently Deployed (OLD):
```
Lambda: upload-images-dev-process-ocr-results
  PackageType: Zip (NOT Image!)
  Runtime: python3.12
```

This is the **OLD** zip-based Lambda that:
- ❌ Does NOT validate merchant
- ❌ Does NOT create embeddings
- ❌ Does NOT write ChromaDB deltas to S3
- ❌ Does NOT create COMPACTION_RUN records
- ✅ Only exports NDJSON and queues to `embed_ndjson_queue` (old flow)

## What Will Happen After Deployment

### New Container-Based Lambda Will:
1. **Process OCR** (`ocr_processor.py`)
   - Download OCR JSON from S3
   - Parse into LINE/WORD/LETTER entities
   - Classify image type (NATIVE/PHOTO/SCAN)
   - Store entities in DynamoDB

2. **Validate Merchant** (`embedding_processor.py`)
   - Fetch lines/words from DynamoDB
   - Query ChromaDB for similar merchants
   - Validate with Google Places API
   - Create `ReceiptMetadata` in DynamoDB

3. **Create Embeddings** (`embedding_processor.py`)
   - Generate embeddings with merchant context
   - Create local ChromaDB deltas (in `/tmp`)
   - Upload zipped deltas to S3 (`s3://chromadb-bucket/lines/delta/{run_id}/`)

4. **Trigger Compaction** (`embedding_processor.py`)
   - Create `COMPACTION_RUN` record in DynamoDB
   - DynamoDB stream → stream processor → SQS → compaction Lambda
   - Compaction Lambda merges deltas from S3 to ChromaDB on EFS

## Infrastructure Changes

### Will Be Created:
- ECR Repository: `upload-images-process-ocr-image-repo-{hash}`
- CodePipeline: `upload-images-process-ocr-image-pipeline-{hash}`
- CodeBuild Project: For building Docker image
- Lambda Function: **Updated** to use Image (not Zip)

### Environment Variables (Already Configured):
```python
DYNAMO_TABLE_NAME
S3_BUCKET
RAW_BUCKET
SITE_BUCKET
ARTIFACTS_BUCKET
OCR_JOB_QUEUE_URL
OCR_RESULTS_QUEUE_URL
CHROMADB_BUCKET          # ← NEW: For delta uploads
CHROMA_HTTP_ENDPOINT     # ← NEW: For merchant resolution
GOOGLE_PLACES_API_KEY    # ← NEW: For merchant validation
OPENAI_API_KEY           # ← NEW: For embeddings
```

## Deployment Steps

1. **Run Pulumi Preview**
   ```bash
   cd /Users/tnorlund/GitHub/example/infra
   pulumi preview
   ```
   - Should show: ECR repo creation, CodePipeline creation, Lambda update

2. **Deploy Infrastructure**
   ```bash
   pulumi up
   ```
   - Creates ECR repo
   - Creates CodePipeline
   - Updates Lambda to use Image package type
   - Triggers initial Docker build

3. **Wait for CodePipeline**
   - Monitor: `aws codepipeline list-pipeline-executions --pipeline-name upload-images-process-ocr-image-pipeline-*`
   - Should build Docker image and update Lambda

4. **Verify Deployment**
   ```bash
   aws lambda get-function --function-name upload-images-dev-process-ocr-results \
     --query '{PackageType: Configuration.PackageType, ImageUri: Code.ImageUri}'
   ```
   - Should show `PackageType: Image`

5. **Test End-to-End**
   - Upload image via Next.js site
   - Check CloudWatch logs for `upload-images-dev-process-ocr-results`
   - Should see:
     - "Processing OCR for image..."
     - "Merchant resolved: ..."
     - "Uploading line delta to s3://..."
     - "COMPACTION_RUN created: run_id=..."
   - Check DynamoDB for `COMPACTION_RUN` record
   - Check stream processor logs for compaction queue message
   - Check compaction Lambda logs for delta merge

## Rollback Plan

If deployment fails or Lambda has issues:

1. **Revert to Old Zip-Based Lambda**
   ```bash
   git revert HEAD~4..HEAD  # Revert container migration commits
   pulumi up
   ```

2. **Check Old Lambda Still Works**
   - Old Lambda will resume NDJSON export + queue to `embed_ndjson_queue`
   - `embed_from_ndjson` Lambda will handle merchant validation

## Benefits After Deployment

1. **Faster**: Single Lambda instead of 2 (no SQS hop)
2. **Simpler**: One container instead of zip + separate embedding Lambda
3. **Atomic**: OCR → Merchant → Embeddings → COMPACTION_RUN in one transaction
4. **Cheaper**: Fewer Lambda invocations, less SQS traffic
5. **EFS-Ready**: Deltas go to S3, compaction Lambda merges to EFS

## Next Steps After Successful Deployment

1. **Deprecate Old Flow**
   - Remove `embed_ndjson_queue` (no longer needed)
   - Remove `embed_from_ndjson` Lambda (functionality merged)
   - Remove NDJSON export logic from old `process_ocr_results.py`

2. **Monitor Metrics**
   - Lambda duration (should be < 60s for most receipts)
   - ChromaDB delta size
   - Compaction success rate

3. **Test Edge Cases**
   - Multi-receipt images (PHOTO/SCAN types)
   - Merchant resolution failures
   - ChromaDB unavailable scenarios

