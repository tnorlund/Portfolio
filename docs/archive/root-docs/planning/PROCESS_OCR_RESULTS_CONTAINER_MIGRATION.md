# Process OCR Results Container Migration Plan

## Date: October 24, 2025

## Executive Summary

Migrate `process_ocr_results` from a zip-based Lambda to a container-based Lambda that includes merchant validation logic. This simplifies the architecture by reducing from 3 Lambdas to 2, while making the code more maintainable and testable.

---

## Current Architecture (3 Lambdas)

```
┌─────────────────────────────────────────────────────────────┐
│ 1. process_ocr_results (Zip-based, 1024MB, 300s)           │
│    - Parse OCR JSON                                         │
│    - Store LINE/WORD/LETTER in DynamoDB                     │
│    - Export NDJSON to S3                                    │
│    - Queue to embed-ndjson-queue                            │
└──────────────────────┬──────────────────────────────────────┘
                       │ SQS
                       ↓
┌─────────────────────────────────────────────────────────────┐
│ 2. embed_from_ndjson (Container, 2048MB, 900s)             │
│    - Download NDJSON from S3                                │
│    - Validate merchant (ChromaDB + Google Places)           │
│    - Create embeddings with merchant context                │
│    - Write ChromaDB deltas to S3                            │
│    - Update COMPACTION_RUN                                  │
└──────────────────────┬──────────────────────────────────────┘
                       │ DynamoDB Stream
                       ↓
┌─────────────────────────────────────────────────────────────┐
│ 3. enhanced_compaction (Container + EFS, 3008MB, 900s)     │
│    - Merge ChromaDB deltas to EFS                           │
│    - Create S3 snapshots                                    │
└─────────────────────────────────────────────────────────────┘
```

---

## Proposed Architecture (2 Lambdas)

```
┌─────────────────────────────────────────────────────────────┐
│ 1. process_ocr_results (Container, 2048MB, 600s)           │
│    - Parse OCR JSON                                         │
│    - Store LINE/WORD/LETTER in DynamoDB                     │
│    - Validate merchant (ChromaDB HTTP + Google Places)      │
│    - Create ReceiptMetadata                                 │
│    - Create embeddings with merchant context                │
│    - Write ChromaDB deltas to S3                            │
│    - Update COMPACTION_RUN                                  │
└──────────────────────┬──────────────────────────────────────┘
                       │ DynamoDB Stream
                       ↓
┌─────────────────────────────────────────────────────────────┐
│ 2. enhanced_compaction (Container + EFS, 3008MB, 900s)     │
│    - Merge ChromaDB deltas to EFS                           │
│    - Create S3 snapshots                                    │
└─────────────────────────────────────────────────────────────┘
```

**Benefits:**
- ✅ One less Lambda to maintain
- ✅ One less SQS queue
- ✅ Faster (no queue delay)
- ✅ Atomic (merchant + embedding together)
- ✅ Easier to test (all logic in one place)
- ✅ Container-based = easier dependency management

---

## Implementation Plan

### Phase 1: Create Modular Handler Structure

Create a new modular structure similar to the compaction Lambda:

```
infra/upload_images/
├── container/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── handler/
│       ├── __init__.py
│       ├── handler.py           ← Main Lambda handler
│       ├── ocr_processor.py     ← OCR parsing logic
│       ├── merchant_validator.py ← Merchant validation (from embed_from_ndjson)
│       ├── embedding_creator.py  ← Embedding logic (from embed_from_ndjson)
│       └── utils.py             ← Shared utilities
```

### Phase 2: Extract Reusable Code

#### From `process_ocr_results.py`:
- OCR parsing logic
- DynamoDB writes
- Image classification
- Receipt processing (native/scan/photo)

#### From `embed_from_ndjson/handler.py`:
- Merchant validation logic
- ChromaDB client setup
- Google Places integration
- Embedding creation
- Delta management

---

## Detailed Implementation

### 1. Create Dockerfile

**File:** `infra/upload_images/container/Dockerfile`

```dockerfile
FROM public.ecr.aws/lambda/python:3.12

# Install system dependencies
RUN dnf install -y \
    gcc \
    gcc-c++ \
    python3-devel \
    libpq-devel \
    && dnf clean all \
    && rm -rf /var/cache/dnf

# Copy requirements
COPY requirements.txt ${LAMBDA_TASK_ROOT}/

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy local packages (from workspace root)
COPY ../../../receipt_dynamo ${LAMBDA_TASK_ROOT}/receipt_dynamo/
COPY ../../../receipt_label ${LAMBDA_TASK_ROOT}/receipt_label/
COPY ../../../receipt_upload ${LAMBDA_TASK_ROOT}/receipt_upload/

# Copy handler code
COPY handler/ ${LAMBDA_TASK_ROOT}/handler/

# Set the CMD to your handler
CMD ["handler.handler.lambda_handler"]
```

