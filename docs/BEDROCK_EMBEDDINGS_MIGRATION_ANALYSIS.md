# AWS Bedrock Embeddings Migration Analysis

## Executive Summary

This document analyzes the current OpenAI embeddings implementation and evaluates the feasibility of migrating to AWS Bedrock embeddings. The migration is **moderately complex** but **feasible**, with the main challenges being:

1. **Vector dimension change** (1536 → 1024) requiring re-embedding all existing data
2. **API interface differences** between OpenAI and Bedrock
3. **Batch processing** changes (Bedrock uses different batching approach)
4. **Network/VPC configuration** for Bedrock access
5. **Cost and performance** considerations

## Current OpenAI Embeddings Implementation

### Architecture Overview

The system uses OpenAI's `text-embedding-3-small` model (1536 dimensions) in multiple ways:

1. **Batch Processing** (via OpenAI Batch API)
   - Location: `receipt_label/receipt_label/embedding/word/submit.py` and `line/submit.py`
   - Process: Creates NDJSON files, uploads to OpenAI, submits batch jobs
   - Polling: `receipt_label/receipt_label/embedding/word/poll.py` and `line/poll.py`
   - Used for: Processing large batches of words/lines asynchronously

2. **Real-time Processing**
   - Location: `receipt_label/receipt_label/embedding/word/realtime.py` and `line/realtime.py`
   - Process: Direct API calls to OpenAI embeddings endpoint
   - Used for: On-demand embedding generation

3. **Storage**
   - ChromaDB collections: `words` and `lines`
   - Storage: S3 (snapshots) + EFS (active database)
   - Client: `receipt_label/receipt_label/utils/chroma_client.py` and `vector_store/client/chromadb_client.py`

### Key Components

#### 1. Embedding Model Configuration

```python
# Current model used everywhere
model = "text-embedding-3-small"  # 1536 dimensions
```

**Locations:**
- `receipt_label/receipt_label/embedding/word/realtime.py:216`
- `receipt_label/receipt_label/embedding/word/submit.py:270,307`
- `receipt_label/receipt_label/embedding/line/realtime.py:160`
- `receipt_label/receipt_label/embedding/line/submit.py:99,214`
- `receipt_label/receipt_label/utils/chroma_client.py:105` (ChromaDB embedding function)

#### 2. OpenAI Client Integration

**Client Manager:**
```python
# receipt_label/receipt_label/utils/client_manager.py
@property
def openai(self) -> OpenAI:
    client = OpenAI(api_key=self.config.openai_api_key)
    # Wrapped with usage tracking
    return client
```

**Real-time Embeddings:**
```python
# receipt_label/receipt_label/embedding/word/realtime.py:247
response = openai_client.embeddings.create(
    model="text-embedding-3-small",
    input=[formatted_texts]
)
```

**Batch Embeddings:**
```python
# receipt_label/receipt_label/embedding/word/submit.py:303
batch = client_manager.openai.batches.create(
    input_file_id=file_id,
    endpoint="/v1/embeddings",
    completion_window="24h",
    metadata={"model": "text-embedding-3-small"}
)
```

#### 3. ChromaDB Integration

**Embedding Function:**
```python
# receipt_label/receipt_label/utils/chroma_client.py:102-106
self._embedding_function = (
    embedding_functions.OpenAIEmbeddingFunction(
        api_key=api_key,
        model_name="text-embedding-3-small",
    )
)
```

**Collections:**
- `words` collection: Word-level embeddings with spatial context
- `lines` collection: Line-level embeddings with vertical context

#### 4. Infrastructure

**Lambdas Using Embeddings:**
- Embedding step functions (`infra/embedding_step_functions/`)
- ChromaDB compaction (`infra/chromadb_compaction/`)
- Merchant validation (`infra/merchant_validation_container/`)
- Validation step functions (`infra/validate_merchant_step_functions/`)
- Upload images processing (`infra/upload_images/`)

**Environment Variables:**
- `OPENAI_API_KEY` - Required in all Lambda functions
- Stored as Pulumi secrets across environments

## AWS Bedrock Embeddings Overview

### Available Models

