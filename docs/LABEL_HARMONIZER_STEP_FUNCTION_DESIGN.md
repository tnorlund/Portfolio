# Label Harmonizer Step Function Design

## Overview

This document outlines the Step Function architecture for running label harmonization in parallel across all 16 CORE_LABELS, with efficient data preparation and batch processing.

## Architecture

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                         Label Harmonizer Step Function                        │
└──────────────────────────────────────────────────────────────────────────────┘
                                      │
                    ┌─────────────────┼─────────────────┐
                    ▼                 ▼                 ▼
    ┌─────────────────────┐  ┌─────────────────────┐  ...  (16 total)
    │ PrepareLabels       │  │ PrepareLabels       │
    │ (ZIP Lambda)        │  │ (ZIP Lambda)        │
    │ MERCHANT_NAME       │  │ GRAND_TOTAL         │
    └─────────┬───────────┘  └─────────┬───────────┘
              │                        │
              │  S3: batch files       │
              ▼                        ▼
    ┌─────────────────────┐  ┌─────────────────────┐
    │ Batch N=4           │  │ Batch N=4           │
    │ HarmonizeLabels     │  │ HarmonizeLabels     │
    │ (Container Lambda)  │  │ (Container Lambda)  │
    └─────────────────────┘  └─────────────────────┘
```

## CORE_LABELS (16 total)

```python
CORE_LABELS = [
    "MERCHANT_NAME", "STORE_HOURS", "PHONE_NUMBER", "WEBSITE",
    "LOYALTY_ID", "ADDRESS_LINE", "DATE", "TIME",
    "PAYMENT_METHOD", "COUPON", "DISCOUNT", "PRODUCT_NAME",
    "QUANTITY", "UNIT_PRICE", "LINE_TOTAL", "SUBTOTAL",
    "TAX", "GRAND_TOTAL"
]
```

---

## Step 1: Prepare Labels (Zip-based Lambda)

### Purpose
Fast, lightweight Lambda that queries DynamoDB and prepares data for the heavier processing step. Only needs the DynamoDB layer.

### Input
```json
{
  "label_type": "GRAND_TOTAL",
  "batch_size": 1000,
  "max_merchants": null
}
```

### Actions
1. **Query DynamoDB** using GSI1 (`get_receipt_word_labels_by_label`)
2. **Batch fetch** merchant names and word text
3. **Group by merchant** (in-memory for small datasets, streamed for large)
4. **Serialize to NDJSON** format
5. **Upload to S3** with structured path

### Output
```json
{
  "label_type": "GRAND_TOTAL",
  "s3_batch_path": "s3://harmonizer-batch-bucket/batches/2024-01-15/GRAND_TOTAL/",
  "merchant_groups": [
    {
      "merchant_name": "Sprouts Farmers Market",
      "batch_file": "sprouts-farmers-market.ndjson",
      "label_count": 520
    },
    {
      "merchant_name": "Walmart",
      "batch_file": "walmart.ndjson",
      "label_count": 1234
    }
  ],
  "total_labels": 5847,
  "total_merchants": 23
}
```

### S3 Structure
```
harmonizer-batch-bucket/
├── batches/
│   └── 2024-01-15T12:00:00Z/
│       ├── GRAND_TOTAL/
│       │   ├── metadata.json           # Execution metadata
│       │   ├── sprouts-farmers-market.ndjson
│       │   ├── walmart.ndjson
│       │   └── ...
│       ├── MERCHANT_NAME/
│       │   └── ...
│       └── ...
└── chromadb/
    └── words/
        └── snapshot.tar.gz             # Cached ChromaDB snapshot