### 2. Create requirements.txt

**File:** `infra/upload_images/container/requirements.txt`

```txt
# Core dependencies
boto3>=1.34.0
chromadb>=0.4.22
openai>=1.12.0
google-maps-services>=4.10.0
Pillow>=10.0.0

# Additional dependencies
pydantic>=2.0.0
numpy>=1.24.0
requests>=2.31.0
```

### 3. Create Modular Handler

**File:** `infra/upload_images/container/handler/handler.py`

```python
"""
Container-based Lambda handler for OCR processing with integrated merchant validation.

Combines:
1. OCR parsing and storage (from process_ocr_results.py)
2. Merchant validation (from embed_from_ndjson)
3. Embedding creation (from embed_from_ndjson)
"""

import json
import logging
import os
from typing import Any, Dict

from .ocr_processor import OCRProcessor
from .merchant_validator import MerchantValidator
from .embedding_creator import EmbeddingCreator

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Process OCR results with integrated merchant validation and embedding.
    
    Event format (SQS):
        {
            "Records": [{
                "body": "{\"job_id\": \"uuid\", \"image_id\": \"uuid\"}"
            }]
        }
    """
    logger.info(f"Processing {len(event.get('Records', []))} OCR records")
    
    results = []
    for record in event.get("Records", []):
        try:
            result = _process_single_record(record)
            results.append(result)
        except Exception as e:
            logger.error(f"Failed to process record: {e}", exc_info=True)
            results.append({"success": False, "error": str(e)})
    
    return {
        "statusCode": 200,
        "body": json.dumps({
            "message": "OCR results processed",
            "processed": len(results),
            "results": results
        })
    }


def _process_single_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """Process a single SQS record."""
    body = json.loads(record["body"])
    job_id = body["job_id"]
    image_id = body["image_id"]
    
    logger.info(f"Processing OCR for image {image_id}, job {job_id}")
    
    # Initialize processors
    ocr_processor = OCRProcessor(
        table_name=os.environ["DYNAMO_TABLE_NAME"],
        raw_bucket=os.environ["RAW_BUCKET"],
        site_bucket=os.environ["SITE_BUCKET"],
        artifacts_bucket=os.environ["ARTIFACTS_BUCKET"],
    )
    
    # Step 1: Process OCR (parse, classify, store in DynamoDB)
    ocr_result = ocr_processor.process_ocr_job(image_id, job_id)
    
    if not ocr_result.get("success"):
        return ocr_result
    
    # Step 2: Validate merchant and create embeddings (only for NATIVE receipts)
    if ocr_result.get("image_type") == "NATIVE":
        receipt_id = ocr_result.get("receipt_id", 1)
        
        try:
            # Initialize merchant validator
            merchant_validator = MerchantValidator(
                table_name=os.environ["DYNAMO_TABLE_NAME"],
                chroma_http_endpoint=os.environ.get("CHROMA_HTTP_ENDPOINT"),
                google_places_api_key=os.environ.get("GOOGLE_PLACES_API_KEY"),
                openai_api_key=os.environ.get("OPENAI_API_KEY"),
            )
            
            # Validate merchant and create metadata
            merchant_result = merchant_validator.validate_and_create_metadata(
                image_id=image_id,
                receipt_id=receipt_id,
            )
            
            merchant_name = merchant_result.get("merchant_name")
            logger.info(f"Merchant validated: {merchant_name}")
            
            # Initialize embedding creator
            embedding_creator = EmbeddingCreator(
                table_name=os.environ["DYNAMO_TABLE_NAME"],
                chromadb_bucket=os.environ["CHROMADB_BUCKET"],
                openai_api_key=os.environ.get("OPENAI_API_KEY"),
            )
            
            # Create embeddings with merchant context
            embedding_result = embedding_creator.create_embeddings(
                image_id=image_id,
                receipt_id=receipt_id,
                merchant_name=merchant_name,
            )
            
            return {
                "success": True,
                "image_id": image_id,
                "receipt_id": receipt_id,
                "image_type": ocr_result.get("image_type"),
                "merchant_name": merchant_name,
                "run_id": embedding_result.get("run_id"),
                "embeddings_created": True,
            }
            
        except Exception as e:
            logger.error(f"Merchant validation/embedding failed: {e}", exc_info=True)
            # Don't fail the whole job - OCR data is still stored
            return {
                "success": True,
                "image_id": image_id,
                "receipt_id": receipt_id,
                "image_type": ocr_result.get("image_type"),
                "embeddings_created": False,
                "embedding_error": str(e),
            }
    
    return ocr_result
```