1. **Amazon Titan Embeddings G1 - Text v1**
   - Model ID: `amazon.titan-embed-text-v1`
   - Dimensions: **1024**
   - Max input: 8,192 tokens
   - Pricing: $0.10 per 1M tokens

2. **Amazon Titan Embeddings G1 - Text v2**
   - Model ID: `amazon.titan-embed-text-v2:0`
   - Dimensions: **1024**
   - Max input: 8,192 tokens
   - Pricing: $0.10 per 1M tokens

3. **Cohere Embed English v3**
   - Model ID: `cohere.embed-english-v3`
   - Dimensions: 1024 (configurable)
   - Max input: 512 tokens
   - Pricing: $0.10 per 1M tokens

### API Interface

**Real-time API:**
```python
import boto3

bedrock_runtime = boto3.client('bedrock-runtime', region_name='us-east-1')

response = bedrock_runtime.invoke_model(
    modelId='amazon.titan-embed-text-v2:0',
    body=json.dumps({
        "inputText": "Your text here"
    })
)
```

**Batch Processing:**
- Bedrock doesn't have a direct equivalent to OpenAI's Batch API
- Options:
  1. Use AWS Batch or Step Functions for orchestration
  2. Implement custom batching with Lambda
  3. Use async invocation patterns

## Migration Challenges

### 1. Vector Dimension Mismatch ⚠️ **CRITICAL**

**Current:** OpenAI `text-embedding-3-small` = **1536 dimensions**  
**Bedrock:** Amazon Titan = **1024 dimensions**

**Impact:**
- **Cannot mix vectors** of different dimensions in ChromaDB
- **Must re-embed all existing data** in ChromaDB
- **Migration strategy required** for zero-downtime transition

**Solution Approaches:**

**Option A: Parallel Migration (Recommended)**
1. Create new ChromaDB collections with Bedrock embeddings (1024 dim)
2. Run parallel embedding pipeline (OpenAI + Bedrock)
3. Gradually migrate queries to Bedrock collections
4. Deprecate OpenAI collections after validation

**Option B: Full Re-embedding**
1. Export all existing embeddings metadata
2. Re-embed all text using Bedrock
3. Replace collections atomically
4. Higher risk, requires downtime window

### 2. API Interface Differences

**OpenAI:**
```python
response = openai_client.embeddings.create(
    model="text-embedding-3-small",
    input=["text1", "text2"]  # List of strings
)
embeddings = [item.embedding for item in response.data]
```

**Bedrock:**
```python
response = bedrock_runtime.invoke_model(
    modelId='amazon.titan-embed-text-v2:0',
    body=json.dumps({
        "inputText": "text1"  # Single string per call
    })
)
# Need to call multiple times for batch
```

**Impact:**
- Bedrock requires individual calls (or boto3 batch wrapper)
- No native batch API like OpenAI
- Need custom batching logic

### 3. Batch Processing Architecture

**Current OpenAI Batch:**
- Upload NDJSON file to OpenAI
- Submit batch job via API
- Poll for completion
- Download results

**Bedrock Alternative:**
- Use Step Functions for orchestration
- Lambda functions for embedding calls
- SQS for queuing
- Store results in S3/DynamoDB

**Impact:**
- More infrastructure to manage
- Different error handling patterns
- Requires Step Functions redesign

### 4. ChromaDB Embedding Function

**Current:**
```python
embedding_functions.OpenAIEmbeddingFunction(
    api_key=api_key,
    model_name="text-embedding-3-small",
)
```

**Bedrock Alternative:**
- ChromaDB doesn't have native Bedrock embedding function
- Need custom embedding function:
```python
class BedrockEmbeddingFunction:
    def __call__(self, texts: List[str]) -> List[List[float]]:
        # Call Bedrock API for each text
        # Return list of embeddings
```

### 5. Network and VPC Configuration

**Current:**
- OpenAI calls go over internet
- No VPC required (some Lambdas in VPC for EFS access)

**Bedrock Requirements:**
- Bedrock endpoints are VPC-accessible
- Need VPC endpoints for:
  - `bedrock-runtime.us-east-1.amazonaws.com`
  - `bedrock.us-east-1.amazonaws.com` (for model management)
- Or use NAT Gateway for internet access (less secure)

**Impact:**
- Lambdas need VPC configuration
- VPC endpoints cost (~$7/month per endpoint)
- NAT Gateway costs if not using VPC endpoints