```

### NDJSON Format
```jsonl
{"image_id":"abc123","receipt_id":1,"line_id":5,"word_id":2,"label":"GRAND_TOTAL","validation_status":"VALID","word_text":"$42.99"}
{"image_id":"abc123","receipt_id":1,"line_id":6,"word_id":1,"label":"GRAND_TOTAL","validation_status":"PENDING","word_text":"$42.99"}
```

### Lambda Configuration
```python
{
    "runtime": "python3.12",
    "architecture": "arm64",
    "memory_size": 512,
    "timeout": 300,  # 5 minutes
    "layers": [dynamo_layer.arn],
    "environment": {
        "DYNAMODB_TABLE_NAME": table_name,
        "BATCH_BUCKET": bucket_name,
    }
}
```

---

## Step 2: Harmonize Labels (Container-based Lambda)

### Purpose
Heavy-lifting Lambda with ChromaDB, embeddings, and LLM for semantic analysis. Uses CodeBuildDockerImage for container deployment.

### Input
```json
{
  "label_type": "GRAND_TOTAL",
  "s3_batch_path": "s3://harmonizer-batch-bucket/batches/2024-01-15/GRAND_TOTAL/",
  "merchant_group": {
    "merchant_name": "Sprouts Farmers Market",
    "batch_file": "sprouts-farmers-market.ndjson",
    "label_count": 520
  },
  "dry_run": true,
  "similarity_threshold": 0.70
}
```

### Actions
1. **Download ChromaDB snapshot** from S3 (cached in /tmp if already present)
2. **Stream NDJSON** from S3 batch file
3. **Initialize clients**: ChromaDB, OpenAI embeddings, Ollama LLM
4. **Run harmonization**:
   - Find similar words using ChromaDB
   - Compute consensus for VALID labels
   - Use LLM to identify semantic outliers
5. **Generate results** (outliers, conflicts, patterns)
6. **Upload results** to S3

### Output
```json
{
  "label_type": "GRAND_TOTAL",
  "merchant_name": "Sprouts Farmers Market",
  "status": "completed",
  "results": {
    "labels_processed": 520,
    "outliers_found": 15,
    "conflicts_found": 3,
    "processing_time_seconds": 45.2
  },
  "results_path": "s3://harmonizer-batch-bucket/results/2024-01-15/GRAND_TOTAL/sprouts-farmers-market.json",
  "changes": {
    "would_invalidate": 12,
    "would_validate": 3,
    "notes": "Dry run - no changes applied"
  }
}
```

### Lambda Configuration
```python
{
    "architecture": "arm64",
    "memory_size": 3072,  # 3 GB for ChromaDB + LLM
    "timeout": 900,       # 15 minutes per merchant group
    "ephemeral_storage": 2048,  # 2 GB /tmp for ChromaDB cache
    "environment": {
        "DYNAMODB_TABLE_NAME": table_name,
        "CHROMADB_BUCKET": chromadb_bucket,
        "OPENAI_API_KEY": openai_api_key,
        "OLLAMA_API_KEY": ollama_api_key,
    }
}
```

---

## Step Function Definition

### State Machine Diagram

```
┌──────────────┐
│  StartAt:    │
│  Initialize  │
└──────┬───────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────┐
│  ParallelPrepare (Type: Map, MaxConcurrency: 16)             │
│  ┌──────────────────────────────────────────────────────┐    │
│  │  PrepareLabels (ZIP Lambda)                          │    │
│  │  - Query DynamoDB for label_type                     │    │
│  │  - Group by merchant                                 │    │
│  │  - Upload to S3                                      │    │
│  └──────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────┐
│  FlattenMerchantGroups (Type: Pass)                          │
│  - Combine all merchant groups from all label types          │
│  - Create unified work items array                           │
└──────────────────────────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────┐
│  ProcessInBatches (Type: Map, MaxConcurrency: 4)             │
│  ┌──────────────────────────────────────────────────────┐    │
│  │  HarmonizeLabels (Container Lambda)                  │    │
│  │  - Download ChromaDB snapshot (cached)               │    │
│  │  - Process merchant group                            │    │
│  │  - Identify outliers using LLM                       │    │
│  │  - Upload results to S3                              │    │
│  └──────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────┐
│  AggregateResults (Type: Task)                               │
│  - Combine all results                                       │
│  - Generate summary report                                   │
│  - Optionally trigger DynamoDB updates                       │
└──────────────────────────────────────────────────────────────┘
       │
       ▼