### 4. Create OCR Processor Module

**File:** `infra/upload_images/container/handler/ocr_processor.py`

```python
"""
OCR processing logic extracted from process_ocr_results.py
"""

import json
import logging
from pathlib import Path
from typing import Dict, Any

import boto3
from PIL import Image as PIL_Image

from receipt_dynamo import DynamoClient
from receipt_dynamo.constants import ImageType
from receipt_upload.ocr import process_ocr_dict_as_image
from receipt_upload.receipt_processing.native import process_native
from receipt_upload.receipt_processing.photo import process_photo
from receipt_upload.receipt_processing.scan import process_scan
from receipt_upload.route_images import classify_image_layout
from receipt_upload.utils import (
    download_file_from_s3,
    download_image_from_s3,
    get_ocr_job,
    get_ocr_routing_decision,
)

logger = logging.getLogger(__name__)


class OCRProcessor:
    """Handles OCR parsing and storage."""
    
    def __init__(self, table_name: str, raw_bucket: str, site_bucket: str, artifacts_bucket: str):
        self.table_name = table_name
        self.raw_bucket = raw_bucket
        self.site_bucket = site_bucket
        self.artifacts_bucket = artifacts_bucket
        self.dynamo = DynamoClient(table_name)
        self.s3 = boto3.client("s3")
    
    def process_ocr_job(self, image_id: str, job_id: str) -> Dict[str, Any]:
        """
        Process an OCR job: download, parse, classify, and store.
        
        Returns:
            Dict with success status, image_type, and receipt_id
        """
        try:
            # Get job and routing decision
            ocr_job = get_ocr_job(self.table_name, image_id, job_id)
            ocr_routing_decision = get_ocr_routing_decision(self.table_name, image_id, job_id)
            
            # Download and parse OCR JSON
            ocr_json_path = download_file_from_s3(
                ocr_routing_decision.s3_bucket,
                ocr_routing_decision.s3_key,
                Path("/tmp"),
            )
            
            with open(ocr_json_path, "r", encoding="utf-8") as f:
                ocr_json = json.load(f)
            
            ocr_lines, ocr_words, ocr_letters = process_ocr_dict_as_image(ocr_json, image_id)
            
            # Download image
            raw_image_path = download_image_from_s3(
                ocr_job.s3_bucket, ocr_job.s3_key, image_id
            )
            image = PIL_Image.open(raw_image_path)
            
            # Classify image type
            image_type = classify_image_layout(
                lines=ocr_lines,
                image_height=image.height,
                image_width=image.width,
            )
            
            logger.info(f"Image {image_id} classified as {image_type}")
            
            # Process based on type
            if image_type == ImageType.NATIVE:
                process_native(
                    raw_bucket=self.raw_bucket,
                    site_bucket=self.site_bucket,
                    dynamo_table_name=self.table_name,
                    ocr_job_queue_url=os.environ["OCR_JOB_QUEUE_URL"],
                    image=image,
                    lines=ocr_lines,
                    words=ocr_words,
                    letters=ocr_letters,
                    ocr_routing_decision=ocr_routing_decision,
                    ocr_job=ocr_job,
                )
                return {
                    "success": True,
                    "image_id": image_id,
                    "image_type": "NATIVE",
                    "receipt_id": 1,
                }
            
            elif image_type == ImageType.PHOTO:
                process_photo(
                    raw_bucket=self.raw_bucket,
                    site_bucket=self.site_bucket,
                    dynamo_table_name=self.table_name,
                    ocr_job_queue_url=os.environ["OCR_JOB_QUEUE_URL"],
                    ocr_routing_decision=ocr_routing_decision,
                    ocr_job=ocr_job,
                    image=image,
                )
                return {
                    "success": True,
                    "image_id": image_id,
                    "image_type": "PHOTO",
                    "receipt_id": None,  # Multiple receipts
                }
            
            elif image_type == ImageType.SCAN:
                process_scan(
                    raw_bucket=self.raw_bucket,
                    site_bucket=self.site_bucket,
                    dynamo_table_name=self.table_name,
                    ocr_job_queue_url=os.environ["OCR_JOB_QUEUE_URL"],
                    ocr_routing_decision=ocr_routing_decision,
                    ocr_job=ocr_job,
                    image=image,
                )
                return {
                    "success": True,
                    "image_id": image_id,
                    "image_type": "SCAN",
                    "receipt_id": None,  # Multiple receipts
                }
            
            else:
                logger.error(f"Unknown image type: {image_type}")
                return {
                    "success": False,
                    "error": f"Unknown image type: {image_type}",
                }
        
        except Exception as e:
            logger.error(f"OCR processing failed: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
            }
```

