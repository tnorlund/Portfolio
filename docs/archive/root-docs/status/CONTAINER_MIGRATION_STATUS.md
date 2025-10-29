# Container-Based process_ocr_results Migration Status

## ✅ What's Been Done

### 1. Code Implementation (Committed)
All code is ready and committed to `feat/efs_modular_rebase` branch:

- **Container Handler** (`infra/upload_images/container_ocr/handler/`)
  - `handler.py` - Main Lambda orchestrator
  - `ocr_processor.py` - OCR parsing and DynamoDB storage
  - `embedding_processor.py` - Merchant validation + embedding creation

- **Dockerfile** (`infra/upload_images/container_ocr/Dockerfile`)
  - Multi-stage build
  - Installs `receipt_dynamo`, `receipt_label[full]`, `receipt_upload`
  - Uses `public.ecr.aws/lambda/python:3.12` base image

- **Pulumi Infrastructure** (`infra/upload_images/infra.py` lines 984-996)
  - Uses `CodeBuildDockerImage` component
  - Configured with all required environment variables

### 2. What the New Lambda Does

The container-based Lambda combines OCR processing + merchant validation + embedding creation into a **single atomic operation**:

```
┌─────────────────────────────────────────────────────────────┐
│ Container-Based Lambda: process_ocr_results                 │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  1. OCR Processing (ocr_processor.py)                       │
│     ├─ Download OCR JSON from S3                            │
│     ├─ Parse into LINE/WORD/LETTER entities                 │
│     ├─ Classify image type (NATIVE/PHOTO/SCAN)              │
│     └─ Store entities in DynamoDB                           │
│                                                             │
│  2. Merchant Validation (embedding_processor.py)            │
│     ├─ Fetch lines/words from DynamoDB                      │
│     ├─ Query ChromaDB for similar merchants                 │
│     ├─ Validate with Google Places API                      │
│     └─ Create ReceiptMetadata in DynamoDB                   │
│                                                             │
│  3. Embedding Creation (embedding_processor.py)             │
│     ├─ Generate embeddings with merchant context            │
│     ├─ Create local ChromaDB deltas in /tmp                 │
│     └─ Upload zipped deltas to S3                           │
│        s3://chromadb-bucket/lines/delta/{run_id}/           │
│        s3://chromadb-bucket/words/delta/{run_id}/           │
│                                                             │
│  4. Trigger Compaction (embedding_processor.py)             │
│     └─ Create COMPACTION_RUN record in DynamoDB             │
│        ├─ run_id                                            │
│        ├─ lines_delta_prefix (S3 path)                      │
│        └─ words_delta_prefix (S3 path)                      │
│                                                             │
└─────────────────────────────────────────────────────────────┘
                           ↓
                 DynamoDB Stream Event
                           ↓
                  stream_processor.py
                           ↓
              Detects COMPACTION_RUN completion
              (lines_state=COMPLETED, words_state=COMPLETED)
                           ↓
                Queues message to SQS
                           ↓
             enhanced_compaction Lambda
                           ↓
        Downloads deltas from S3 → Merges to ChromaDB on EFS
```

## ⏳ What Needs to Happen (Deployment)

### Current State (NOT Deployed Yet)
```bash
$ aws lambda list-functions | grep process-ocr
upload-images-dev-process-ocr-results   Zip         python3.12
upload-images-prod-process-ocr-results  Zip         python3.12
```

The Lambda is still using the **OLD zip-based code** that:
- ❌ Does NOT validate merchant
- ❌ Does NOT create embeddings
- ❌ Does NOT write ChromaDB deltas
- ❌ Does NOT create COMPACTION_RUN
- ✅ Only exports NDJSON and queues to `embed_ndjson_queue` (old flow)

### Deployment Steps

1. **Cancel any in-progress Pulumi updates**
   ```bash
   cd /Users/tnorlund/GitHub/example/infra
   pulumi cancel --yes
   ```

2. **Deploy the new infrastructure**
   ```bash
   pulumi up --yes
   ```
   
   This will:
   - Create ECR repository: `upload-images-process-ocr-image-repo-{hash}`
   - Create CodePipeline: `upload-images-process-ocr-image-pipeline-{hash}`
   - Create CodeBuild project for Docker builds
   - **Delete** old zip-based Lambda
   - **Create** new container-based Lambda (initially with bootstrap image)
   - Trigger CodePipeline to build and deploy actual Docker image

3. **Wait for CodePipeline to complete** (~5-10 minutes)
   ```bash
   # Monitor pipeline status
   aws codepipeline list-pipeline-executions \
     --pipeline-name upload-images-process-ocr-image-pipeline-* \
     --max-items 1
   ```

4. **Verify deployment**
   ```bash
   # Check Lambda is using Image package type
   aws lambda get-function \
     --function-name upload-images-dev-process-ocr-results \
     --query '{PackageType: Configuration.PackageType, ImageUri: Code.ImageUri}'
   ```
   
   Should show:
   ```json
   {
     "PackageType": "Image",
     "ImageUri": "123456789012.dkr.ecr.us-east-1.amazonaws.com/upload-images-process-ocr-image-repo-abc123:latest"
   }
   ```