┌──────────────┐
│    Done      │
└──────────────┘
```

### State Machine Definition (ASL)

```json
{
  "Comment": "Label Harmonizer - Parallel processing with batch control",
  "StartAt": "Initialize",
  "States": {
    "Initialize": {
      "Type": "Pass",
      "Parameters": {
        "execution_id.$": "$$.Execution.Id",
        "start_time.$": "$$.Execution.StartTime",
        "dry_run.$": "$.dry_run",
        "max_concurrency_prepare": 16,
        "max_concurrency_process": 4,
        "label_types": [
          "MERCHANT_NAME", "STORE_HOURS", "PHONE_NUMBER", "WEBSITE",
          "LOYALTY_ID", "ADDRESS_LINE", "DATE", "TIME",
          "PAYMENT_METHOD", "COUPON", "DISCOUNT", "PRODUCT_NAME",
          "QUANTITY", "UNIT_PRICE", "LINE_TOTAL", "SUBTOTAL",
          "TAX", "GRAND_TOTAL"
        ]
      },
      "Next": "PrepareChromaDB"
    },

    "PrepareChromaDB": {
      "Type": "Task",
      "Resource": "${prepare_chromadb_lambda_arn}",
      "Comment": "Download and cache ChromaDB snapshot to S3 for worker reuse",
      "TimeoutSeconds": 300,
      "Retry": [
        {
          "ErrorEquals": ["States.TaskFailed", "States.Timeout"],
          "IntervalSeconds": 5,
          "MaxAttempts": 2,
          "BackoffRate": 2.0
        }
      ],
      "ResultPath": "$.chromadb_cache",
      "Next": "ParallelPrepare"
    },

    "ParallelPrepare": {
      "Type": "Map",
      "ItemsPath": "$.label_types",
      "MaxConcurrency": 16,
      "Parameters": {
        "label_type.$": "$$.Map.Item.Value",
        "execution_id.$": "$.execution_id",
        "batch_bucket.$": "$.batch_bucket"
      },
      "Iterator": {
        "StartAt": "PrepareLabels",
        "States": {
          "PrepareLabels": {
            "Type": "Task",
            "Resource": "${prepare_labels_lambda_arn}",
            "TimeoutSeconds": 300,
            "Retry": [
              {
                "ErrorEquals": ["States.TaskFailed", "Lambda.ServiceException"],
                "IntervalSeconds": 2,
                "MaxAttempts": 3,
                "BackoffRate": 2.0
              }
            ],
            "End": true
          }
        }
      },
      "ResultPath": "$.prepare_results",
      "Next": "FlattenMerchantGroups"
    },

    "FlattenMerchantGroups": {
      "Type": "Pass",
      "Parameters": {
        "work_items.$": "$.prepare_results[*].merchant_groups[*]",
        "execution_id.$": "$.execution_id",
        "dry_run.$": "$.dry_run",
        "chromadb_cache.$": "$.chromadb_cache"
      },
      "Next": "ProcessInBatches"
    },

    "ProcessInBatches": {
      "Type": "Map",
      "ItemsPath": "$.work_items",
      "MaxConcurrency": 4,
      "Parameters": {
        "merchant_group.$": "$$.Map.Item.Value",
        "execution_id.$": "$.execution_id",
        "dry_run.$": "$.dry_run",
        "chromadb_cache.$": "$.chromadb_cache"
      },
      "Iterator": {
        "StartAt": "HarmonizeLabels",
        "States": {
          "HarmonizeLabels": {
            "Type": "Task",
            "Resource": "${harmonize_labels_lambda_arn}",
            "TimeoutSeconds": 900,
            "Retry": [
              {
                "ErrorEquals": ["States.TaskFailed", "Lambda.ServiceException"],
                "IntervalSeconds": 5,
                "MaxAttempts": 2,
                "BackoffRate": 2.0
              },
              {
                "ErrorEquals": ["OllamaRateLimitError"],
                "IntervalSeconds": 30,
                "MaxAttempts": 5,
                "BackoffRate": 1.5
              }
            ],
            "End": true
          }
        }
      },
      "ResultPath": "$.process_results",
      "Next": "AggregateResults"
    },

    "AggregateResults": {
      "Type": "Task",
      "Resource": "${aggregate_results_lambda_arn}",
      "TimeoutSeconds": 120,
      "ResultPath": "$.summary",
      "Next": "Done"
    },

    "Done": {
      "Type": "Pass",
      "End": true
    }
  }
}
```

---

## Lambda Handler Implementations

### 1. Prepare Labels Handler (Zip Lambda)

```python
# infra/label_harmonizer_step_functions/handlers/prepare_labels.py