### 5. Create Merchant Validator Module

**File:** `infra/upload_images/container/handler/merchant_validator.py`

```python
"""
Merchant validation logic extracted from embed_from_ndjson.
"""

import logging
import os
from typing import Dict, Any, Optional

from receipt_dynamo import DynamoClient
from receipt_label.data.places_api import PlacesAPI
from receipt_label.merchant_resolution.resolver import resolve_receipt
from receipt_label.vector_store import VectorClient

logger = logging.getLogger(__name__)


class MerchantValidator:
    """Handles merchant validation using ChromaDB and Google Places."""
    
    def __init__(
        self,
        table_name: str,
        chroma_http_endpoint: Optional[str],
        google_places_api_key: Optional[str],
        openai_api_key: Optional[str],
    ):
        self.dynamo = DynamoClient(table_name)
        self.places_api = PlacesAPI(api_key=google_places_api_key) if google_places_api_key else None
        self.openai_api_key = openai_api_key
        
        # Initialize ChromaDB client (HTTP for now, could be EFS later)
        self.chroma_client = None
        if chroma_http_endpoint:
            try:
                self.chroma_client = VectorClient.create_chromadb_client(
                    mode="read",
                    http_url=chroma_http_endpoint
                )
                logger.info(f"ChromaDB client initialized: {chroma_http_endpoint}")
            except Exception as e:
                logger.warning(f"Failed to initialize ChromaDB client: {e}")
    
    def validate_and_create_metadata(
        self,
        image_id: str,
        receipt_id: int,
    ) -> Dict[str, Any]:
        """
        Validate merchant and create ReceiptMetadata.
        
        Returns:
            Dict with merchant_name and validation details
        """
        try:
            # Create embedding function
            def _embed_texts(texts):
                if not texts or not self.openai_api_key:
                    return [[0.0] * 1536 for _ in texts]
                
                from receipt_label.utils import get_client_manager
                openai_client = get_client_manager().openai
                resp = openai_client.embeddings.create(
                    model="text-embedding-3-small",
                    input=list(texts)
                )
                return [d.embedding for d in resp.data]
            
            # Resolve merchant
            resolution = resolve_receipt(
                key=(image_id, receipt_id),
                dynamo=self.dynamo,
                places_api=self.places_api,
                chroma_line_client=self.chroma_client,
                embed_fn=_embed_texts,
                write_metadata=True,  # Creates ReceiptMetadata in DynamoDB
            )
            
            decision = resolution.get("decision") or {}
            best = decision.get("best") or {}
            merchant_name = best.get("name") or best.get("merchant_name")
            
            return {
                "success": True,
                "merchant_name": merchant_name,
                "source": best.get("source"),
                "score": best.get("score"),
                "place_id": best.get("place_id"),
                "wrote_metadata": bool(resolution.get("wrote_metadata")),
            }
        
        except Exception as e:
            logger.error(f"Merchant validation failed: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "merchant_name": None,
            }
```

### 6. Create Embedding Creator Module

**File:** `infra/upload_images/container/handler/embedding_creator.py`