### 6. Cost Considerations

**OpenAI Pricing:**
- `text-embedding-3-small`: $0.02 per 1M tokens
- Batch API: Same pricing

**Bedrock Pricing:**
- Amazon Titan: $0.10 per 1M tokens
- **5x more expensive** than OpenAI

**Cost Impact:**
- Significant cost increase unless usage is very high (AWS discounts may apply)
- Consider if Bedrock integration benefits outweigh cost

### 7. IAM Permissions

**Required Permissions:**
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream"
      ],
      "Resource": "arn:aws:bedrock:us-east-1::foundation-model/amazon.titan-embed-text-v2:0"
    }
  ]
}
```

**Impact:**
- Need to update IAM roles for all Lambda functions
- Need to enable Bedrock model access in AWS Console

## Migration Implementation Plan

### Phase 1: Infrastructure Setup

1. **Create Bedrock Embedding Service**
   - New module: `receipt_label/receipt_label/embedding/bedrock/`
   - Bedrock client wrapper
   - Custom ChromaDB embedding function
   - Error handling and retries

2. **Update Infrastructure**
   - Add VPC endpoints for Bedrock
   - Update Lambda IAM roles
   - Add Bedrock model access permissions
   - Environment variables for model selection

3. **Create Parallel Collections**
   - New ChromaDB collections: `words_bedrock`, `lines_bedrock`
   - Configure with 1024-dimension embedding function

### Phase 2: Real-time Embeddings

1. **Create Bedrock Realtime Module**
   - `receipt_label/receipt_label/embedding/word/bedrock_realtime.py`
   - `receipt_label/receipt_label/embedding/line/bedrock_realtime.py`
   - Mirror OpenAI realtime interface
   - Add feature flag for model selection

2. **Update Client Manager**
   - Add `bedrock_runtime` property
   - Add configuration for embedding provider
   - Support both OpenAI and Bedrock

3. **Update ChromaDB Client**
   - Add Bedrock embedding function option
   - Support dimension configuration

### Phase 3: Batch Processing

1. **Redesign Batch Architecture**
   - Step Functions workflow for Bedrock embeddings
   - SQS queues for job management
   - Lambda functions for embedding calls
   - Result storage in S3/DynamoDB

2. **Migration Scripts**
   - Export existing embedding metadata
   - Re-embed with Bedrock
   - Import to new collections

### Phase 4: Testing & Validation

1. **Quality Testing**
   - Compare embedding quality (similarity scores)
   - Test query results accuracy
   - Performance benchmarking

2. **Load Testing**
   - Test batch processing throughput
   - Test concurrent real-time requests
   - Monitor costs

### Phase 5: Gradual Migration

1. **Dual-Write Phase**
   - Write to both OpenAI and Bedrock collections
   - Compare results
   - Monitor for issues

2. **Read Migration**
   - Feature flag for query source
   - Gradually shift queries to Bedrock
   - Monitor performance

3. **Deprecation**
   - Stop writing to OpenAI collections
   - Archive OpenAI collections
   - Remove OpenAI code paths

## Code Changes Required

### 1. New Bedrock Embedding Client

```python
# receipt_label/receipt_label/embedding/bedrock/client.py
import boto3
import json
from typing import List