5. **Test end-to-end**
   - Upload an image via Next.js site or Mac OCR script
   - Check CloudWatch logs: `/aws/lambda/upload-images-dev-process-ocr-results`
   - Should see:
     ```
     Processing OCR for image <uuid>, job <uuid>
     Got OCR job type: FIRST_PASS
     Image <uuid> classified as NATIVE (dimensions: 1170x2532)
     Processing native receipt <uuid>
     Loaded 45 lines, 234 words for image_id=<uuid> receipt_id=1
     Merchant resolved: Costco Wholesale (source: places_api, score: 0.95)
     Creating embeddings with merchant_name=Costco Wholesale
     Uploading line delta to s3://chromadb-dev-bucket-abc123/lines/delta/<run_id>/
     Uploading word delta to s3://chromadb-dev-bucket-abc123/words/delta/<run_id>/
     COMPACTION_RUN created: run_id=<run_id>
     ```
   
   - Check DynamoDB for `COMPACTION_RUN` record:
     ```bash
     aws dynamodb query \
       --table-name receipt-table-dev \
       --key-condition-expression "PK = :pk" \
       --expression-attribute-values '{":pk": {"S": "COMPACTION_RUN#<run_id>"}}'
     ```
   
   - Check stream processor logs: `/aws/lambda/chromadb-dev-stream-processor`
   - Should see:
     ```
     Detected embeddings completion for run_id=<run_id>
     Queued 2 messages to compaction queue
     ```
   
   - Check compaction Lambda logs: `/aws/lambda/chromadb-dev-enhanced-compaction`
   - Should see:
     ```
     Processing compaction for collection=lines, run_id=<run_id>
     Downloaded delta from s3://chromadb-dev-bucket-abc123/lines/delta/<run_id>/
     Merged 45 records to ChromaDB on EFS
     Created S3 snapshot
     ```

## 📊 Expected Changes After Deployment

### Infrastructure
| Resource | Before | After |
|----------|--------|-------|
| Lambda Package Type | Zip | **Image** |
| Lambda Runtime | python3.12 | N/A (container) |
| Lambda Timeout | 60s | **600s** (10 min) |
| Lambda Memory | 512 MB | **2048 MB** (2 GB) |
| ECR Repository | None | **Created** |
| CodePipeline | None | **Created** |

### Environment Variables (New)
```bash
CHROMADB_BUCKET          # For delta uploads
CHROMA_HTTP_ENDPOINT     # For merchant resolution
GOOGLE_PLACES_API_KEY    # For merchant validation
OPENAI_API_KEY           # For embeddings
```

### Flow Changes
```
OLD FLOW (2 Lambdas, 1 SQS hop):
  process_ocr_results (Zip)
    → Export NDJSON to S3
    → Queue to embed_ndjson_queue
    → embed_from_ndjson Lambda
      → Merchant validation
      → Create embeddings
      → Upload deltas to S3
      → Create COMPACTION_RUN

NEW FLOW (1 Lambda, atomic):
  process_ocr_results (Container)
    → OCR processing
    → Merchant validation
    → Create embeddings
    → Upload deltas to S3
    → Create COMPACTION_RUN
```

## 🎯 Benefits

1. **Faster**: Single Lambda instead of 2 (no SQS hop)
2. **Simpler**: One container instead of zip + separate embedding Lambda
3. **Atomic**: OCR → Merchant → Embeddings → COMPACTION_RUN in one transaction
4. **Cheaper**: Fewer Lambda invocations, less SQS traffic
5. **More reliable**: No intermediate NDJSON files, direct DynamoDB reads
6. **EFS-Ready**: Deltas go to S3, compaction Lambda merges to EFS

## 🔄 Rollback Plan

If deployment fails or Lambda has issues:

```bash
# Revert commits
git revert HEAD~4..HEAD

# Redeploy old infrastructure
cd infra
pulumi up --yes
```

This will restore the old zip-based Lambda with NDJSON export flow.

## 📝 Next Steps After Successful Deployment

1. **Monitor for 24 hours**
   - Lambda duration
   - Error rates
   - ChromaDB delta sizes
   - Compaction success rate

2. **Deprecate old flow** (after confirming stability)
   - Remove `embed_ndjson_queue` (no longer needed)
   - Remove `embed_from_ndjson` Lambda (functionality merged)
   - Remove NDJSON export logic from old `process_ocr_results.py`

3. **Update documentation**
   - Update `infra/upload_images/README.md`
   - Update flow diagrams
   - Document new Lambda architecture

## 🐛 Troubleshooting

### If Lambda times out:
- Check merchant resolution (ChromaDB query might be slow)
- Check Google Places API (might be rate limited)
- Check OpenAI API (embeddings might be slow)
- Increase timeout beyond 600s if needed

### If embeddings fail:
- Check `OPENAI_API_KEY` is set correctly
- Check OpenAI API quota
- Check embedding function in `embedding_processor.py`

### If merchant resolution fails:
- Check `CHROMA_HTTP_ENDPOINT` is accessible from Lambda
- Check `GOOGLE_PLACES_API_KEY` is valid
- Check ChromaDB ECS service is running

### If COMPACTION_RUN not created:
- Check DynamoDB permissions
- Check `CompactionRun` entity structure
- Check `dynamo.add_compaction_run()` call

### If compaction not triggered:
- Check DynamoDB stream is enabled
- Check stream processor is processing events
- Check `is_embeddings_completed()` logic in `compaction_run.py`

