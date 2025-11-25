# Dependency Update Required: Add receipt_chroma to Lambda Builds

## Summary

After migrating the code to use `receipt_chroma`, we need to update the Lambda build configurations (Dockerfiles and layers) to include the `receipt_chroma` package.

## Issues Found

### ❌ Container-Based Lambdas (Dockerfiles)

These Dockerfiles install `receipt_label` but **NOT** `receipt_chroma`:

1. **`infra/validate_pending_labels/lambdas/Dockerfile`**
   - Currently installs: `receipt_dynamo`, `receipt_label[full]`
   - Needs: `receipt_chroma` added

2. **`infra/routes/address_similarity_cache_generator/lambdas/Dockerfile`**
   - Currently installs: `receipt_dynamo`, `receipt_label[full]`
   - Needs: `receipt_chroma` added

3. **`infra/validate_merchant_step_functions/container/Dockerfile`**
   - Currently installs: `receipt_dynamo`, `receipt_label[full]`
   - Needs: `receipt_chroma` added

4. **`infra/upload_images/container/Dockerfile`**
   - Currently installs: `receipt_dynamo`, `receipt_label[full]`
   - Needs: `receipt_chroma` added

5. **`infra/upload_images/container_ocr/Dockerfile`**
   - Currently installs: `receipt_dynamo`, `receipt_upload`, `receipt_label[full]`
   - Needs: `receipt_chroma` added

### ❌ Zip-Based Lambda (Layer)

**`process_receipt_realtime`** uses `label_layer` which only includes `receipt_label[lambda]`:
- Currently uses: `label_layer` (includes `receipt_label[lambda]` + `receipt_dynamo`)
- Needs: A `receipt_chroma` layer added to the Lambda

## Required Changes

### 1. Update Dockerfiles

All Dockerfiles should follow the pattern from `infra/embedding_step_functions/unified_embedding/Dockerfile`:

```dockerfile
# Copy only the dependency packages
COPY receipt_dynamo/ /tmp/receipt_dynamo/
COPY receipt_chroma/ /tmp/receipt_chroma/  # ← ADD THIS
COPY receipt_label/ /tmp/receipt_label/

# Install dependencies
RUN pip install --no-cache-dir /tmp/receipt_dynamo && \
    pip install --no-cache-dir /tmp/receipt_chroma && \  # ← ADD THIS
    pip install --no-cache-dir "/tmp/receipt_label[full]" && \
    rm -rf /tmp/receipt_dynamo /tmp/receipt_chroma /tmp/receipt_label  # ← UPDATE THIS
```

### 2. Create receipt_chroma Layer

Add `receipt_chroma` to the layers in `infra/lambda_layer.py`:

```python
layers_to_build = [
    # ... existing layers ...
    {
        "package_dir": "receipt_chroma",
        "name": "receipt-chroma",
        "description": "ChromaDB layer for receipt_chroma",
        "python_versions": ["3.12"],
        "needs_pillow": False,  # Check if needed
    },
]
```

### 3. Update process_receipt_realtime Lambda

In `infra/embedding_step_functions/components/lambda_functions.py`, add `receipt_chroma` layer to `process_receipt_realtime`:

```python
# Add chroma_layer to process_receipt_realtime
if config["source_dir"] == "process_receipt_realtime":
    if chroma_layer:
        layers.append(chroma_layer.arn)
```

## Files to Update

1. `infra/validate_pending_labels/lambdas/Dockerfile`
2. `infra/routes/address_similarity_cache_generator/lambdas/Dockerfile`
3. `infra/validate_merchant_step_functions/container/Dockerfile`
4. `infra/upload_images/container/Dockerfile`
5. `infra/upload_images/container_ocr/Dockerfile`
6. `infra/lambda_layer.py` (add receipt_chroma layer)
7. `infra/embedding_step_functions/components/lambda_functions.py` (add chroma_layer to process_receipt_realtime)

## Reference Implementation

See `infra/embedding_step_functions/unified_embedding/Dockerfile` for the correct pattern (already includes receipt_chroma).