class BedrockEmbeddingClient:
    def __init__(self, region_name: str = "us-east-1", model_id: str = "amazon.titan-embed-text-v2:0"):
        self.bedrock_runtime = boto3.client('bedrock-runtime', region_name=region_name)
        self.model_id = model_id
    
    def embed_text(self, text: str) -> List[float]:
        """Embed a single text."""
        response = self.bedrock_runtime.invoke_model(
            modelId=self.model_id,
            body=json.dumps({"inputText": text})
        )
        result = json.loads(response['body'].read())
        return result['embedding']
    
    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Embed multiple texts (sequential calls)."""
        return [self.embed_text(text) for text in texts]
```

### 2. Custom ChromaDB Embedding Function

```python
# receipt_label/receipt_label/utils/bedrock_embedding_function.py
from chromadb.utils import embedding_functions
from receipt_label.embedding.bedrock.client import BedrockEmbeddingClient

class BedrockEmbeddingFunction(embedding_functions.EmbeddingFunction):
    def __init__(self, model_id: str = "amazon.titan-embed-text-v2:0"):
        self.client = BedrockEmbeddingClient(model_id=model_id)
    
    def __call__(self, texts: List[str]) -> List[List[float]]:
        return self.client.embed_batch(texts)
```

### 3. Update Client Manager

```python
# receipt_label/receipt_label/utils/client_manager.py
@property
def bedrock_runtime(self):
    """Get or create Bedrock runtime client."""
    if self._bedrock_runtime is None:
        import boto3
        self._bedrock_runtime = boto3.client(
            'bedrock-runtime',
            region_name=os.environ.get('AWS_REGION', 'us-east-1')
        )
    return self._bedrock_runtime
```

### 4. Feature Flag for Embedding Provider

```python
# receipt_label/receipt_label/config/embedding.py
EMBEDDING_PROVIDER = os.environ.get("EMBEDDING_PROVIDER", "openai")  # "openai" | "bedrock"
BEDROCK_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "amazon.titan-embed-text-v2:0")
```

## Network Configuration

### VPC Endpoints Required

```python
# infra/bedrock_vpc_endpoints.py
from aws import ec2

# Create VPC endpoints for Bedrock
bedrock_endpoint = ec2.VpcEndpoint(
    "bedrock-endpoint",
    vpc_id=vpc_id,
    service_name=f"com.amazonaws.us-east-1.bedrock-runtime",
    vpc_endpoint_type="Interface",
    subnet_ids=subnet_ids,
    security_group_ids=[security_group_id],
)
```

### Lambda VPC Configuration

All Lambdas using Bedrock need:
- VPC configuration (if not already)
- Security group allowing outbound HTTPS
- VPC endpoint or NAT Gateway access

## Estimated Effort

| Task | Effort | Complexity |
|------|--------|------------|
| Bedrock client implementation | 2-3 days | Low |
| ChromaDB embedding function | 1 day | Low |
| Real-time embedding migration | 2-3 days | Medium |
| Batch processing redesign | 5-7 days | High |
| Infrastructure updates | 2-3 days | Medium |
| Testing & validation | 3-5 days | Medium |
| Migration scripts | 2-3 days | Medium |
| Gradual migration execution | 1-2 weeks | High |

**Total Estimated Effort: 3-4 weeks**

## Recommendations

### ✅ Easy Wins (Low Risk)

1. **Start with real-time embeddings** - Simpler API, easier to test
2. **Use feature flags** - Allow gradual rollout
3. **Parallel collections** - No risk to existing data
4. **Monitor costs closely** - Bedrock is 5x more expensive

### ⚠️ Considerations

1. **Cost Impact** - Bedrock is significantly more expensive. Consider:
   - AWS committed use discounts
   - Reserved capacity
   - Whether benefits justify cost

2. **Batch Processing** - Significant redesign needed. Consider:
   - Keeping OpenAI for batch processing
   - Using Bedrock only for real-time
   - Hybrid approach

3. **Vector Migration** - All existing embeddings need re-embedding. Consider:
   - Parallel run period for validation
   - Gradual migration strategy
   - Data export/import scripts

### 🎯 Alternative Approaches

1. **Hybrid Model**
   - Use Bedrock for new embeddings
   - Keep OpenAI for batch processing
   - Migrate gradually

2. **Cost Optimization**
   - Use Bedrock only for specific use cases
   - Keep OpenAI for high-volume batch jobs
   - Monitor and optimize

3. **Wait for Bedrock Batch API**
   - Bedrock may add batch API in future
   - Reduces migration complexity
   - Monitor AWS announcements

## Conclusion

The migration to AWS Bedrock embeddings is **feasible but non-trivial**. The main challenges are:

1. ✅ **API differences** - Manageable with wrapper code
2. ⚠️ **Vector dimension change** - Requires re-embedding all data
3. ⚠️ **Batch processing** - Significant architecture changes needed
4. ✅ **Network setup** - Straightforward VPC endpoint configuration
5. ⚠️ **Cost** - 5x increase, needs justification

**Recommendation:** Start with a **proof-of-concept** for real-time embeddings, validate quality and costs, then decide on full migration or hybrid approach.
