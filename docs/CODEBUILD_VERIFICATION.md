# CodeBuild Pipeline Verification

## ✅ Confirmation: Pipeline Will Build Validation Code

### What We Verified

1. **Dockerfile Includes receipt_label**:
   ```dockerfile
   COPY receipt_label/ /tmp/receipt_label/
   RUN pip install --no-cache-dir "/tmp/receipt_label[full]"
   ```
   ✅ The Dockerfile copies the entire `receipt_label/` package, which includes our validation code.

2. **Content Hash Includes receipt_label**:
   From `codebuild_docker_image.py` lines 192-193:
   ```python
   packages_to_hash = [
       "receipt_dynamo/receipt_dynamo",
       "receipt_dynamo/pyproject.toml",
       "receipt_label/receipt_label",  # ← Includes our validation code
       "receipt_label/pyproject.toml",
   ]
   ```
   ✅ The hash calculation includes `receipt_label/receipt_label/`, which contains `chromadb_client.py` with validation.

3. **Build Context Includes receipt_label**:
   From upload script (lines 313-316):
   ```bash
   --include='receipt_label/' \
   --include='receipt_label/pyproject.toml' \
   --include='receipt_label/receipt_label/' \
   --include='receipt_label/receipt_label/**' \
   ```
   ✅ The build context includes all files in `receipt_label/receipt_label/`, including our validation code.

4. **Code is Committed**:
   ```bash
   git log -1 -- receipt_label/receipt_label/vector_store/client/chromadb_client.py
   # Shows: 39caec13 Add delta validation and retry logic...
   ```
   ✅ The validation code is committed, so CodeBuild will see it.

## How It Works

### Step 1: Pulumi Calculates Hash
- Reads `receipt_label/receipt_label/vector_store/client/chromadb_client.py` from filesystem
- Calculates hash including our validation code
- Hash changed → triggers rebuild

### Step 2: CodeBuild Pipeline Triggered
- Uploads build context to S3 (includes committed `receipt_label/` code)
- CodeBuild downloads context
- Builds Docker image with `receipt_label` package

### Step 3: Docker Build
- Copies `receipt_label/` to container
- Installs package: `pip install "/tmp/receipt_label[full]"`
- Validation code is now in the image

### Step 4: Lambda Update
- Pushes image to ECR
- Updates Lambda function with new image URI
- Lambda now has validation code

## Verification Checklist

- [x] Validation code is committed to git
- [x] Dockerfile includes receipt_label package
- [x] Content hash includes receipt_label/receipt_label
- [x] Build context includes receipt_label/receipt_label/**
- [x] CodeBuild will rebuild when hash changes
- [x] Lambda will be updated with new image

## Expected Behavior After Deployment

### New Delta Creation
1. Create delta locally
2. Upload to S3
3. **Download and validate** ← NEW (from committed code)
4. If validation fails → retry (up to 3 times)
5. If all retries fail → raise error

### Logs to Look For
After deployment, check logs for:
- `"Validating delta by downloading from S3"`
- `"Delta validation successful"`
- `"Delta validation failed"` (with retry attempts)
- `"Retry attempt X/3 for delta upload"`

## Conclusion

**✅ YES, the CodeBuild pipeline will build and deploy the validation code.**

The pipeline:
1. ✅ Includes `receipt_label/` in the Docker image
2. ✅ Calculates hash from committed files (including our validation code)
3. ✅ Will rebuild when hash changes (which it did when we committed)
4. ✅ Will update Lambda functions with the new image

The validation code is in the committed `receipt_label/receipt_label/vector_store/client/chromadb_client.py` file, which is:
- Included in the Docker build context
- Installed as part of the `receipt_label[full]` package
- Available to Lambda functions at runtime