import json
import os
from datetime import datetime
from typing import Any, Dict

import boto3
from receipt_dynamo import DynamoClient

s3 = boto3.client('s3')

def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Prepare labels for a single label type.

    1. Query DynamoDB for all labels of this type
    2. Batch fetch merchant names and word text
    3. Group by merchant
    4. Upload to S3 as NDJSON files
    """
    label_type = event['label_type']
    execution_id = event['execution_id']
    batch_bucket = os.environ['BATCH_BUCKET']
    table_name = os.environ['DYNAMODB_TABLE_NAME']

    dynamo = DynamoClient(table_name)

    # Prepare S3 path
    timestamp = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
    s3_prefix = f"batches/{execution_id}/{label_type}/"

    # Stream labels from DynamoDB
    merchant_groups = {}
    labels_processed = 0

    # Use pagination to handle large datasets
    for label in dynamo.get_receipt_word_labels_by_label(label_type):
        labels_processed += 1

        # Get merchant name (batch this for efficiency)
        metadata = dynamo.get_receipt_metadata(
            label['image_id'],
            label['receipt_id']
        )
        merchant_name = metadata.merchant_name if metadata else "Unknown"

        # Get word text
        word = dynamo.get_receipt_word(
            label['image_id'],
            label['receipt_id'],
            label['line_id'],
            label['word_id']
        )
        word_text = word.text if word else ""

        # Group by merchant
        if merchant_name not in merchant_groups:
            merchant_groups[merchant_name] = []

        merchant_groups[merchant_name].append({
            'image_id': label['image_id'],
            'receipt_id': label['receipt_id'],
            'line_id': label['line_id'],
            'word_id': label['word_id'],
            'label': label['label'],
            'validation_status': label.get('validation_status', 'PENDING'),
            'word_text': word_text,
            'merchant_name': merchant_name,
        })

    # Upload each merchant group as NDJSON
    output_groups = []
    for merchant_name, labels in merchant_groups.items():
        safe_name = merchant_name.lower().replace(' ', '-').replace('/', '-')
        file_key = f"{s3_prefix}{safe_name}.ndjson"

        # Convert to NDJSON
        ndjson_content = '\n'.join(json.dumps(label) for label in labels)

        # Upload to S3
        s3.put_object(
            Bucket=batch_bucket,
            Key=file_key,
            Body=ndjson_content.encode('utf-8'),
            ContentType='application/x-ndjson'
        )

        output_groups.append({
            'merchant_name': merchant_name,
            'batch_file': file_key,
            'label_count': len(labels),
            'label_type': label_type,
        })

    return {
        'label_type': label_type,
        's3_batch_path': f"s3://{batch_bucket}/{s3_prefix}",
        'merchant_groups': output_groups,
        'total_labels': labels_processed,
        'total_merchants': len(merchant_groups),
    }
```

### 2. Harmonize Labels Handler (Container Lambda)

```python
# infra/label_harmonizer_step_functions/lambdas/harmonize_labels.py

import asyncio
import json
import os
import tempfile
from typing import Any, Dict

import boto3

from receipt_agent.tools.label_harmonizer import LabelHarmonizer, LabelRecord
from receipt_chroma import ChromaClient
from receipt_dynamo import DynamoClient

s3 = boto3.client('s3')

def download_chromadb_snapshot(bucket: str, cache_path: str) -> str:
    """Download ChromaDB snapshot from S3 if not cached."""
    if os.path.exists(os.path.join(cache_path, 'chroma.sqlite3')):
        return cache_path

    # Download from S3
    pointer_key = "words/snapshot/latest-pointer.txt"
    response = s3.get_object(Bucket=bucket, Key=pointer_key)
    timestamp = response['Body'].read().decode().strip()

    prefix = f"words/snapshot/timestamped/{timestamp}/"
    paginator = s3.get_paginator('list_objects_v2')

    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get('Contents', []):
            key = obj['Key']
            relative_path = key[len(prefix):]
            if not relative_path:
                continue
            local_path = os.path.join(cache_path, relative_path)
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            s3.download_file(bucket, key, local_path)

    return cache_path


async def process_merchant_group(
    harmonizer: LabelHarmonizer,
    labels: list[LabelRecord],
    merchant_name: str,
    label_type: str,
    dry_run: bool = True,
) -> Dict[str, Any]:
    """Process a single merchant group."""
    from receipt_agent.tools.label_harmonizer import MerchantLabelGroup

    # Create merchant group
    group = MerchantLabelGroup(
        merchant_name=merchant_name,
        label_type=label_type,
        labels=labels,
    )

    # Run analysis
    result = await harmonizer.analyze_group(group)

    # Prepare results
    return {
        'merchant_name': merchant_name,
        'label_type': label_type,
        'labels_processed': len(labels),
        'outliers_found': len(result.outliers),
        'outlier_details': [
            {
                'word_text': o.word_text,
                'validation_status': o.validation_status,
                'image_id': o.image_id,
                'receipt_id': o.receipt_id,
            }
            for o in result.outliers
        ],
        'consensus': result.consensus_label,
    }


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Harmonize labels for a single merchant group.

    1. Download ChromaDB snapshot (cached)
    2. Stream labels from S3 NDJSON file
    3. Run harmonization with LLM-based outlier detection
    4. Upload results to S3
    """
    merchant_group = event['merchant_group']
    execution_id = event['execution_id']
    dry_run = event.get('dry_run', True)

    batch_bucket = os.environ['BATCH_BUCKET']
    chromadb_bucket = os.environ['CHROMADB_BUCKET']
    table_name = os.environ['DYNAMODB_TABLE_NAME']

    # Setup ChromaDB (cached in /tmp)
    chroma_path = '/tmp/chromadb'
    download_chromadb_snapshot(chromadb_bucket, chroma_path)

    # Initialize clients
    dynamo = DynamoClient(table_name)
    chroma = ChromaClient(persist_directory=chroma_path, mode='read')

    # Create embedding function
    from langchain_openai import OpenAIEmbeddings
    embed_fn = OpenAIEmbeddings(model="text-embedding-3-small")

    # Create harmonizer
    harmonizer = LabelHarmonizer(
        dynamo_client=dynamo,
        chroma_client=chroma,
        embed_fn=embed_fn,
    )

    # Download labels from S3
    batch_file = merchant_group['batch_file']
    response = s3.get_object(Bucket=batch_bucket, Key=batch_file)
    ndjson_content = response['Body'].read().decode('utf-8')

    # Parse NDJSON
    labels = []
    for line in ndjson_content.strip().split('\n'):
        if line:
            data = json.loads(line)
            labels.append(LabelRecord(
                image_id=data['image_id'],
                receipt_id=data['receipt_id'],
                line_id=data['line_id'],
                word_id=data['word_id'],
                label=data['label'],
                validation_status=data.get('validation_status'),
                merchant_name=data.get('merchant_name'),
                word_text=data.get('word_text'),
            ))

    # Run harmonization
    result = asyncio.run(process_merchant_group(
        harmonizer=harmonizer,
        labels=labels,
        merchant_name=merchant_group['merchant_name'],
        label_type=merchant_group['label_type'],
        dry_run=dry_run,
    ))

    # Upload results
    results_key = f"results/{execution_id}/{merchant_group['label_type']}/{merchant_group['merchant_name'].lower().replace(' ', '-')}.json"
    s3.put_object(
        Bucket=batch_bucket,
        Key=results_key,
        Body=json.dumps(result, indent=2).encode('utf-8'),
        ContentType='application/json'
    )

    return {
        'status': 'completed',
        'results_path': f"s3://{batch_bucket}/{results_key}",
        **result,
    }
