# LayoutLM Inference API Implementation Plan

## Overview

Create an API endpoint similar to `/address_similarity` that displays LayoutLM model predictions on receipts. The API will show model predictions alongside ground truth labels for visualization and evaluation.

## Can LayoutLM Run in Container-Based Lambda?

**Yes!** Here's why:

### ✅ Feasibility
- **Model Size**: ~451MB (fits comfortably in container Lambda)
- **Container Limit**: Up to 10GB (plenty of room)
- **Memory**: Can allocate 2-4GB for model + inference
- **CPU Inference**: Works fine (slower than GPU, but acceptable for API)

### ⚠️ Considerations
- **Cold Start**: Model download + loading takes ~10-30 seconds on first invocation
- **Inference Speed**: CPU inference ~100-500ms per receipt (vs ~10-50ms on GPU)
- **Memory**: Need 2-4GB RAM for model + PyTorch + inference
- **Ephemeral Storage**: Model needs ~500MB-1GB disk space

### 💡 Optimization Strategies
1. **Model Caching**: Keep model in `/tmp` (persists across warm invocations)
2. **Result Caching**: Cache predictions in S3 (like address similarity)
3. **Provisioned Concurrency**: Keep Lambda warm to avoid cold starts
4. **Model Optimization**: Could quantize model to reduce size/speed

## Architecture Options

### Option A: Cached Results (Recommended - Similar to Address Similarity)

**Pattern**: Background Lambda generates predictions, API serves cached results

**Pros**:
- Fast API response (just S3 read)
- No cold start issues for API
- Can pre-compute predictions for common receipts

**Cons**:
- Predictions may be stale
- Need to decide which receipts to cache

**Components**:
1. **Cache Generator Lambda** (container-based)
   - Runs on schedule (e.g., every 5-10 minutes)
   - Selects random receipt(s)
   - Runs LayoutLM inference
   - Compares predictions vs ground truth
   - Uploads JSON to S3

2. **API Lambda** (simple Python)
   - Downloads cached JSON from S3
   - Returns structured response

### Option B: On-Demand Inference

**Pattern**: API Lambda runs inference when requested

**Pros**:
- Always fresh predictions
- Can query any receipt
- No pre-computation needed

**Cons**:
- Slow cold starts (10-30s)
- Higher latency (500ms-2s per request)
- More expensive (compute on every request)

**Components**:
1. **API Lambda** (container-based)
   - Loads model on cold start
   - Accepts `image_id` and `receipt_id` as query params
   - Runs inference
   - Returns predictions + ground truth

### Option C: Hybrid Approach (Best of Both)

**Pattern**: Cache common receipts, on-demand for others

**Components**:
1. **Cache Generator**: Pre-computes predictions for random receipts
2. **API Lambda**: 
   - First checks cache
   - Falls back to on-demand inference if not cached
   - Optionally updates cache

## Recommended Implementation: Option A (Cached)

Following the address similarity pattern for consistency and performance.

## API Response Structure

```typescript
interface LayoutLMInferenceResponse {
  original: {
    receipt: Receipt;
    lines: Line[];
    words: Word[];
    ground_truth_labels: ReceiptWordLabel[];  // VALID labels only
    predictions: Array<{
      word_id: number;
      line_id: number;
      text: string;
      predicted_label: string;      // e.g., "MERCHANT_NAME", "O"
      predicted_confidence: number; // 0.0-1.0
      ground_truth_label?: string;  // If word has VALID label
      is_correct?: boolean;          // true if prediction matches ground truth
    }>;
    line_predictions: Array<{
      line_id: number;
      tokens: string[];
      predicted_labels: string[];
      confidences: number[];
      ground_truth_labels?: string[];
    }>;
  };
  metrics: {
    overall_accuracy: number;        // % of words predicted correctly
    per_label_f1: Record<string, number>;
    per_label_precision: Record<string, number>;
    per_label_recall: Record<string, number>;
  };
  model_info: {
    model_name: string;
    job_id: string;
    s3_uri: string;
    device: "cpu" | "cuda";
  };
  cached_at: string;
}
```

## Implementation Details

### 1. Cache Generator Lambda (Container-Based)

**Location**: `infra/routes/layoutlm_inference_cache_generator/`

**Dockerfile**:
```dockerfile
FROM public.ecr.aws/lambda/python:3.12

# Install system dependencies
RUN yum install -y awscli && yum clean all

# Copy requirements
COPY receipt_layoutlm/pyproject.toml receipt_layoutlm/
COPY receipt_dynamo/pyproject.toml receipt_dynamo/

# Install Python packages
RUN pip install --no-cache-dir \
    torch==2.5.1 \
    transformers>=4.40.0 \
    boto3>=1.34.0 \
    -e receipt_dynamo \
    -e receipt_layoutlm

# Copy handler
COPY infra/routes/layoutlm_inference_cache_generator/lambdas/ ${LAMBDA_TASK_ROOT}/

CMD [ "index.handler" ]
```

