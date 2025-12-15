# Address Similarity API Implementation Plan

## Overview

Create a new API endpoint that displays ChromaDB similarity search results for address labels. The endpoint will serve pre-computed data from S3 cache, updated once per day by a background Lambda.

## Architecture

1. **Background Lambda** (cache generator): Runs once per day via EventBridge
   - Gets random word with address label
   - Queries ChromaDB lines collection for similar lines
   - Fetches receipt context (±n lines to include all address words)
   - Uploads JSON to S3

2. **API Lambda** (cache reader): Handles GET requests
   - Downloads cached JSON from S3
   - Returns structured response

## API Response Structure

```json
{
  "original": {
    "receipt": { /* Receipt object */ },
    "lines": [ /* ReceiptLine objects */ ],
    "words": [ /* ReceiptWord objects */ ],
    "labels": [ /* ReceiptWordLabel objects with address label */ ]
  },
  "similar": [
    {
      "receipt": { /* Receipt object */ },
      "lines": [ /* ReceiptLine objects */ ],
      "words": [ /* ReceiptWord objects */ ],
      "labels": [ /* ReceiptWordLabel objects with address label */ ],
      "similarity_distance": 0.123
    }
  ],
  "cached_at": "2025-01-15T10:30:00Z"
}
```

## Implementation Details

### Assumptions
- Find 8 similar lines from ChromaDB (reasonable for UI display)
- Return empty similar array if none found
- Include all lines from min to max line_id that contain address words
- Cache structure: `address-similarity-cache/latest.json` (single file, overwritten)
- Return 404 if cache missing (scheduled updates should prevent this)

### Files to Create

1. **Background Lambda**:
   - `infra/routes/address_similarity_cache_generator/handler/index.py`
   - `infra/routes/address_similarity_cache_generator/infra.py`

2. **API Lambda**:
   - `infra/routes/address_similarity/handler/index.py`
   - `infra/routes/address_similarity/infra.py`

3. **Update API Gateway**:
   - Add route to `infra/api_gateway.py`

### Background Lambda Logic

1. **Get random address word**:
   ```python
   # Query GSI1 for LABEL#ADDRESS labels
   labels = client.get_receipt_word_labels_by_label("address", limit=100)
   random_label = random.choice(labels)
   ```

2. **Get original context**:
   - Get receipt details: `client.get_receipt_details(random_label.image_id, random_label.receipt_id)`
   - Find all address labels in receipt
   - Calculate min/max line_id for address words
   - Get all lines from receipt (already have full receipt)

3. **Query ChromaDB for similar lines**:
   ```python
   # Get line embedding from ChromaDB words collection or use line text
   # Query lines collection
   similar_results = chroma_client.query(
       collection_name="lines",
       query_embeddings=[line_embedding],
       n_results=8
   )
   ```

4. **For each similar line**:
   - Extract `image_id`, `receipt_id`, `line_id` from metadata
   - Get receipt details
   - Find address labels in that receipt
   - Calculate line range
   - Get full receipt context

5. **Upload to S3**:
   ```python
   s3_client.put_object(
       Bucket=bucket,
       Key="address-similarity-cache/latest.json",
       Body=json.dumps(response_data)
   )
   ```

### API Lambda Logic

1. **Download from S3**:
   ```python
   response = s3_client.get_object(
       Bucket=bucket,
       Key="address-similarity-cache/latest.json"
   )
   data = json.loads(response["Body"].read())
   ```

2. **Return JSON response**

### Infrastructure Components

1. **EventBridge Schedule**: Once per day
2. **S3 Bucket**: Use existing bucket or create new
3. **IAM Permissions**:
   - Background Lambda: DynamoDB read, ChromaDB read (EFS/S3), S3 write
   - API Lambda: S3 read
4. **VPC Configuration**: Background Lambda needs VPC for EFS access

### Dependencies

- `receipt-dynamo` (DynamoDB client)
- `receipt-label[full]` (ChromaDB client, embedding functions)
- `boto3` (S3 operations)

## Testing Strategy

1. **Local testing**: Test cache generation logic with dev data
2. **Dev deployment**: Deploy and verify EventBridge triggers
3. **Verify cache**: Check S3 for cached JSON
4. **Test API**: Call endpoint and verify response structure
5. **UI integration**: Update Next.js frontend to consume endpoint

## Rollout Plan

1. ✅ Create infrastructure files
2. ⚠️ **TODO**: Update `__main__.py` to configure Lambda functions with proper parameters
3. Deploy to dev environment
4. Monitor cache generation (CloudWatch logs)
5. Test API endpoint
6. Update frontend to display results
7. Deploy to prod

## Implementation Status

### ✅ Completed

1. **Background Lambda Handler** (`infra/routes/address_similarity_cache_generator/handler/index.py`)
   - Gets random address label
   - Queries ChromaDB for similar lines
   - Fetches receipt context
   - Uploads JSON to S3

2. **Background Lambda Infrastructure** (`infra/routes/address_similarity_cache_generator/infra.py`)
   - IAM roles and policies
   - EventBridge schedule (once per day)
   - Function factory for proper configuration

3. **API Lambda Handler** (`infra/routes/address_similarity/handler/index.py`)
   - Downloads cached JSON from S3
   - Returns JSON response with CORS headers

4. **API Lambda Infrastructure** (`infra/routes/address_similarity/infra.py`)
   - IAM roles and policies
   - Function factory for proper configuration

5. **API Gateway Integration** (`infra/api_gateway.py`)
   - Added route: `GET /address_similarity`
   - Integration and permissions configured

### ⚠️ Remaining Integration

The Lambda functions are created with placeholder values. To complete the integration, update `__main__.py` to:

1. Import the factory functions:
   ```python
   from routes.address_similarity_cache_generator.infra import (
       create_address_similarity_cache_generator,
   )
   from routes.address_similarity.infra import (
       create_address_similarity_lambda,
   )
   ```

2. Create the cache generator Lambda with proper configuration:
   ```python
   # Cache generator is container-based, reads from S3 (no VPC needed)
   address_similarity_cache_generator = create_address_similarity_cache_generator(
       chromadb_bucket_name=shared_chromadb_buckets.bucket_name,
   )
   ```

3. Create the API Lambda with proper configuration:
   ```python
   address_similarity_lambda = create_address_similarity_lambda(
       chromadb_bucket_name=shared_chromadb_buckets.bucket_name,
   )
   ```

4. Update the API Gateway import to use the properly configured Lambda:
   ```python
   # In api_gateway.py, the import should use the configured instance
   # You may need to export it from __main__.py or restructure slightly
   ```

### Important Notes

- **Container-based Lambda**: The cache generator uses a container-based Lambda (built with CodeBuild) to support ChromaDB dependencies
- **S3-only access**: Reads ChromaDB snapshots from S3, eliminating need for VPC/EFS (reduces costs)
- **No VPC**: Since we're reading from S3, no VPC configuration is needed, further reducing costs
- **Ephemeral storage**: Lambda configured with 10GB ephemeral storage for snapshot download