```

---

## Infrastructure (Pulumi)

### Component Structure

```
infra/
└── label_harmonizer_step_functions/
    ├── __init__.py
    ├── infrastructure.py          # Main Pulumi component
    ├── handlers/
    │   ├── prepare_labels.py      # Zip Lambda handler
    │   ├── prepare_chromadb.py    # Pre-cache ChromaDB
    │   └── aggregate_results.py   # Aggregate all results
    └── lambdas/
        ├── Dockerfile             # Container Lambda image
        └── harmonize_labels.py    # Main harmonizer handler
```

### Main Infrastructure Component

```python
# infra/label_harmonizer_step_functions/infrastructure.py

class LabelHarmonizerStepFunction(ComponentResource):
    """
    Step Function infrastructure for label harmonization.

    Components:
    - Zip Lambda: prepare_labels (DynamoDB layer only)
    - Container Lambda: harmonize_labels (ChromaDB + LLM)
    - S3 Bucket: batch files and results
    - Step Function: orchestration
    """

    def __init__(
        self,
        name: str,
        *,
        dynamodb_table_name: pulumi.Input[str],
        dynamodb_table_arn: pulumi.Input[str],
        chromadb_bucket_name: pulumi.Input[str],
        max_concurrency_prepare: int = 16,
        max_concurrency_process: int = 4,
        opts: Optional[ResourceOptions] = None,
    ):
        # ... implementation similar to CreateLabelsStepFunction