**Handler** (`lambdas/index.py`):
```python
import json
import os
import boto3
from receipt_dynamo import DynamoClient
from receipt_layoutlm import LayoutLMInference

S3_BUCKET = os.environ["S3_CACHE_BUCKET"]
DYNAMO_TABLE = os.environ["DYNAMO_TABLE_NAME"]
MODEL_S3_URI = os.environ.get("MODEL_S3_URI")  # Optional, auto-discovers if not set
CACHE_KEY = "layoutlm-inference-cache/latest.json"

def handler(event, context):
    # Initialize clients
    dynamo = DynamoClient(table_name=DYNAMO_TABLE)
    s3 = boto3.client("s3")
    
    # Load model (cached in /tmp after first load)
    model_dir = "/tmp/layoutlm-model"
    infer = LayoutLMInference(
        model_dir=model_dir,
        model_s3_uri=MODEL_S3_URI,
        auto_from_bucket_env="LAYOUTLM_TRAINING_BUCKET"
    )
    
    # Select random receipt with VALID labels
    # (similar to address similarity - query for receipts with labels)
    
    # Run inference
    result = infer.predict_receipt_from_dynamo(dynamo, image_id, receipt_id)
    
    # Get ground truth labels
    # Compare predictions vs ground truth
    # Calculate metrics
    
    # Build response
    response = {
        "original": {...},
        "metrics": {...},
        "model_info": {...},
        "cached_at": datetime.now().isoformat()
    }
    
    # Upload to S3
    s3.put_object(
        Bucket=S3_BUCKET,
        Key=CACHE_KEY,
        Body=json.dumps(response),
        ContentType="application/json"
    )
    
    return {"statusCode": 200, "body": "Cache updated"}
```

**Lambda Configuration**:
- **Memory**: 4096 MB (4GB) - enough for model + inference
- **Timeout**: 300 seconds (5 min) - model download + inference
- **Ephemeral Storage**: 2048 MB (2GB) - for model files
- **Architecture**: `arm64` (Graviton2 - cheaper, sufficient for CPU inference)
- **Reserved Concurrency**: 1 (only one cache update at a time)

### 2. API Lambda (Simple Python)

**Location**: `infra/routes/layoutlm_inference/`

**Handler** (`handler/index.py`):
```python
import json
import boto3
from botocore.exceptions import ClientError

S3_CACHE_BUCKET = os.environ["S3_CACHE_BUCKET"]
CACHE_KEY = "layoutlm-inference-cache/latest.json"

def handler(event, context):
    s3 = boto3.client("s3")
    
    try:
        # Download cached JSON from S3
        response = s3.get_object(Bucket=S3_CACHE_BUCKET, Key=CACHE_KEY)
        data = json.loads(response["Body"].read())
        
        return {
            "statusCode": 200,
            "body": json.dumps(data),
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
        }
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchKey":
            return {
                "statusCode": 404,
                "body": json.dumps({"error": "Cache not found"}),
            }
        raise
```

**Lambda Configuration**:
- **Memory**: 256 MB (just reading from S3)
- **Timeout**: 30 seconds
- **Runtime**: Python 3.12
- **Architecture**: `arm64`

### 3. API Gateway Route

Add to `infra/api_gateway.py`:
```python
# /layoutlm_inference
integration_layoutlm_inference = aws.apigatewayv2.Integration(
    "layoutlm_inference_lambda_integration",
    api_id=api.id,
    integration_type="AWS_PROXY",
    integration_uri=layoutlm_inference_lambda.invoke_arn,
    integration_method="POST",
    payload_format_version="2.0",
)
route_layoutlm_inference = aws.apigatewayv2.Route(
    "layoutlm_inference_route",
    api_id=api.id,
    route_key="GET /layoutlm_inference",
    target=integration_layoutlm_inference.id.apply(
        lambda id: f"integrations/{id}"
    ),
)
```

## Cost Considerations

### Container Lambda (Cache Generator)
- **Memory**: 4GB × $0.0000166667/GB-second = $0.0000667/second
- **Invocations**: 1 per 5-10 minutes = ~288-144 per day
- **Duration**: ~30-60 seconds (model load + inference)
- **Daily Cost**: ~$0.50-1.00/day

### API Lambda (Simple)
- **Memory**: 256MB × $0.0000166667/GB-second = $0.0000042/second
- **Invocations**: Variable (user requests)
- **Duration**: ~100-500ms (S3 read)
- **Cost**: Negligible

## Performance Expectations

### Cache Generator (First Run)
- Model download: 10-20 seconds
- Model loading: 5-10 seconds
- Inference: 1-3 seconds
- **Total**: ~20-35 seconds

### Cache Generator (Warm)
- Model already loaded: 0 seconds
- Inference: 1-3 seconds
- **Total**: ~1-3 seconds

### API Response
- S3 read: 100-500ms
- JSON parsing: <10ms
- **Total**: ~100-500ms

## Alternative: On-Demand API

If you want on-demand inference instead of caching:

**Single Container Lambda**:
- Accepts `image_id` and `receipt_id` query params
- Loads model on cold start (cached in `/tmp`)
- Runs inference
- Returns results

**Trade-offs**:
- ✅ Always fresh
- ✅ Can query any receipt
- ❌ Slow cold starts (10-30s)
- ❌ Higher latency (500ms-2s)
- ❌ More expensive

## Next Steps

1. **Wait for training to complete** - Get the best model checkpoint
2. **Choose approach** - Cached (Option A) vs On-demand (Option B)
3. **Implement cache generator** - Container Lambda with model
4. **Implement API Lambda** - Simple S3 reader
5. **Add API Gateway route** - `/layoutlm_inference`
6. **Create frontend component** - Similar to AddressSimilarity

## Files to Create

### Cache Generator (Container)
- `infra/routes/layoutlm_inference_cache_generator/lambdas/index.py`
- `infra/routes/layoutlm_inference_cache_generator/lambdas/Dockerfile`
- `infra/routes/layoutlm_inference_cache_generator/infra.py`

### API Lambda
- `infra/routes/layoutlm_inference/handler/index.py`
- `infra/routes/layoutlm_inference/infra.py`

### Updates
- `infra/api_gateway.py` - Add route
- `portfolio/types/api.ts` - Add TypeScript types
- `portfolio/services/api/index.ts` - Add API client method
- `portfolio/components/ui/Figures/` - Create LayoutLMInference component