```python
"""
Embedding creation logic extracted from embed_from_ndjson.
"""

import logging
import os
import tempfile
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, Optional

import boto3

from receipt_dynamo import DynamoClient
from receipt_label.vector_store import VectorClient
from receipt_label.embeddings import (
    embed_lines_realtime,
    embed_words_realtime,
    upsert_embeddings,
)

logger = logging.getLogger(__name__)


class EmbeddingCreator:
    """Handles embedding creation and ChromaDB delta management."""
    
    def __init__(self, table_name: str, chromadb_bucket: str, openai_api_key: Optional[str]):
        self.dynamo = DynamoClient(table_name)
        self.chromadb_bucket = chromadb_bucket
        self.openai_api_key = openai_api_key
        self.s3 = boto3.client("s3")
    
    def create_embeddings(
        self,
        image_id: str,
        receipt_id: int,
        merchant_name: Optional[str],
    ) -> Dict[str, Any]:
        """
        Create embeddings and write ChromaDB deltas to S3.
        
        Returns:
            Dict with run_id and success status
        """
        try:
            # Fetch lines and words from DynamoDB
            receipt_lines, _ = self.dynamo.list_receipt_lines(image_id, receipt_id, limit=1000)
            receipt_words, _ = self.dynamo.list_receipt_words(image_id, receipt_id, limit=10000)
            
            logger.info(f"Creating embeddings for {len(receipt_lines)} lines, {len(receipt_words)} words")
            
            # Generate run ID
            run_id = str(uuid.uuid4())
            
            # Create local ChromaDB deltas
            delta_lines_dir = os.path.join(tempfile.gettempdir(), f"lines_{run_id}")
            delta_words_dir = os.path.join(tempfile.gettempdir(), f"words_{run_id}")
            
            line_client = VectorClient.create_chromadb_client(
                persist_directory=delta_lines_dir,
                mode="delta",
                metadata_only=True
            )
            word_client = VectorClient.create_chromadb_client(
                persist_directory=delta_words_dir,
                mode="delta",
                metadata_only=True
            )
            
            # Upsert embeddings with merchant context
            upsert_embeddings(
                line_client=line_client,
                word_client=word_client,
                line_embed_fn=embed_lines_realtime,
                word_embed_fn=embed_words_realtime,
                lines=receipt_lines,
                words=receipt_words,
                image_id=image_id,
                receipt_id=receipt_id,
                merchant_name=merchant_name,  # ← Included in metadata!
            )
            
            # Upload deltas to S3
            self._upload_directory_to_s3(
                delta_lines_dir,
                f"deltas/{run_id}/lines/"
            )
            self._upload_directory_to_s3(
                delta_words_dir,
                f"deltas/{run_id}/words/"
            )
            
            # Update COMPACTION_RUN to COMPLETED
            self.dynamo.update_compaction_run(
                image_id=image_id,
                receipt_id=receipt_id,
                run_id=run_id,
                lines_state="COMPLETED",
                words_state="COMPLETED",
                lines_finished_at=datetime.now(timezone.utc),
                words_finished_at=datetime.now(timezone.utc),
            )
            
            logger.info(f"Embeddings created successfully: run_id={run_id}")
            
            return {
                "success": True,
                "run_id": run_id,
                "lines_count": len(receipt_lines),
                "words_count": len(receipt_words),
            }
        
        except Exception as e:
            logger.error(f"Embedding creation failed: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
            }
    
    def _upload_directory_to_s3(self, local_dir: str, s3_prefix: str):
        """Upload a directory to S3."""
        import os
        for root, dirs, files in os.walk(local_dir):
            for file in files:
                local_path = os.path.join(root, file)
                relative_path = os.path.relpath(local_path, local_dir)
                s3_key = os.path.join(s3_prefix, relative_path)
                
                self.s3.upload_file(local_path, self.chromadb_bucket, s3_key)
                logger.debug(f"Uploaded {local_path} to s3://{self.chromadb_bucket}/{s3_key}")
```

---

## Infrastructure Changes

### Update `infra/upload_images/infra.py`

Replace the zip-based `process_ocr_results` Lambda with a container-based one:

```python
# Create CodeBuild project for process_ocr_results container
process_ocr_docker_image = CodeBuildDockerImage(
    f"{name}-process-ocr-container",
    dockerfile_path="infra/upload_images/container/Dockerfile",
    build_context_path=".",  # Root of repo
    ecr_repository_name=f"{name}-{stack}-process-ocr-results",
    lambda_function_name=f"{name}-{stack}-process-ocr-results",
    lambda_role_arn=process_ocr_role.arn,
    lambda_timeout=600,  # 10 minutes
    lambda_memory_size=2048,
    lambda_environment_variables={
        "DYNAMO_TABLE_NAME": dynamodb_table.name,
        "S3_BUCKET": image_bucket.bucket,
        "RAW_BUCKET": raw_bucket.bucket,
        "SITE_BUCKET": site_bucket.bucket,
        "ARTIFACTS_BUCKET": artifacts_bucket.bucket,
        "OCR_JOB_QUEUE_URL": self.ocr_queue.url,
        "OCR_RESULTS_QUEUE_URL": self.ocr_results_queue.url,
        "CHROMADB_BUCKET": chromadb_bucket_name,
        "CHROMA_HTTP_ENDPOINT": chroma_http_endpoint,
        "GOOGLE_PLACES_API_KEY": google_places_api_key,
        "OPENAI_API_KEY": openai_api_key,
    },
    opts=ResourceOptions(parent=self),
)

process_ocr_lambda = process_ocr_docker_image.lambda_function
```