```

---

## Configuration Options

### Environment Variables

| Variable | Description | Lambda |
|----------|-------------|--------|
| `DYNAMODB_TABLE_NAME` | DynamoDB table name | Both |
| `BATCH_BUCKET` | S3 bucket for batch files | Both |
| `CHROMADB_BUCKET` | S3 bucket with ChromaDB snapshots | Container |
| `OPENAI_API_KEY` | OpenAI API key for embeddings | Container |
| `OLLAMA_API_KEY` | Ollama API key for LLM | Container |

### Step Function Input

```json
{
  "dry_run": true,
  "similarity_threshold": 0.70,
  "max_merchants_per_label": null,
  "label_types": null  // null = all CORE_LABELS
}
```

---

## Performance Estimates

| Phase | Concurrency | Time per Unit | Total Items | Estimated Time |
|-------|-------------|---------------|-------------|----------------|
| Prepare | 16 parallel | 30s per label type | 16 | ~30s |
| Process | 4 parallel | 2-10min per merchant | ~100 merchants | ~50-100min |
| Aggregate | 1 | 30s | 1 | ~30s |

**Total estimated time**: 1-2 hours for full harmonization run

---

## Monitoring

### CloudWatch Metrics
- `LabelsPrepared` - Count per label type
- `OutliersFound` - Count per merchant
- `ProcessingTime` - Duration per merchant
- `LLMErrors` - Ollama/LLM failures

### Alarms
- Step Function failures
- Lambda timeout rate > 5%
- Ollama rate limit errors > 10/min

---

## Next Steps

1. [ ] Create `infra/label_harmonizer_step_functions/` directory structure
2. [ ] Implement `prepare_labels.py` handler (zip Lambda)
3. [ ] Create `Dockerfile` for container Lambda
4. [ ] Implement `harmonize_labels.py` handler
5. [ ] Create Pulumi `infrastructure.py` component
6. [ ] Add to main Pulumi stack
7. [ ] Test with single label type
8. [ ] Full integration test