---

## Deployment Steps

### 1. Create the Container Structure

```bash
cd /Users/tnorlund/GitHub/example/infra/upload_images
mkdir -p container/handler
touch container/Dockerfile
touch container/requirements.txt
touch container/handler/__init__.py
touch container/handler/handler.py
touch container/handler/ocr_processor.py
touch container/handler/merchant_validator.py
touch container/handler/embedding_creator.py
```

### 2. Copy Code from Existing Files

- Copy OCR logic from `process_ocr_results.py` → `ocr_processor.py`
- Copy merchant validation from `embed_from_ndjson/handler.py` → `merchant_validator.py`
- Copy embedding logic from `embed_from_ndjson/handler.py` → `embedding_creator.py`

### 3. Update Infrastructure

```bash
cd /Users/tnorlund/GitHub/example/infra
# Update upload_images/infra.py to use container instead of zip
```

### 4. Deploy

```bash
cd /Users/tnorlund/GitHub/example/infra
pulumi up
```

The CodeBuild project will:
1. Build the Docker image
2. Push to ECR
3. Update the Lambda function

### 5. Test

```bash
# Run Mac OCR script
cd /Users/tnorlund/GitHub/example/receipt_ocr_swift
swift run ReceiptOCRCLI /path/to/images/*.jpg

# Watch logs
aws logs tail /aws/lambda/process-ocr-results-dev --follow
```

---

## Benefits of Container-Based Approach

### 1. Easier Dependency Management
- ✅ No Lambda layers needed
- ✅ All dependencies in requirements.txt
- ✅ Consistent with other container Lambdas

### 2. Larger Package Size
- ✅ 10GB vs 250MB limit
- ✅ Can include larger libraries (ChromaDB, NumPy, etc.)

### 3. Better Testing
- ✅ Can test locally with Docker
- ✅ Same environment as production

### 4. Modular Code
- ✅ Separated concerns (OCR, merchant, embedding)
- ✅ Easier to maintain and update
- ✅ Reusable components

### 5. Simplified Architecture
- ✅ One less Lambda
- ✅ One less SQS queue
- ✅ Faster end-to-end processing

---

## Cost Comparison

### Current (3 Lambdas)
| Lambda | Memory | Duration | Cost per Invocation |
|--------|--------|----------|---------------------|
| process_ocr_results | 1024MB | 10s | $0.0002 |
| embed_from_ndjson | 2048MB | 30s | $0.0010 |
| enhanced_compaction | 3008MB | 10s | $0.0005 |
| **Total** | | **50s** | **$0.0017** |

### Proposed (2 Lambdas)
| Lambda | Memory | Duration | Cost per Invocation |
|--------|--------|----------|---------------------|
| process_ocr_results | 2048MB | 40s | $0.0013 |
| enhanced_compaction | 3008MB | 10s | $0.0005 |
| **Total** | | **50s** | **$0.0018** |

**Cost difference:** ~$0.0001 per invocation (6% increase)

**But:**
- ✅ Faster (no SQS delay)
- ✅ Simpler (one less Lambda)
- ✅ More maintainable

---

## Rollback Plan

If issues arise:

1. **Keep both versions deployed**
   - Old: `process-ocr-results-zip`
   - New: `process-ocr-results-container`

2. **Use environment variable to switch**
   ```python
   USE_CONTAINER_VERSION = os.environ.get("USE_CONTAINER_VERSION", "false")
   ```

3. **Gradual rollout**
   - Test with 10% of traffic
   - Monitor errors and performance
   - Increase to 100% if successful

---

## Timeline

- **Day 1**: Create container structure and Dockerfile
- **Day 2**: Extract and modularize code
- **Day 3**: Update infrastructure and deploy
- **Day 4**: Test and monitor
- **Day 5**: Full rollout

**Total: 5 days**

---

## Next Steps

1. ✅ Review this plan
2. ✅ Create container structure
3. ✅ Extract modular code
4. ✅ Update infrastructure
5. ✅ Deploy and test
6. ✅ Monitor and optimize

**Ready to implement!** 🚀

